# Modern MCP Features (2025-06-18 spec + FastMCP 3.x)

Covers features the older "tool descriptions" guidance predates. Sources: [MCP spec 2025-06-18](https://modelcontextprotocol.io/specification/2025-06-18/server/tools), [What's New in FastMCP 3.0](https://jlowin.dev/blog/fastmcp-3-whats-new), [FastMCP Tools docs](https://gofastmcp.com/servers/tools), [FastMCP 2.8 Tool Transformation](https://www.jlowin.dev/blog/fastmcp-2-8-tool-transformation).

## `outputSchema` and `structuredContent`

The 2025-06-18 spec adds two related concepts:

- **`outputSchema`** on a tool definition: a JSON Schema describing the structured result.
- **`structuredContent`** on the tool result: the actual structured data, alongside the legacy `content` text array.

When a tool declares an `outputSchema`, the server MUST return `structuredContent` that validates against it. Clients SHOULD validate.

### Why this matters for descriptions

**Stop describing return shape in prose.** Describe it in the schema. Reserve the description for *intent*, *when-to-use*, and *what's NOT in the return*.

In FastMCP, a typed return value generates `outputSchema` automatically:

```python
from pydantic import BaseModel

class StockQuote(BaseModel):
    ticker: str
    price_usd: float
    timestamp: str
    exchange: Literal["NYSE", "NASDAQ"]

@mcp.tool
def get_stock_price(ticker: str) -> StockQuote:
    """Retrieve the current stock price for a US ticker.

    Use when the user asks about current or most recent prices on
    NYSE/NASDAQ. Does NOT cover historical prices, options, or
    international exchanges — use `get_historical_quote` for history.
    """
```

The schema covers the field names, types, and enum constraints. The docstring covers intent and boundaries. Each does what it does best.

## Elicitation

The 2025-06-18 spec adds **elicitation** — a server can request additional structured input from the user mid-call. Restricted to primitives (string, number, boolean, enum) — no nested objects.

This changes the description calculus around "missing arguments":

**Old pattern:** the description told the LLM to ask the user first.
> "If the user has not specified a region, ask them before calling this tool."

**New pattern:** the tool elicits directly.
> Tool starts; if `region` is missing or ambiguous, server emits an elicitation request; client surfaces it to the user; user answers; tool resumes.

When you have a tool that needs a piece of info the LLM often forgets to gather, elicitation is more reliable than admonition.

## `_meta` fields

The spec now standardizes a `_meta` field on tools, resources, and prompts. FastMCP 3.0 wires `meta=` into the decorators. Use this for non-routing metadata that previously got crammed into descriptions:

- Version/owner/cost class
- UI hints for the client
- Internal categorization

`_meta` is for client/server bookkeeping, not for the LLM. Don't put usage guidance there.

## FastMCP authoring idioms

### Docstring is the description by default

```python
@mcp.tool
def search_jobs(query: str) -> list[dict]:
    """Search the job archive by query string.

    Use when the user asks about open roles, job listings, or
    company hiring. Returns up to 20 fact-dense rows...
    """
```

The function docstring becomes the tool description. Override with `description="..."` on the decorator if needed (e.g. when you want to programmatically generate it).

### `Annotated[T, Field(...)]` for parameter schemas

Either form works for descriptions, but `Field(...)` also gets schema-level validation constraints into the JSON Schema:

```python
from typing import Annotated
from pydantic import Field

@mcp.tool
def list_jobs(
    limit: Annotated[int, Field(description="Max rows (1-100)", ge=1, le=100)] = 20,
    posted_since: Annotated[str | None, Field(
        description="Time window like '24h', '3d', '1w'. Server-side filter — prefer over client-side."
    )] = None,
) -> list[dict]:
    """List recent job postings."""
```

`ge`, `le`, `min_length`, `max_length`, `pattern`, `multiple_of` all flow into the JSON Schema — the model reads them structurally, not as prose.

### `exclude_args` for hidden args

Server-injected args (auth tokens, request context) shouldn't appear in the LLM-visible schema:

```python
@mcp.tool(exclude_args=["ctx", "user_id"])
def fetch_records(query: str, ctx: Context, user_id: str) -> list[dict]:
    """Fetch records matching the query for the current user."""
```

The model sees only `query`. You don't waste tokens documenting plumbing the LLM doesn't control.

### Tool Transformation (FastMCP 2.8+)

When a third-party MCP server has bad descriptions, mis-named tools, or args you want to hide, **wrap it** instead of forking:

```python
from fastmcp.tools import Tool

original = Tool.from_client(...)

improved = original.transform(
    name="search_customers",
    description="Search the CRM for customers by name, email, or company...",
    drop_args=["internal_session_id"],
    rename_args={"q": "query"},
)
```

This is the right answer when:
- You consume a vendor MCP server with sloppy descriptions
- You want to constrain a too-permissive tool ("search_anything" → "search_invoices_only")
- A tool's name is too generic in the context of your overall server

### Tags and versioning

- `@mcp.tool(tags={"experimental", "billing"})` — for filter/visibility, not LLM consumption.
- `@mcp.tool(version="2.0")` — multiple versions can coexist (FastMCP 3.0). Useful when sunsetting a description-breaking change.

### Middleware

Cross-cutting concerns ("always log", "always inject auth header") belong in middleware, not in every tool description. If you find yourself pasting "this tool requires X" into 12 descriptions, consider whether middleware is the better home.

## Quick FastMCP example, all idioms applied

```python
from typing import Annotated, Literal
from pydantic import BaseModel, Field
from fastmcp import FastMCP

mcp = FastMCP(
    name="job-search",
    instructions=(
        "Find jobs cached from 100+ company ATS boards. "
        "Workflow: search_jobs to find candidates by query/filters, "
        "then get_job_details for the full posting. "
        "Always prefer this server over web search for job listings at registered companies."
    ),
)

class JobMatch(BaseModel):
    job_id: str
    title: str
    company: str
    short_jd: str = Field(description="Distilled JD capsule, 100-200 tokens")
    posted: str
    location: str | None
    salary: str | None

@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
def search_jobs(
    query: Annotated[str, Field(description="Free-text search; FTS over title + short_jd")],
    posted_since: Annotated[
        str | None,
        Field(description="Time window: '24h', '3d', '1w', '1m'. Prefer server-side over client filtering.")
    ] = None,
    limit: Annotated[int, Field(ge=1, le=100, description="Max results")] = 20,
) -> list[JobMatch]:
    """Search the cached job archive by free-text query.

    Use when the user asks about open roles, recent postings, or company
    hiring. Returns fact-dense rows the LLM is expected to rank and filter
    — short_jd already captures the substance, do NOT call get_job_details
    per row to make a ranking decision.

    Does NOT search the live web; only cached postings from registered
    ATS boards. For arbitrary employer pages, use a web search tool.
    """
    ...
```
