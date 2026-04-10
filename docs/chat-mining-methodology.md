# Mining Methodology

How the project-mining system searches project histories for behavioral evidence. Covers the three-corpora topology, the research loop, what separates good from mediocre researchers, observed failure modes, and open design questions.

Derived from field observations across 12+ subagents and multiple mining sessions, including field tests of the three-corpora topology and subsequent iterations on scout scope, dispatch discipline, file-based agent I/O, and project grouping.

## The three-corpora topology

Evidence about a project lives in three places, and each requires a different kind of reading:

- **Process corpus** — all artifacts that encode *how and why* the thing was built. The rawest sources are chat logs (Claude Code, Cursor) and git history; the most durable are crystallized artifacts like CLAUDE.md, memory files, design docs, PR discussions, methodology docs, architecture decision records. Mined by the **process-analyst** (Sonnet). Chat logs are the most in-the-moment source but also the most ephemeral — Claude Code purges sessions after ~30 days, so for any project older than a month, git + docs carry most of the weight.
- **Codebase corpus** (source, config, architecture, docs) — what was built, how it's structured, what the code itself demonstrates. Mined by the **codebase-analyst** (Opus). The primary source for architectural judgment, implementation quality, and technical depth.
- **Output corpus** (databases, generated media, logs, UI, hardware behavior) — what the running system actually produces. Mined by the **output-analyst** (Sonnet). Labels every finding on a five-rung evidence ladder from direct observation down to inference-from-code.

A single **project-scout** (Haiku) runs first as a cross-project landscape sweep — one scout agent visits all projects in scope in a single pass and produces a raw inventory: commit counts, session counts, notable directories, footguns. The scout does not rate corpus quality, does not suggest research directions, and does not produce findings. Qualitative judgments belong to the orchestrator (which has the lens) and the researchers (which do the deep reading).

