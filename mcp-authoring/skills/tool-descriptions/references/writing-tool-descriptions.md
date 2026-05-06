# Writing Tool Descriptions

Sources: [Anthropic: Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [Anthropic: Define Tools](https://platform.claude.com/docs/en/agents-and-tools/tool-use/implement-tool-use), [Yao et al. 2026 "MCP Tool Descriptions Are Smelly"](https://arxiv.org/html/2602.14878v1), [MCP filesystem reference server](https://github.com/modelcontextprotocol/servers).

## Anthropic's headline guidance

> "Provide extremely detailed descriptions. This is by far the most important factor in tool performance."

Specifically:
- What the tool does
- When it should be used (and when it should not)
- What each parameter means and how it affects behavior
- Important caveats or limitations
- What the tool does NOT return (if the name might be misleading)

Aim for at least 3–4 sentences per tool description, more for complex tools.

## The five components

Empirical analysis of 856 MCP tools across 103 servers found 97.1% had at least one description quality defect. Effective descriptions cover:

| Component | What It Covers | Example |
|---|---|---|
| **Purpose** | What the tool does, independent of task context | "Retrieves the current stock price for a given ticker symbol" |
| **Guidelines** | When to use it (activation criteria) and how to use it | "Use when the user asks about current stock prices. Returns the latest trade price in USD." |
| **Limitations** | What it cannot do, constraints, edge cases | "Only covers major US exchanges (NYSE, NASDAQ). Does not provide historical prices." |
| **Parameter explanation** | What each input means, expected formats | "ticker: Stock ticker symbol in uppercase, e.g. AAPL for Apple Inc." |
| **Examples** | Concrete input/output patterns (optional) | Inline in description or via JSON Schema `examples` |

You don't need all five on every tool. Ablation studies showed "Purpose + Limitations" or "Purpose + Guidelines" often matched fully augmented descriptions.

## Three levels of description quality

- **Level 1 (vague):** "Search for flights"
- **Level 2 (functional):** "Search for available flights between two airports on a given date"
- **Level 3 (complete):** "Search for available flights between two airports on a specific date. Returns up to 20 results sorted by price. Use 3-letter IATA airport codes (e.g., 'LAX', 'JFK'). Only searches economy class. For business or first class, use the `premium_flight_search` tool. Dates must be within the next 330 days."

Level 3 answers: what, when, how, what format, what limitations, where to go instead.

## The front-loading rule

LLMs may not read entire descriptions with equal attention, especially in large tool sets. Lead with action and resource, not prerequisites.

❌ "Before using this tool, make sure the user is authenticated with Salesforce. This tool creates a new contact record…"

✅ "Create a new Salesforce contact. Requires prior Salesforce authentication."

## Tone and voice

Lead **declarative**, follow **imperative**. This mirrors Anthropic's own examples and the official MCP filesystem server.

```
Retrieves a user profile by ID. Use this when you need user details like
name, email, or role. Returns null if the user ID does not exist.
```

- Declarative for the lead: "Retrieves the current stock price."
- Imperative for usage: "Use when…"
- Negative declarative for boundaries: "It will not provide other information."

Treat descriptions like explaining to a new team member — make implicit context explicit.

## Examples in descriptions

### `input_examples` is NOT a real MCP spec field

Earlier guidance treated `input_examples` as Anthropic-API-level metadata. The MCP spec's tool object has `name`, `title`, `description`, `inputSchema`, `outputSchema`, `annotations`, and `_meta` — no `input_examples`. Don't rely on it for MCP. Use one of:

1. **JSON Schema `examples` keyword** on individual properties — portable, model-readable.
2. **Inline format hints** in the parameter or tool description.
3. **The `default` value** when there's a sensible one.

### When examples earn their tokens

The "smelly descriptions" research found removing examples from augmented descriptions caused no statistically significant degradation. Examples earn their token cost only when:

- Input format is ambiguous (date strings, query DSLs, nested objects)
- Schema alone can't communicate the expected shape
- The tool has multiple valid call patterns

For simple tools, skip examples — the schema is enough.

### Inline format hints (cheap, often sufficient)

```python
@mcp.tool
def search(
    query: Annotated[str, Field(
        description="Search query. Supports boolean operators: 'python AND web', 'error OR exception'"
    )],
) -> list[dict]:
    """Search the knowledge base for articles matching a query."""
```

## Parameter descriptions

Three elements make a parameter description useful:

1. **Semantic meaning**: what the value represents ("The stock ticker symbol")
2. **Format specification**: what it looks like ("3-letter IATA airport code, e.g. 'LAX'")
3. **Constraints**: valid range or values ("1–100", "must be in the future", "uppercase only")

### When it's noise

If the parameter name and type cover it, a description adds nothing.

```python
# Redundant — the name and type say it all
count: Annotated[int, Field(description="The count")] = 10

# Useful — clarifies meaning and constraints
max_results: Annotated[int, Field(description="Maximum items to return (1-100)", ge=1, le=100)] = 10
```

### Schema constraints beat prose

Pydantic `Field` validators (`ge`, `le`, `min_length`, `max_length`, `pattern`) encode constraints into the JSON Schema itself. Models read structural constraints more reliably than prose:

```python
width: Annotated[int, Field(description="Target width in pixels", ge=1, le=2000)] = 800
```

### Coerce, don't lecture

Accept flexible inputs and normalize internally. If a date param could be `"2024-01-15"`, `"January 15"`, or `"yesterday"` — accept all three and normalize server-side. Don't force the model to guess the exact format.

## Describing the return value

**Modern guidance:** describe the return shape in `outputSchema` (typed return type → FastMCP generates the schema), not in prose. The 2025-06-18 spec defines `outputSchema` and `structuredContent`. See `modern-mcp-features.md`.

What still belongs in the description:
- High-level summary of what comes back ("Returns the latest trade price in USD")
- What's NOT in the response if the name is misleading
- Special return values (null on miss, empty list semantics, etc.)
