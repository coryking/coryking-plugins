# Server Instructions, Naming, and Tool Consolidation

Sources: [FastMCP Server Docs](https://gofastmcp.com/servers/server), [Anthropic: Define Tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use), [Anthropic: Code Execution with MCP](https://www.anthropic.com/engineering/code-execution-with-mcp), [MCP Spec: Tools](https://modelcontextprotocol.io/specification/2025-06-18/server/tools).

## Server-level `instructions`

The MCP spec defines an `instructions` field on the server's `InitializeResult`. It provides server-wide guidance — workflow order, relationships between tools, overall purpose.

```python
mcp = FastMCP(
    name="my-server",
    instructions=(
        "Document management system with full-text search.\n\n"
        "Workflow: search_documents to find relevant docs, "
        "then get_document to read full content. "
        "Use create_document for new entries."
    ),
)
```

### What goes where

| Content | Server `instructions` | Tool `description` |
|---|---|---|
| Overall server purpose | Yes | No |
| Tool workflow / sequencing ("call A before B") | Yes | Reference if critical |
| What a specific tool does | No | Yes |
| When to use a specific tool | No | Yes |
| Available accounts / data categories | Yes | No |
| Parameter formats | No | Yes |

Server instructions are the place for cross-tool coordination no single tool description can express: "Start with `activity_report` to discover accounts, then use `list_posts` to find threads, then `get_post` to read them."

### Client support reality

**Critical caveat:** not all clients surface server instructions to the model.

- **Claude Code:** surfaces them. Works as intended.
- **Claude.ai (web):** silently drops the `instructions` field from `InitializeResult` — the model only sees individual tool descriptions.
- **Other clients:** varies. Don't rely on instructions reaching the model.

**Practical implication:** server instructions are valuable but not load-bearing. Duplicate essential workflow guidance into the most important tool's description as a fallback. Or expose a dedicated "help" / "getting_started" tool that returns instructions when called — the model can self-prompt by invoking it.

## Tool naming

### Spec constraints
- 1–128 characters
- Case-sensitive
- Allowed: `A-Z a-z 0-9 _ - .`
- Must be unique within a server

### Patterns that work

**Namespace by service or resource:**
```
github_list_prs, github_create_pr, github_merge_pr
slack_send_message, slack_list_channels
db_query_users, db_insert_user
```

Important when tool search is in play: namespace prefixes let the model find all tools from a service via `github_*`.

**Prefix vs. suffix matters and varies by model.** Anthropic's "Writing Tools for Agents" recommends *testing both* (`asana_search` vs. `search_asana`) on your eval set — measurable differences exist and they're not consistent across model families. If you can't run an eval, prefer service-prefix (`asana_search`) — that's the dominant pattern in Anthropic's own examples.

**Be action-oriented:** `calculate_sum` not `sum_calc`. `delete_user` not `user_deletion`.

**Be specific:** `search_products` not `search`. `get_user_details` not `get`.

## Tool consolidation

Anthropic's explicit guidance: **"Consolidate related operations into fewer tools."** Group with an `action` parameter rather than splitting one-tool-per-endpoint:

```json
{
  "name": "manage_pr",
  "description": "Create, review, or merge a pull request. Specify the action to perform.",
  "input_schema": {
    "type": "object",
    "properties": {
      "action": {
        "type": "string",
        "enum": ["create", "review", "merge", "list"],
        "description": "The operation to perform on pull requests"
      }
    }
  }
}
```

### Why this matters

- Fewer tools reduces selection ambiguity
- Smaller tool surface is easier for the model to navigate
- Less context-window consumption
- **Compounding reliability:** three chained calls at 95% each = 85.7%; one consolidated call stays at ~95%

### Real-world receipts

- **GitHub Copilot:** cut from 40 → 13 tools, with measurable benchmark improvements
- **Block:** rebuilt their Linear MCP server from 30+ tools → 2

The anti-pattern is **API-wrapping**: one MCP tool per REST endpoint. This forces the model to learn your backend architecture. Design for outcomes, not operations.

## Anthropic's "code execution" stance (2025)

Anthropic's "Code Execution with MCP" post pushes a stronger version of this: instead of exposing fine-grained tools, expose a code-execution sandbox plus a small set of high-leverage primitives, and let the model compose them. Worth reading before designing a new server with more than ~10 tools — the question to ask is "could two tools and a sandbox replace these twenty?"

## Token budget thresholds

| Tool count | Strategy |
|---|---|
| < 10 | Detailed descriptions everywhere. Token cost is low. |
| 10–30 | Detailed for high-use; concise for rarely-used. |
| 30+ | Use tool search with `defer_loading: true`. Keep 3–5 core tools non-deferred with detailed descriptions. Deferred tools can have rich descriptions because they only enter context on demand. |

Tool search reduces definition token overhead by 85%+ in large tool sets while maintaining selection accuracy. The search mechanism indexes tool names, descriptions, argument names, and argument descriptions — so the description doubles as a search surface for deferred tools.

Real-world tool-set sizes:

| Setup | Tools | Tokens |
|---|---|---|
| GitHub MCP server | 35 | ~26K |
| Slack MCP server | 11 | ~21K |
| 5-server enterprise setup | 58 | ~55K |
