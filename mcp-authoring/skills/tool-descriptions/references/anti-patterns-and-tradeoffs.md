# Anti-Patterns, Negative Instructions, and Length Tradeoffs

Sources: [Yao et al. 2026 "MCP Tool Descriptions Are Smelly"](https://arxiv.org/html/2602.14878v1), [Anthropic: Advanced Tool Use](https://www.anthropic.com/engineering/advanced-tool-use), [Anthropic: Writing Tools for Agents](https://www.anthropic.com/engineering/writing-tools-for-agents), [Anthropic: Tool Search](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool).

## Negative instructions

### When they help

Effective for **disambiguation between similar tools**. They prevent wrong-tool selection when multiple tools have overlapping purposes.

✅ "Only searches economy class. For business or first class, use the `premium_flight_search` tool."

The pattern: constrain *and* redirect. Both halves matter — a negative without an alternative leaves the model stuck.

✅ Boundary statement: "It will not provide any other information about the stock or company."

This prevents the model from expecting more than the tool delivers.

### When they hurt or are wasted

❌ "Do not use this tool incorrectly." — model can't act on it.

❌ "Do not use this tool to send emails" on a weather tool — model would never attempt it; pure token waste.

❌ "DO NOT MAKE UP DATA. ALWAYS USE REAL VALUES." — shouty all-caps without specifics rarely changes behavior.

### Anthropic's web search example

Claude was unnecessarily appending "2025" to web search queries, biasing results. They fixed it with a targeted negative: *"do not append the current year to queries unless the user specifically asks for time-bound results."* Specific, bounded, and tells the model when the negative does NOT apply.

## Length and token tradeoffs

### The tradeoff is real

The "smelly descriptions" research found augmenting descriptions improved task success by a median of 5.85 percentage points but **also increased execution steps by 67.46%**. Longer descriptions improve accuracy but reduce efficiency.

There's no universal "right length" — it's domain- and tool-set-specific.

### Ablation studies: you don't need all five components

Practitioners could often achieve equivalent performance using subsets:
- "Purpose + Limitations"
- "Purpose + Guidelines"

These often matched fully augmented descriptions while consuming fewer tokens. Pick the components your model is actually getting wrong, don't pile all five on every tool.

### Progressive disclosure changes the math

With Anthropic's tool search (`defer_loading: true`), deferred tools are not in context until discovered. This means:

- **Non-deferred tools** (3–5 most-used): keep descriptions detailed but efficient. Always in context.
- **Deferred tools:** descriptions can be richer because they only enter context when needed. The search mechanism indexes name, description, argument names, and argument descriptions — so the description doubles as a search surface.

Tool search reduces definition token overhead by 85%+ in large tool sets while maintaining selection accuracy.

### Practical guidance

| Tool count | Strategy |
|---|---|
| < 10 | Detailed descriptions everywhere. Token cost is low. |
| 10–30 | Detailed for high-use; concise for rarely-used. |
| 30+ | Use tool search with `defer_loading`. Keep 3–5 core tools non-deferred. |

## Common anti-patterns (with prevalence rates)

From Yao et al.'s analysis of 856 MCP tools across 103 servers:

### 1. Vague Purpose (56% of tools)
The description fails to articulate what the tool does clearly enough for the model to decide whether to use it. Fix: lead with action and resource ("Retrieve X by Y").

### 2. Missing Boundaries / Unstated Limitations (89.8%)
The description doesn't say what the tool cannot do. The model tries it for tasks it will fail at. Fix: state scope explicitly, redirect to alternatives.

### 3. Missing Usage Guidelines (89.3%)
No "when to use" guidance. With multiple similar tools, the model picks randomly. Fix: include "Use when…" sentence.

### 4. Opaque Parameters (84.3%)
Parameters with no descriptions or descriptions that just restate the name. `user_id: "The user ID"` adds nothing. Fix: include format, constraints, semantic meaning.

### 5. API-Wrapping Anti-Pattern
One MCP tool per REST endpoint (`create_pr`, `review_pr`, `merge_pr`, `list_prs`, `get_pr`). Forces the model to learn your backend architecture. Fix: consolidate by outcome (`manage_pr` with action enum). See `server-and-architecture.md` for receipts (GitHub Copilot 40→13, Block 30+→2).

### 6. Error Messages That Don't Teach
A raw `429` or Python traceback tells the model nothing. Fix: prompt-engineer error responses. See `annotations-and-errors.md`.

### 7. Bloated Tool Responses
Returning entire database records when the model only needs an ID and status. Fix: return high-signal fields with stable identifiers. Offer a `response_format` parameter (detailed/concise) when both use cases exist.

### 8. Ambiguous Tool Names
`notification-send-user` vs. `notification-send-channel` — clear to a human reading them side-by-side, but the model may not distinguish reliably. Fix: consistent prefixing, explicit distinction in descriptions, consider consolidation.

### 9. Description-as-Documentation
Walls of text suitable for a human reading reference docs. The LLM doesn't benefit from headers, change logs, "see also" sections, or implementation notes. Fix: cut anything that doesn't help the model decide when/how to call the tool.

### 10. Implementation Leakage
"This tool calls the `/v2/customers/search` endpoint with..." — the model doesn't need to know your backend. Fix: describe what the tool does, not how it's built.

## Counter-anti-patterns (don't over-correct)

- **Don't strip descriptions to one-liners** because you read about token costs. Anthropic's #1 guidance is "extremely detailed descriptions." Brevity isn't the goal; high-signal-per-token is.
- **Don't drop schema constraints** in favor of prose. `Field(ge=1, le=100)` beats "must be between 1 and 100" every time.
- **Don't paste your README** into the description. The audience is the LLM mid-decision, not a developer browsing docs.
