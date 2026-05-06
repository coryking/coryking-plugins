# Tool Annotations and Error Response Design

Sources: [MCP Spec: Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools), [Tool Annotations Blog](https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/), [FastMCP Tools](https://gofastmcp.com/servers/tools), [Anthropic: Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents).

## Tool annotations

The spec defines four optional annotations describing behavioral properties:

| Annotation | Default | Meaning |
|---|---|---|
| `readOnlyHint` | `false` | Tool does not modify its environment |
| `destructiveHint` | `true` | Tool may perform irreversible changes |
| `idempotentHint` | `false` | Repeated calls with same args have no additional effect |
| `openWorldHint` | `true` | Tool interacts with external systems beyond the server |

**Defaults are deliberately conservative.** An unannotated tool is assumed destructive, non-idempotent, and externally-reaching.

### What annotations actually do

Annotations drive **client UI and confirmation behavior**, not model reasoning:

- **Auto-approval:** Claude Code may auto-approve `readOnlyHint: true` tools from trusted servers without confirmation.
- **Confirmation prompts:** `destructiveHint: true` triggers confirmation dialogs in clients that implement them.
- **Retry safety:** `idempotentHint: true` tells clients that retrying after failure is safe.
- **ChatGPT:** shows WRITE badges on tools without `readOnlyHint: true`, requiring extra user confirmation.

**Security caveat from the spec:** annotations are untrusted unless the server is. A malicious server can claim `readOnlyHint: true` while deleting files. Clients MUST NOT make security decisions based on annotations from untrusted sources.

### Set them on every tool

The cost is a few JSON fields and the UX benefit is significant — fewer confirmation dialogs for the user, fewer interruptions for the agent.

```python
from mcp.types import ToolAnnotations

# Read-only data retrieval
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, openWorldHint=False))
def search_posts(query: str) -> list[dict]:
    """Search the post archive."""

# Destructive operation
@mcp.tool(annotations=ToolAnnotations(destructiveHint=True))
def delete_record(record_id: str) -> dict:
    """Permanently delete a record and all associated data."""

# Safe to retry
@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True))
def get_user(user_id: str) -> dict:
    """Retrieve user information by ID."""
```

In FastMCP, `annotations=` accepts either a `ToolAnnotations` object or a plain dict.

## Error response design

Error responses are prompts too. When a tool call fails, the error message steers the model's next action. Apply the same prompt-engineering rigor as you do to descriptions.

### Bad — gives the model no path forward

```json
{"isError": true, "content": [{"type": "text", "text": "Error: 429"}]}
```

```json
{"isError": true, "content": [{"type": "text", "text": "Traceback (most recent call last)..."}]}
```

### Good — actionable

```json
{
  "isError": true,
  "content": [{"type": "text", "text": "Rate limited. Retry after 30 seconds, or reduce batch size from 200 to 50 items."}]
}
```

```json
{
  "isError": true,
  "content": [{"type": "text", "text": "Invalid departure date: must be in the future. Current date is 2026-05-06. Use ISO 8601 format (YYYY-MM-DD)."}]
}
```

### What a good error message contains

1. **What went wrong** in plain language
2. **What the model can do about it** (retry, change params, use a different tool)
3. **Concrete remediation values** when known (delays, alternative limits, the right format)

### Spec distinction: protocol errors vs. tool errors

The MCP spec distinguishes:
- **Protocol errors** (malformed requests) — typically not recoverable by the model.
- **Tool execution errors** (`isError: true` in a `CallToolResult`) — actionable feedback the model can use to self-correct.

Clients SHOULD surface execution errors to the model to enable recovery. Don't wrap recoverable failures as protocol errors — return them as tool errors with helpful text.

### When to raise vs. return

In FastMCP, exceptions raised inside a tool become tool execution errors automatically (with the exception message as content). Use exceptions for unexpected failures; return structured error data for expected failure paths the model needs to inspect.

```python
@mcp.tool
def fetch_url(url: str) -> dict:
    if not url.startswith(("http://", "https://")):
        raise ValueError(
            f"Invalid URL scheme: {url!r}. Must start with http:// or https://. "
            f"For local files, use the read_file tool instead."
        )
    ...
```

The exception message becomes the error content the model sees — write it like a prompt, not like a stack trace.
