# Chat Mining Methodology: search→quote

How mining-researcher agents search Claude Code chat logs for behavioral evidence. Derived from field observations across 12 subagents and 3 mining sessions (see `investigation-reports/mining-researcher-field-observations.md` for the raw data).

## The research loop

Chat log mining is iterative, not linear. The best researchers follow a two-stage loop:

**Search** (`search_project`) — orient across all sessions. Start broad with multiple patterns to see which terms land and which sessions are hot. Each round of reading should produce new search terms the researcher didn't start with — "intellectual humility" shows up as "wait, is our judge actually trustworthy?"

**Grep** (`grep_session`) — examine matches in context within a single session. This is where you read the data and invent new search terms.

**Read** (`read_turn`) — extract the full conversation moment. Once a researcher has found something worth reporting, they need: what the assistant said that triggered the reaction, the user's exact words, and what happened next. A finding without a direct quote is a claim without evidence.

The cycle: search with 3-4 broad patterns → grep hot sessions → read what the data says → invent 2-3 new search categories from what appears → search/grep those → find the gold → read the specific moments → write the finding.

## What separates good from mediocre researchers

The field data reveals a clear spectrum across 12 agents:

### Vocabulary expansion is the key differentiator

Every researcher receives search vocabulary from the orchestrator. The quality difference is whether they expand beyond it.

**Low-expansion researchers** stayed close to orchestrator terms. BT2's product-direction agent searched for `YAGNI|rabbit hole|over-engineer|keep it simple` — nearly verbatim from the dispatch prompt. It found what those words pointed to and stopped. This is grep with extra steps.

**High-expansion researchers** built new search categories from data signals. SMH's large-file researcher found correction signals, then asked "corrections about WHAT?" and invented purpose-specific vocabularies:

- Output quality critique: `catalog|summariz|not interpret|editorializ|dry|academic`
- Context contamination: `don't pull|don't read|spoil|blow our context|contamina`
- Protective corrections: `leave that|restore|don't re-anal|don't change|keep it as`
- Delegation refusal: `your job|synthesis$|just write|that is your job`

BT1's state-machines agent pivoted from generic architecture terms to domain-specific ones after early hits, then traced the full git evolution across 10 commits and recognized that the architecture is deliberately NOT a state machine while solving state machine problems — an inferential leap no search term could produce.

### The inferential depth spectrum

Findings fall into three tiers:

**Keyword match** — found the search term and quoted it. Searching for "YAGNI" and finding a CLAUDE.md line that says "YAGNI." This is grep output, not research.

**Contextual match** — found the term and understood the surrounding context. Finding an auth error in a chat log and understanding it was about EasyAuth vs custom JWT validation.

**Inferential leap** — found evidence of the behavioral pattern without the search term being present. Recognizing that a `SecurityError` exposing `chrome://juggler` in a stack trace is a bot detection fingerprint leak, and connecting it to the reverse engineering narrative. None of the search terms would find this; the researcher had to understand the domain.

Expansion quality correlates with inferential depth. Agents that invented search categories from data signals produced more inferential leaps.

### Cross-source synthesis is rare but valuable

Chat-log researchers almost never corroborate findings with git history or project docs. They stay in their lane. The standout exception was SMH's git/docs researcher, which synthesized across 4 sources (two agent definitions, a process doc, and a memory file) for a single finding. Chat is one source among several — git, docs, code, and Cursor history all carry evidence. The best findings combine multiple sources.

## Failure modes observed in production

### Silent evidence loss from externalized results

The biggest problem across all sessions: 38 externalized result files totaling ~4.5MB, almost none read back. When a Bash `rg` command returns too much output, Claude Code writes it to a `tool-results/*.txt` file and gives the agent a 2KB stub. The agent sees the stub and moves on. Evidence gone.

BT2's cross-functional agent had 1.5MB of externalized evidence (11 files) that never made it into findings. It produced the longest final output of any agent in its session, but the patterns in those 1.5MB were invisible to it.

cc-explorer solves this by controlling output size — grep_session truncates to a configurable limit and includes overflow hints, and read_turn accepts a per-entry character limit.

### Shell state doesn't persist between Bash calls

