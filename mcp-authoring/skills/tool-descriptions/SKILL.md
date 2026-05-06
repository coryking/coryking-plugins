---
name: tool-descriptions
description: Use when writing or reviewing MCP tool descriptions, server `instructions`, parameter schemas, `outputSchema`, `_meta`, elicitation, or tool annotations. Triggers on phrases like "MCP docstring", "tool description", "tool isn't getting picked up", "describe this MCP tool", "FastMCP `@mcp.tool`", "server instructions", "tool annotations", "structured content", or work in `mcp_server.py` / files that import `fastmcp` or `mcp.server`. Surfaces an opinionated reference distilled from the MCP spec (2025-06-18 + 2025-11-25), Anthropic's tool-use guidance, FastMCP 3.x idioms, and the "smelly descriptions" empirical research.
---

# MCP Tool Authoring

Tool descriptions are simultaneously **specifications** and **prompts**. Defects propagate as both spec errors and prompt misguidance. They deserve the same engineering attention as a system prompt — Anthropic credits small refinements with state-of-the-art SWE-bench scores.

The audience for these docs is the LLM that calls your tools. The user is the human running the MCP client. Optimize for the human's outcome, not the LLM's convenience.

## When to consult this skill

- Writing or reviewing a `@mcp.tool` / `@server.tool` decorator
- Server `instructions` field — what to put there vs. in tool descriptions
- A tool exists but the model isn't picking it up, or picks a wrong sibling
- Designing return types: when to use `outputSchema` / `structuredContent`
- Setting `readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`
- Wrapping a third-party MCP tool whose description is bad (Tool Transformation)
- Designing error responses the model can recover from
- A server has 30+ tools and is bleeding context — consolidate or defer

Don't use this skill for: MCP transport/auth setup, debugging connection issues, or general FastMCP architecture (use FastMCP docs).

## Where the answer lives

| If you're working on…                                    | Read                                          |
|----------------------------------------------------------|-----------------------------------------------|
| The description string itself (purpose, when, limits)   | `references/writing-tool-descriptions.md`     |
| Server `instructions`, naming, multi-tool consolidation | `references/server-and-architecture.md`       |
| `outputSchema`, elicitation, `_meta`, FastMCP idioms    | `references/modern-mcp-features.md`           |
| Annotations and error response design                    | `references/annotations-and-errors.md`        |
| Negative instructions, length tradeoffs, anti-patterns   | `references/anti-patterns-and-tradeoffs.md`   |
| Real-world example (Context7's two-tool design)          | `references/case-studies.md`                  |

Grep references first — they total ~2000 lines. Pull the smallest section that answers the question.

## Quick reference

### The five components of a good description

| Component | Covers | Example fragment |
|---|---|---|
| **Purpose** | What it does, task-independent | "Retrieves the current stock price for a ticker." |
| **Guidelines** | When to use, how to use | "Use when the user asks about current prices." |
| **Limitations** | What it can't do, scope | "Only NYSE/NASDAQ. No historical prices." |
| **Parameters** | Meaning, format | "ticker: uppercase symbol, e.g. AAPL." |
| **Examples** | Optional; format-sensitive cases only | inline or via JSON Schema `examples` |

97.1% of surveyed MCP tools fail at least one of these (Yao et al. 2026).

### Front-loading rule

First sentence states the action and resource. Prerequisites come after.
- ❌ "Before using this tool, ensure auth. This creates a contact…"
- ✅ "Create a Salesforce contact. Requires prior Salesforce auth."

### Authoring checklist

- [ ] First sentence states what the tool does (declarative, action-first)
- [ ] When-to-use guidance present (imperative voice OK here)
- [ ] Boundaries stated, or redirect to the right alternative tool
- [ ] Every non-obvious parameter has a description with format/constraints
- [ ] Schema-level constraints used (`Field(ge=, le=, pattern=…)`) not just prose
- [ ] Return shape lives in `outputSchema` (typed return), not prose
- [ ] `readOnlyHint` / `destructiveHint` / `idempotentHint` set accurately
- [ ] Tool name is action-oriented, specific, namespaced (`github_list_prs`)
- [ ] Length ≥ 3–4 sentences for non-trivial tools; concise for trivial ones
- [ ] Error messages explain *what* failed and *what to try* — not raw stack traces
- [ ] Server `instructions` set if cross-tool workflow exists

### Token budget rules of thumb

- < 10 tools: detailed descriptions everywhere; cost is negligible.
- 10–30 tools: detailed for high-use, concise for rare.
- 30+ tools: use tool search / `defer_loading`; keep 3–5 core tools non-deferred.

## Authoring posture

- **Treat the description like a system prompt for one specific situation.** Iterate on it the way you iterate on prompts.
- **Design for outcomes, not API endpoints.** Block went 30 tools → 2 on Linear. GitHub Copilot went 40 → 13 with measurable benchmark gains. Don't expose your REST surface.
- **Server `instructions` are unreliable across clients** (silently dropped by some). Duplicate cross-tool workflow into the most-used tool's description as fallback.
- **Schema beats prose.** Pydantic `Field(ge=…, le=…, pattern=…)` and typed return values produce structural constraints the model reads more reliably than prose admonishments.
- **When you can't fix a third-party server's bad descriptions, wrap it.** FastMCP Tool Transformation lets you re-describe, rename, drop, or constrain args without forking. See `references/modern-mcp-features.md`.
