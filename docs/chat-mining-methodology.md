# Mining Methodology

How the project-mining system searches project histories for behavioral evidence. Covers the three-corpora topology, the research loop, what separates good from mediocre researchers, observed failure modes, and open design questions.

Derived from field observations across 12+ subagents and 4 mining sessions, plus a full six-project field test of the three-corpora topology (April 2026).

## The three-corpora topology

Evidence about a project lives in three places, and each requires a different kind of reading:

- **Process corpus** (chat logs + git history) — how things got built, what decisions were made, what was tried and abandoned. Mined by the **process-analyst** (Sonnet). The richest source for decision-making, struggles, pivots, and human judgment under ambiguity.
- **Codebase corpus** (source, config, architecture, docs) — what was built, how it's structured, what the code itself demonstrates. Mined by the **codebase-analyst** (Opus). The primary source for architectural judgment, implementation quality, and technical depth.
- **Output corpus** (databases, generated media, logs, UI, hardware behavior) — what the running system actually produces. Mined by the **output-analyst** (Sonnet). Labels every finding on a five-rung evidence ladder from direct observation down to inference-from-code.

A **project-scout** (Haiku) runs first per project, producing a compact orientation brief: what exists, how much of it, where it lives, what's reachable. The scout is a map-maker, not an analyst — it does not produce findings or evaluate evidence quality against the lens.