One agent (BT1 state-machines) used a shell variable `$STRIP_DIR` that didn't persist between Bash invocations. The variable resolved to empty, causing `rg` to search from the filesystem root: 8 minutes wasted, 482 streaming progress records, laptop heating. The agent detected the problem, fixed it, and still produced the best findings in its session — but the wasted effort is avoidable.

cc-explorer takes `project` as a parameter (defaulting to CWD), eliminating the variable persistence problem.

### Progress line bloat in JSONL transcripts

One agent's 2.6MB JSONL file was 87.7% `progress` type streaming records — UI artifacts with zero analytical value. The actual content was 336KB. Anyone analyzing subagent transcripts needs to filter these out.

### Findings delivery is fragile

In the SMH session, 27 structured findings from the best researcher were lost through conversation rewinds. Researcher findings exist only in the task handle / conversation context. If the orchestrator's context loses them (through branching, compaction, or task handle severing), they are gone. The orchestrator spent 20+ tool calls trying to reconstruct from raw files on disk.

### Padding despite explicit anti-padding instructions

The agent definition says "asymmetric returns are normal — if your assignment only has 2 strong findings, return 2." Despite this, no agent across any session returned fewer than 8 findings. Some agents carved a single user message into 3-5 separate findings. Some counted raw commit counts as "velocity signals." The anti-padding instruction needs stronger framing or structural enforcement (e.g., requiring a strength self-assessment per finding).

## How the tools map to the loop

cc-explorer exposes MCP tools that match the research workflow:

| Stage | Tool | Scope | What it does |
|-------|------|-------|-------------|
| Orient | `list_project_sessions` | project | What conversations exist, with stats |
| Search | `search_project` | project | Which patterns hit, which sessions are hot |
| Grep | `grep_session` | session | Matches with surrounding context in one conversation |
| Read | `read_turn` | turn | Full fidelity text around a specific moment |
| Inspect | `list_agent_sessions` / `list_session_agents` / `get_agent_detail` | varies | Agent inspection at manifest/session/detail levels |

Each step narrows scope and increases fidelity — like `ls`, `rg -c`, `rg -C3`, and `sed -n` on JSONL files. No tool switches modes based on hit volume.

The corpus is treated as one pool of data. Sessions are identified by short UUID + auto-generated title (first human message, truncated) + date — not filenames. Every hit includes a `session:xxx/turn:yyy` reference that points to the immutable JSONL entry.

`project` defaults to CWD — only needed when inspecting a different project's history.

### Progressive zoom design

`search_project` returns pattern-centric results: each pattern shows its hit count, which sessions contain it, and centered excerpts. Researchers start broad to see which terms land, then `grep_session` on hot sessions to see matches in context. `read_turn` pulls the full moment when the evidence is found. Each tool has one output shape — no mode switching.

### Tool call visibility in output

Assistant entries in search and quote output include inline tool call summaries: `→ ToolName(key="value", ...)`. This shows what agents actually did — Bash commands run, files read/edited, MCP tools invoked — alongside what they said. In search output, tool summaries are subject to the same truncation as text; in quote output (truncate=0), full parameters are shown.

### Searching tool inputs

`scope: "tools"` (on both `search_project` and `grep_session`) searches inside tool_use blocks — Bash commands, Read/Edit file paths, Grep patterns, Agent prompts. This surfaces what agents *did*, not just what was *said*. `scope: "all"` searches both messages and tool inputs. When using tools/all scope, the role filter automatically includes both user and assistant entries.

## What these tools don't cover

Chat mining is one part of the researcher's toolkit. Researchers also need:

- **Git history** — `git log`, `git show`, `git diff` for tracing architectural evolution. Some of the strongest findings come from git (BT1 state-machines traced 10 commits to reconstruct an architecture's evolution).
- **Source code** — direct `Read` of modules, schemas, handlers. BT1's Mozicode agent read 25 Perl modules.
- **Project docs** — CLAUDE.md, architecture decisions, design docs. Often the "why" behind code changes.
- **Cursor/IDE chat** — separate scripts (`cursor_search_prompts.py`, `cursor_pull_conversation.py`). User value evidence often lives in Cursor conversations where the user corrects copy or argues about UX.

These remain separate tools with their own access patterns. cc-explorer handles the chat log part of the loop.