The **orchestrator** (Opus, the user's session) runs two phases: a planning phase that shapes the lens through dialogue with the user and dispatches the scout, then an execution phase that fans out researchers, tracks progress via their return messages, and synthesizes findings by reading each researcher's output file.

## Project groups

Related projects can be analyzed together as a **group** — one researcher trio (codebase, process, output) that covers multiple repositories whose work belongs to the same story. Example: `camoufox`, `camoufox-135`, `stealth-research`, and `bluetaka` form a scraping stack where the decisions in one shaped the others. Analyzing them as a group enables cross-project findings that a single-repo analyst would miss (e.g., a protocol choice in the upstream fork that constrains the downstream consumer).

Grouping is a planning-phase decision surfaced via AskUserQuestion when the scout brief shows related projects. A group with one project is valid — it's just a single-project assignment.

## File-based I/O between orchestrator and agents

Every run creates a **run directory** beside the output file: if output goes to `docs/mining/swe-resume-evidence-2026-04.md`, the run directory is `docs/mining/swe-resume-evidence-2026-04/`. Inside:

- `lens.md` — the full lens text verbatim, written once by the orchestrator before dispatch
- `scout.md` — the scout's landscape brief
- `<group-slug>-<corpus>.md` — one file per (group, corpus) pair

The orchestrator pre-creates placeholder files before dispatching, so the Write permission prompt fires once in the foreground. Background agents that try to write to a new path without prior permission get denied silently and their work is lost.

Every researcher reads `lens.md` as its first step and writes its full findings document to its assigned output file. It returns only a short confirmation (`Wrote N findings to <path>`), a 2-3 sentence top-line highlight for progress display, and any gaps. The full findings stay on disk until the orchestrator reads them during synthesis. This is the main context-pressure fix: with 16 researchers returning 20-30K of findings each, inline returns blow the orchestrator's window; file-based I/O keeps the orchestrator lean until synthesis time.

## Dispatch templates

The orchestrator copies literal fenced-block templates from SKILL.md and fills only the slots — no improvisation allowed. The templates contain: lens file path, project paths, group slug, output file path, subject human (when multi-human), sibling boundaries (when multiple researchers share a group), and the raw landscape inventory for the group.

The templates deliberately have no slot for "look for:" bullets, lens decomposition, data-source prescription, or evaluative framing ("the richest process corpus is X"). Those additions reintroduce the bias and dispatch-override failure modes documented below. If a dispatch is longer than the template, the orchestrator is prescribing methodology.

## Why three corpora instead of one researcher

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

**Observed repeatedly across multiple field tests.** Scouts produced evaluative conclusions ("best evidence for X," "focus especially on Y," research suggestions) that the orchestrator passed to researchers. Researchers then confirmed the scout's opinions instead of discovering independently — one field test showed 7 of 10 findings on topics the scout had pre-flagged.

**Root cause chain:** Scout prompt had an open template and exploratory workflow language → scouts went deep (53 tool calls, 196 seconds for a single project) and editorialized → orchestrator treated scout opinions as ground truth → researchers confirmed those opinions.

**Fix applied — multiple iterations:**
1. SKILL.md told the orchestrator to strip evaluative conclusions before dispatch. This was a band-aid; the scout itself still produced the evaluations.
2. Scout prompt was rewritten with hard template enforcement, a tool-call budget, and explicit "if you find yourself writing about what the evidence means, stop" language. Scout model switched to haiku.
3. Scout was reduced further to a single cross-project landscape sweep that produces raw inventory only — no corpus ratings, no "notable" language. The orchestrator makes qualitative judgments with the lens in hand, not the scout without it. **The scout can't rate process-corpus richness without understanding what process evidence is, and understanding what process evidence is *is the research* — so the rating work was moved out of the scout entirely.**

### The orchestrator prescribing methodology in dispatch prompts

**Observed in the v2.31 field test.** Despite the scout fixes, the first researcher dispatch looked like this:

> "Mine git history and Claude Code chat sessions. This project has the RICHEST process corpus (112 sessions). Look for: how the project evolved from simple scraping to full archival platform, architectural decisions (why Playwright, how the 62-tool architecture emerged), AI tool usage patterns with 112 CC sessions, how the MCP server concept was conceived, struggles with Facebook's anti-scraping measures, data pipeline evolution, schema evolution visible in 23 migrations..."

Every sentence was a methodology injection. The dispatch told the agent *what data sources to use, what to look for, and which findings would be interesting*. The researcher obediently complied and stayed in its prescribed lane — the social-media-history process-analyst made 32 cc-explorer calls + 8 git calls + **zero reads on project docs**, even though its prompt had been updated to treat CLAUDE.md, design docs, and methodology files as primary process evidence.

The orchestrator's dispatch prompt overrode the agent prompt. The agent prompt's analytical guidance lost to the dispatch prompt's specific inquiry hooks.

**Fix applied:** SKILL.md now contains literal fenced-block dispatch templates that the orchestrator must copy verbatim. The templates have slots for lens file path, project paths, group slug, output file path, subject, boundaries, and raw landscape context. They have no slot for "look for" bullets, lens decomposition, data-source hints, or evaluative framing. If the dispatch is longer than the template, the orchestrator is prescribing methodology.

### The orchestrator refusing to ask questions

**Observed in the v2.29–v2.30 field tests.** SKILL.md said "shape the lens through focused dialogue" and "use AskUserQuestion." The orchestrator read the reference docs, built a signal table in its own head, made every decision itself (output organization, destination, grain), asked two questions inline as a wall of text the user never saw, and charged ahead. The user had to abort runs because decisions had been made without them.

**Fix applied:** SKILL.md now has a hard gate — the orchestrator MUST call AskUserQuestion at least once before presenting a dispatch plan. The failure mode is named explicitly in the prompt: "Your tendency will be to read the reference docs, build a signal table in your own head, make every decision yourself, and present a finished plan. That is the failure mode this section exists to prevent."

### Chat log ephemerality

**Observed in the v2.32 run.** Claude Code purges session files after ~30 days. A bluetaka mining run in April 2026 found only 2 chat sessions because the rest had been nuked — but the project had 650 commits, CLAUDE.md, design docs, Terraform configs, and a GitHub PR history. Under a narrow "process = chat + git" framing, bluetaka's process corpus would look thin. Under the broader framing ("any artifact encoding how the thing was built"), it's rich.

**Fix applied:** The process-analyst prompt was rewritten to make "what counts as process evidence" explicit and broad. CLAUDE.md, memory files, design docs, PR discussions, methodology docs, and architecture decision records are primary evidence, not context. The agent has a "pull on threads" instruction: when a chat session references a doc or config file, go read it.

**Caveat:** Even after the prompt change, researchers still default to their comfort zone (cc-explorer + git) unless the orchestrator's dispatch doesn't actively narrow the scope. This is why the dispatch-template fix and the process-broadening fix had to land together.

### Agent output blowing the orchestrator context

**Observed in the v2.31 run.** With 16 researchers each returning ~25K of findings inline, the orchestrator's context was getting eaten for breakfast before synthesis could start. Progress highlights also required reading the full findings to pick the strongest one, further bloating context.

**Fix applied:** Researchers now write full findings to files in the run directory and return only a short confirmation + a 2-3 sentence top-line highlight for progress display. The orchestrator reads the files serially during synthesis, not while accumulating highlights. Lens is also written to a file (read by every researcher) instead of being pasted into every dispatch prompt.

### The planning phase is where quality leverage lives

**Observed across field tests.** A good lens produces good findings almost mechanically. A bad lens produces padding no matter how good the researchers are. Properties of a good lens:
- Points at **observable behaviors**, not abstract qualities
- **Specific enough to guide search** but open enough for inferential leaps
- **Calibrated to the output destination** — resume bullets need different evidence grain than STAR episodes

**Fix applied:** SKILL.md rewritten with a planning phase (Phase 1) that shapes the lens through focused dialogue, uses AskUserQuestion for genuine choices (project grouping, output organization, citation depth, evidence grain, destination), and has a hard gate before dispatch. The orchestrator must also write the lens to a file before dispatching, so every researcher sees the exact same source text rather than the orchestrator's paraphrase.

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

## What the cc-explorer tools don't cover

The progressive-zoom cc-explorer tools are for chat log mining specifically. Other kinds of process evidence the process-analyst uses (and which live outside cc-explorer):

- **Git history** — `git log`, `git show`, `git diff`, `git log -S`, commit message archaeology. When multi-human, scope with `git log --author=<subject>`.
- **Project docs** — `Read` of CLAUDE.md, memory files, design docs, methodology docs, architecture decision records, READMEs that describe reasoning. These are crystallized process evidence, not context.
- **GitHub PRs and issues** — `gh pr list`, `gh pr view <n> --comments`, `gh issue list`. Discussion threads capture design reasoning and review pushback that doesn't always land in chat logs.
- **Cursor/IDE chat** — separate scripts (`cursor_search_prompts.py`, `cursor_pull_conversation.py`). User-value evidence often lives in Cursor conversations.

The other researcher agents have their own primary sources:

- **Source code** — direct `Read` of modules, schemas, handlers. The codebase-analyst's primary source.
- **System outputs** — databases, fixtures, screenshots, running services. The output-analyst's primary source.

## Open design questions

### Synthesis independence

The orchestrator still does most of the workflow: planning conversation, scout reading, researcher dispatch, progress narration to the user, and final synthesis. The file-based I/O change (agents writing findings to disk, returning only short confirmations) reduced the accumulated-context problem significantly — the orchestrator no longer has 16 × 25K of findings inline by synthesis time. But it still accumulates user framing, dispatch decisions, and progress highlights through the run. Whether a separate synthesis agent with a clean context would produce better output is still an open question, now with lower stakes because file-based I/O means the data is already on disk where a second agent could read it.

### Lens quality meta-knowledge

The skill should understand what makes a lens work well — not just accept what the user hands it. Properties of effective lenses (observable behaviors, specific-but-open, output-destination-calibrated) are now named in SKILL.md and surfaced during the planning dialogue. The next step is for the orchestrator to actively coach the user toward a better lens when the initial prompt is weak, not just accept whatever they hand over and try to work with it.

### Chat log archival before the 30-day purge

Claude Code purges session files after ~30 days. For long-running projects, the rawest process evidence (in-the-moment decision traces, pivots, corrections) evaporates unless something preserves it first. The methodology has been broadened to treat durable artifacts (CLAUDE.md, memory files, design docs, PRs) as primary process evidence, which mitigates the loss — but it doesn't replace what's gone. An archival step that runs before the purge and extracts high-signal moments from sessions would be a separate project, not part of project-mining itself.

### Group discovery

Project groups (related projects analyzed together as one researcher trio) are currently decided via AskUserQuestion during planning, prompted by the scout's cross-project notes. The scout doesn't actively hunt for shared tooling, shared code, or cross-references between projects. If it did, grouping could be proposed automatically. For now, the user knows the relationships better than the scout does, so a planning-phase question is the right default.

### Output file validation

The pre-create-placeholder trick reliably triggers Write permissions, but it doesn't verify that researchers actually produce valid findings files. A researcher that crashes partway through leaves a placeholder or a partial document on disk with no signal to the orchestrator. The return protocol asks for a confirmation line, but a crashed researcher won't return one at all. A light validation step (check file size, check that each file has at least one `### Finding` header) during the synthesis handoff would catch these without much ceremony.

### Per-agent progress highlights

As researchers complete, the orchestrator surfaces one-line highlights against the lens. Highlights now come from the agent's return message rather than from reading the full findings file, so they cost almost no context. The accumulation still creates anchoring bias for synthesis — the orchestrator hears "bluetaka: reactive handler" ten times and is primed to weight that finding when writing the final doc. Whether to mute or filter highlights before synthesis is unresolved, but the stakes are lower now that the full findings are on disk and can be read fresh by a hypothetical synthesis agent.