The **orchestrator** (Opus, the user's session) runs two phases: a planning phase that shapes the lens through dialogue with the user, then an execution phase that dispatches researchers, tracks progress, and synthesizes findings.

### Why three corpora instead of one researcher

The predecessor system used a single `mining-researcher` agent that inherited a process-biased worldview: "artifacts are byproducts, only the process is evidence." A user's complaint triggered the split: "sometimes the artifact IS also the evidence." A README that calls something a weekend hack has no bearing on whether the code rises to the level the lens describes — but a single agent steeped in chat logs would absorb that framing.

The split dissolves the false dichotomy. Corpus weighting is lens-dependent: "assess against a staff-engineer rubric" → codebase-analyst primary. "How does the user work with AI" → process-analyst primary. "What does the system actually do" → output-analyst primary.

## The research loop (process-analyst)

Chat log mining is iterative, not linear. The process-analyst uses cc-explorer MCP tools at three zoom levels:

**Search** (`search_project`) — cast a wide net across all sessions with several candidate patterns. Results show which terms land and which sessions are hot. Orientation step.

**Grep** (`grep_session` / `grep_sessions`) — drill into specific sessions with multiple patterns. Front-load all candidates in one call to get per-pattern breakdowns.

**Read** (`read_turn`) — pull a specific conversation moment at full fidelity. Every finding needs a direct quote or close paraphrase with a `session:turn` reference.

**The cycle:** search with 3-4 broad patterns → grep hot sessions → read what the data says → invent 2-3 new search categories from what appears → search/grep those → find the gold → read the specific moments → write the finding.

## What separates good from mediocre researchers

### Vocabulary expansion is the key differentiator

Every researcher receives search vocabulary from the orchestrator. The quality difference is whether they expand beyond it.

**Low-expansion researchers** stay close to orchestrator terms. Searching for `YAGNI|rabbit hole|over-engineer` — nearly verbatim from the dispatch prompt. This is grep with extra steps.

**High-expansion researchers** build new search categories from data signals. Finding correction signals, then asking "corrections about WHAT?" and inventing purpose-specific vocabularies:
- Output quality critique: `catalog|summariz|not interpret|editorializ|dry|academic`
- Protective corrections: `leave that|restore|don't re-anal|don't change|keep it as`
- Delegation refusal: `your job|synthesis$|just write|that is your job`

In the April 2026 field test, the jobsearch-buddy process-analyst showed strong expansion: started with scout-flagged terms, then invented creative patterns to find decision moments — `"that won't scale|doesn't"`, `"that's not right|no"`, `"what are we actually trying|what"`. Those find pushback and ambiguity, not keywords.

### The inferential depth spectrum

Findings fall into three tiers:

**Keyword match** — found the search term and quoted it. This is grep output, not research.

**Contextual match** — found the term and understood the surrounding context.

**Inferential leap** — found evidence of the behavioral pattern without the search term being present. Recognizing that a `SecurityError` exposing `chrome://juggler` in a stack trace is a bot detection fingerprint leak, and connecting it to the reverse engineering narrative. None of the search terms would find this; the researcher had to understand the domain.

Vocabulary expansion correlates with inferential depth. Agents that invented search categories from data signals produced more inferential leaps.

### Cross-corpus synthesis

The topology's value proposition: findings a single researcher couldn't produce. A codebase-analyst finding about `worker/dispatcher.py` and a process-analyst finding about the session where that architecture was chosen are the same finding with two evidence legs. The synthesis step merges them.

In the field test, the codebase-analyst independently discovered a branch-merge thread assembly algorithm with a convergence loop that nobody flagged during scouting. This is the kind of finding that justifies the codebase-analyst's independent read — but it only happened in 3 of 10 substantive findings. The other 7 confirmed scout opinions. See "scout bias" below.

## Failure modes observed in production

### Scout bias flowing into researcher dispatch

**Observed in the April 2026 field test.** Scouts produced evaluative conclusions ("best evidence for X," "focus especially on Y," research suggestions). The orchestrator passed these verbatim to researchers. Result: 7 of 10 substantive findings from the social-media-history codebase-analyst were on topics the scout had pre-flagged. Only 3 were genuinely independent discoveries.

**Root cause chain:** Scout prompt didn't structurally enforce "map only, no findings" → scouts went deep and editorialized (53 tool calls for one project, 196 seconds) → orchestrator treated scout opinions as ground truth → researchers confirmed scout conclusions instead of discovering independently.

**Fix applied:** SKILL.md now instructs the orchestrator to pass only structural facts from scout briefs (what exists, how big, where it is, landmines) and strip evaluative conclusions. The scout prompt also needs tightening — scouts were adding "Next steps for research agents" and "Bottom line" sections beyond their template.

### The planning phase is where quality leverage lives

**Observed in the April 2026 field test.** The alignment conversation was one-shot — orchestrator read the docs, restated the lens, said "ready to go?" The user had to catch that the scout step was skipped. The output was organized by lens signal when the user needed by-project grouping for resume use. This debate should have happened during planning.

A good lens produces good findings almost mechanically. A bad lens produces padding no matter how good the researchers are. Properties of a good lens:
- Points at **observable behaviors**, not abstract qualities
- **Specific enough to guide search** but open enough for inferential leaps
- **Matches the available corpora** — calibrated by scout briefs
- **Calibrated to the output destination** — resume bullets need different evidence grain than STAR episodes

**Fix applied:** SKILL.md rewritten with a planning phase (Phase 1) that shapes the lens through focused dialogue, dispatches scouts immediately to inform the conversation, uses AskUserQuestion for genuine choices (output organization, citation depth, evidence grain), and has a hard gate before dispatch.

### Padding despite anti-padding instructions

Across 12 agents and 3 mining sessions (pre-topology era), no agent returned fewer than 8 findings despite explicit anti-padding instructions ("asymmetric returns are normal — if your assignment only has 2 strong findings, return 2"). Some agents carved a single user message into 3-5 separate findings. Some counted raw commit counts as "velocity signals." The anti-padding instruction needs stronger framing or structural enforcement.

### Silent evidence loss from externalized results

The biggest problem in the pre-cc-explorer era: 38 externalized result files totaling ~4.5MB, almost none read back. When a Bash `rg` command returns too much output, Claude Code writes it to a file and gives the agent a 2KB stub. Evidence gone.

cc-explorer solves this by controlling output size — grep_session truncates to a configurable limit with overflow hints, read_turn accepts a per-entry character limit. No shell commands, no externalization risk.

### Shell state doesn't persist between Bash calls

One agent used a shell variable `$STRIP_DIR` that didn't persist between Bash invocations. The variable resolved to empty, causing `rg` to search from the filesystem root. cc-explorer takes `project` as a parameter (defaulting to CWD), eliminating the variable persistence problem.

### Project Content as Tainted Data

LLM context is flat — skill instructions, project docs, and scanned artifacts all sit at the same level with no privilege separation. Project content can bias analytical behavior the same way unsanitized input biases a SQL query.

Three observed failure modes:
1. **Identity adoption** — bluetaka's CLAUDE.md said "scrappy, not enterprise" and the agent filtered out sophisticated engineering patterns.
2. **Content short-circuit** — a project contained career docs that literally described what the agent was asked to find. It reported doc content as findings instead of examining the process.
3. **Premature scope expansion** — seeing career docs about other projects caused the agent to plan mining those projects instead of staying in scope.

The agent prompts address this with the "visiting analyst, not resident developer" framing and the distinction between operational facts (use for navigation) and development posture (examine, don't adopt).

### Sycophancy noise in chat logs

Chat logs are 50%+ AI being agreeable. Without explicit filtering, agents treat AI praise as corroboration of the human's ideas. The process-analyst prompt includes a sycophancy noise filter naming specific trained behaviors to read past: AI praise is not evidence of idea quality, AI agreement is not evidence of framing correctness, AI enthusiasm is not evidence of direction soundness.

## How the tools map to the research loop

| Stage | Tool | Scope | What it does |
|-------|------|-------|-------------|
| Orient | `list_project_sessions` | project | What conversations exist, with stats |
| Search | `search_project` | project | Which patterns hit, which sessions are hot |
| Grep | `grep_session` / `grep_sessions` | session(s) | Matches with surrounding context |
| Read | `read_turn` | turn | Full fidelity text around a specific moment |
| Inspect | `list_agent_sessions` / `list_session_agents` / `get_agent_detail` | varies | Agent inspection at manifest/session/detail levels |

Each step narrows scope and increases fidelity. No tool switches modes based on hit volume. The corpus is one pool identified by session UUIDs and turn UUIDs.

The process-analyst also uses git (`git log`, `git show`, `git diff`, `git log -S`) for commit history evidence. When multi-human, scope with `git log --author=<subject>`.

## What these tools don't cover

- **Source code** — direct `Read` of modules, schemas, handlers. The codebase-analyst's primary source.
- **System outputs** — databases, fixtures, screenshots, running services. The output-analyst's primary source.
- **Cursor/IDE chat** — separate scripts (`cursor_search_prompts.py`, `cursor_pull_conversation.py`). User-value evidence often lives in Cursor conversations.

## Open design questions

### Synthesis independence

The orchestrator does everything: planning conversation, scout reading, researcher dispatch, progress narration to the user, and final synthesis. By synthesis time, its context is loaded with accumulated state — user framing, scout opinions, dispatch decisions, progressive highlights. The accumulated context is both a strength (deep understanding) and a liability (can't read findings fresh, anchoring on earlier conclusions).

Would synthesis be better from a separate agent with a clean context window that receives only the findings and the lens? That agent would need a durable plan document capturing planning-phase decisions (output organization, citation depth, etc.) — a real architectural change. The concern is real but the scope creep is significant.

### Lens quality meta-knowledge

The skill should understand what makes a lens work well — not just accept what the user hands it. Properties of effective lenses (observable behaviors, corpus-calibrated, output-destination-calibrated) should inform the planning conversation. The skill should act like a reference librarian helping frame the question, not a form accepting a search query.

### Scout scope and editorializing

Scouts are meant to be fast map-makers. In practice they go deep (53 tool calls, 196 seconds) and editorialize. Haiku should help (less inclined to over-elaborate), but the prompt also needs structural enforcement: "your output is exactly the brief template. Do not add sections." The `tools` allowlist (Read, Glob, Grep, Bash, list_project_sessions) constrains what tools are available but doesn't constrain depth of reading.

### Output organization as a planning decision

The field test produced by-lens-signal organization when the user needed by-project for resume use. This is a genuine choice that depends on downstream use — the skill can't prescribe it. The planning phase should surface the options when the answer isn't obvious from context.

### Per-agent progress highlights

As researchers complete, the orchestrator should surface one-line highlights against the lens — what was found, not how many tool calls it took. The accumulation of highlights builds a picture but also creates anchoring bias for the synthesis step. Tension between keeping the user informed and keeping the synthesizer fresh.
