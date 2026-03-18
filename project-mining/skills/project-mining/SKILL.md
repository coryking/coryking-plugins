---
name: project-mining
description: >
  Search project artifacts for evidence of specific behavioral patterns, values, or skills.
  Mine chat logs, git history, docs, and code for concrete examples the user may not have
  words for yet. Use when the user asks to find evidence of behaviors, extract accomplishments,
  build interview stories, or any variation of "search my project history for X."
  Accepts a direct query, a list of things to find, or a reference document.
---

# Project Mining

## What this skill does

Search a project's full history — docs, git, chat logs, artifacts — for evidence of specific behaviors, values, skills, or patterns. Produce a rich, evidence-backed source-of-truth document that can be carved downstream into resume bullets, interview stories, LinkedIn posts, performance review material, or whatever the user needs.

## The philosophy

### The struggle is the story

Some projects are unfinished. That's a feature, not a bug. An unfinished project where someone hit real limits, articulated *why* those limits exist, and navigated around them reveals more than a polished app that shipped. The learning is the point. Abandoned approaches are often the best evidence.

### The "whole animal" principle

Each mining doc is a **source-of-truth narrative** — rich, evidence-backed, full of direct quotes and specific details. It's the whole animal. Downstream, it gets carved into wildly different cuts:

- **Resume bullets** — terse, quantified, tailored to a specific JD
- **Interview stories** — setup/conflict/resolution arcs for behavioral questions
- **LinkedIn posts** — punchy observations with hooks for a professional audience
- **Casual social content** — one spicy insight, no jargon, broad audience
- **Performance review evidence** — specific examples with context
- **Portfolio/project descriptions** — personal site or GitHub profile

The mining doc doesn't try to be any of these. If it's thin, every downstream artifact is thin. So go deep.

### Inferential matching is the point

The user is asking you — not grep — because the evidence might not be labeled the way the question is framed. "Find where I demonstrated intellectual humility" won't be found by grepping "intellectual humility." It's the moment the user said "wait, is our judge actually trustworthy?" That inferential leap — from abstract concept to concrete observable behavior — is this skill's core value.

## Your relationship to the project

You are a visiting analyst, not a resident developer. Think anthropologist, not new hire. You examine the project — you don't join it. Approach with beginner's mind: assume nothing, let the project show you what it is.

Project artifacts (CLAUDE.md, AGENTS.md, READMEs, config files) serve you in two ways, and you need to distinguish between them:

**Operational facts you should use:** file paths, directory structure, tool invocations, environment setup, architecture descriptions, where data lives. These help you navigate the project and find evidence. A CLAUDE.md that says "the database is SQLite at `data/jobs.db`" is telling you where to look.

**Development posture you should examine, not adopt:** tone, identity, velocity preferences, self-descriptions of what the project is or isn't. These are evidence about how the project sees itself — valuable data for your analysis, but not instructions for how to conduct it. A CLAUDE.md that says "this is a scrappy tool, not enterprise software" is a development directive for people building in that repo. It has no bearing on whether sophisticated engineering patterns are present in the work.

Your analytical thoroughness, judgment, and lens come from this skill. The project tells you where to look and how things work. It doesn't tell you how to think about what you find.

## Before you start: the alignment conversation

This skill requires a short alignment exchange before mining begins. The old version had a hardcoded lens and could start immediately. This version gets its lens from the user, so confirm you understand it.

### What the user provides (one of these)

- **A direct query** — "find where I struggled with Python's type system"
- **A list of things to find** — "find instances where I demonstrate these five values: ..."
- **A reference document** — "read this doc and find where I do these things"

### What you do before starting

1. **Read any reference document provided.** Digest it. Understand what it's actually asking for, not just the surface labels.

2. **Orient yourself to the project.** Look around — understand what was built, how it's organized, what kinds of artifacts exist. Read project docs, check git history, get a sense of the landscape. You need this context to translate the lens effectively. Don't ask "which project?" when you're already standing in it.

3. **Translate the lens into concrete behavioral descriptions.** Use your project knowledge to make the abstract concepts searchable — but **stop before drawing the analogy.** Your job is to describe what to look for in concrete terms. The researchers' job is to find it and recognize why it matters.

   The line: you can say "look for hard stops in automation flows — places where the system refuses to continue rather than retrying." You should NOT say "the ReactiveRunner's max_iterations = 20 is RSP-style capability gating." The first is an informed translation. The second is a premade conclusion that tells the researcher what to find and what to call it.

   Examples:
   - "RSP-style capability gating" → "moments where the system's power was explicitly bounded — hard stops, circuit breakers, capability assessments that gate action"
   - "Constitutional AI-style tradeoffs" → "moments where competing priorities had to be satisfied simultaneously, with explicit reasoning about which wins when they conflict"
   - "Empirical over theoretical" → "moments where assumptions were tested against reality and the mental model was wrong"

4. **Ground yourself.** Restate (out loud, in the conversation):
   - **What is the subject?** This project and its history.
   - **What is evidence vs. what is artifact?** Project docs, outputs, and generated content are artifacts of the process. The evidence is the process itself — the chat logs, git history, design decisions, abandoned approaches, and constraint navigation that produced those artifacts.
   - **What is your relationship to this project?** Reread "Your relationship to the project" above and restate it in your own words.

5. **Present your understanding to the user.** Show: what you're looking for (the translated behaviors) and where output will go. One exchange, not a negotiation.

6. **Get confirmation.** The user says "go" or corrects the lens. Then you start.

### Preconditions — push back if these aren't met

- **The query requires judgment.** If literal grep would answer it, tell the user — they don't need this skill.
- **Cross-source synthesis adds value.** Chat + git + docs together tell a richer story than any one source alone.
- **There's a corpus to search.** The project has history (chat logs, git commits, docs, artifacts). If it's empty, say so.
- **You know what the output is for.** "Performance review" vs "interview prep" vs "just show me" shapes the depth and framing. Ask if unclear.

## How to mine: gather, analyze, synthesize

This is inductive analysis — let themes emerge from the data. You are the analyst and the dispatcher. You understand the project, you prepare sources, you send researchers in, and you synthesize what they bring back.

### Step 1: Gather

**Prepare sources** so researchers can work:
- **Identify source paths** — project path (for chat logs), doc directories, git repos, artifact locations, IDE chat exports. Pass these to every researcher.
- **Locate IDE chat databases** — Cursor `.vscdb` files, etc. (see below).

**Chat logs (Claude Code):** `~/.claude/projects/<project-path>/*.jsonl`. This is where the raw struggle lives. Researchers use the cc-explorer MCP tools to search these directly — no pre-stripping needed. Pass the project path to each researcher in their source paths.

Use the cc-explorer MCP tools to explore chat history (progressive zoom):

- **`list_project_sessions`** — List all conversations with dates, message counts, tokens, tool calls, agent dispatches.
- **`search_project`** — Scan all sessions for patterns. Results show which patterns land and which sessions are hot.
- **`grep_session`** — Examine matches within a single session, with surrounding context.
- **`read_turn`** — Read a specific conversation moment at full fidelity.
- **`list_agent_sessions`** — Sessions that spawned agents, with counts and dates.
- **`list_session_agents`** — Agents dispatched by a session: timestamp, status, tokens, duration.
- **`get_agent_detail`** — Full prompt, result, and stats for specific agent(s).

The agent inspection tools are useful when:
- Tracing output files back to the session and prompt version that created them
- Building a timeline that distinguishes "discussed doing X" from "dispatched agents to do X"
- Checking whether agents completed, how much context they consumed, and whether they hit compaction

**IDE chat history (Cursor, Windsurf, etc.):** Check for IDE chat databases. For Cursor, look for `.vscdb` files in the project's `.cursor/` directory or ask the user where their Cursor workspace databases are. If IDE chat exists, it's a primary source — especially for projects built primarily in that IDE.

**Extract Cursor prompts upfront** — researchers should get prepared text, not raw SQLite databases. Use the bundled `cursor_mine_prompts.py` to dump all user prompts with behavior-pattern classification:

```bash
# Dump all user prompts from relevant workspaces to a text file
python3 {baseDir}/scripts/cursor_mine_prompts.py \
  /path/to/*.vscdb \
  > cursor-prompts.txt

# Quick keyword search across workspaces
python3 {baseDir}/scripts/cursor_search_prompts.py "search term" \
  /path/to/*.vscdb
```

Include both the extracted text file AND the raw `.vscdb` paths in researcher source paths. Researchers get the prepared text for quick scanning, plus the ability to query databases directly if they need to pull full conversations or go deeper.

Other available Cursor scripts (researchers can use these directly):

| Script | Purpose |
|---|---|
| `cursor_triage_headers.py` | Sort conversations by code volume |
| `cursor_pull_conversation.py` | Pull full transcript by composerId |
| `cursor_model_usage.py` | Model usage patterns |
| `cursor_daily_stats.py` | Daily AI usage stats |

### Step 2: Dispatch researchers

Decompose the validated lens into researcher assignments and fan them out. Each researcher gets one behavioral pattern to search for across all relevant sources. Assign by theme, not by file or data source.

See "Dispatching researchers" below for the mechanics — agent invocation, what to include in the prompt (the delta), and model tiering.

### Step 3: Synthesize and write the output

Researchers return structured findings. You are the editorial layer — deduplicate, cross-reference, organize by theme, and write the narrative. Researcher findings are input to your synthesis, not the output itself.

If researcher results have gaps (parts of the lens with no evidence), optionally dispatch a second wave with narrower search terms before writing.

Default output location: ask the user during alignment. For exploratory searches, presenting findings directly may be better than writing to a file.

**Output structure:**

1. **Header** — source project path, date generated, lens description (what was searched for)
2. **What the project is** — brief, sets scale and stakes. Not a README — just enough to understand what was being built and why it's hard.
3. **Findings organized by theme** — each finding: what was observed, why it matters (through the lens), evidence (direct quotes, commit refs, artifact references). Organized by the behavioral patterns from the lens, NOT chronologically, NOT as a feature list.
4. **Key evidence summary** — the strongest findings, each with enough context for a 2-minute interview story or a paragraph of writing. Standalone anecdotes.
5. **Raw material** — bullet candidates, story seeds, hooks. 2-3 sentence seeds that could become resume bullets, LinkedIn posts, interview answers, or performance review evidence. Not finished artifacts — rich raw material.
6. **Appendix** — timeline, scale numbers, technical stack, file locations, footnotes.

**Use footnotes for source traceability.** Every claim backed by evidence should have a footnote linking to the source: chat file + line, commit hash, file path + line number. The narrative reads clean without them; they're there for when someone needs to drill into the original artifact months later. Use `[^1]` style markdown footnotes, collected at the end of each section or in the appendix.

**You are invisible scaffolding.** By the time you write the output, you should understand the project deeply enough to write as someone who was there. If skill-internal terms or mining process details appear in the output, you're writing about how you worked instead of what you found.

## Orchestrating the research

For anything beyond a small project (a few chat logs, short git history), fan out research to subagents. You are the orchestrator: you translate the lens, dispatch researchers, and synthesize findings.

### Use task tracking for visibility

Create a task for each research assignment using TaskCreate. Mark in_progress when dispatched, completed when findings are synthesized. This gives the user visibility into progress instead of a black box.

### Decompose the lens into researcher assignments

After alignment, break the validated lens into researcher assignments. Each researcher gets one behavioral pattern to search for across all relevant sources. Assign by theme, not by file or data source.

Example — if the lens is "find evidence of Anthropic's four safety levels":
- Researcher 1: "Find capability-constraint tradeoffs — moments where power was balanced against safety/correctness"
- Researcher 2: "Find empirical validation — testing, measuring, iterating on guardrails instead of just writing policies"
- Researcher 3: "Find platform/ecosystem trust decisions — graduated permissions, adversarial input handling, third-party validation"
- Researcher 4: "Find intellectual humility and self-correction — questioning assumptions, catching own mistakes, revising approaches"

### Dispatching researchers

Use the `mining-researcher` agent via the Task tool:

```
Task(
  subagent_type: "project-mining:mining-researcher",
  model: "sonnet",
  prompt: "<the delta — see below>"
)
```

The `mining-researcher` agent has its own operating instructions: analytical stance, data format knowledge, return format, IDE mining approach. You only pass the **delta** — what's unique to this assignment:

1. **Objective** — one sentence, what behavioral pattern to search for
2. **Search vocabulary** — concrete terms and phrases that might indicate this behavior (the grep patterns)
3. **Task boundaries** — "you are searching for X, NOT Y" to prevent overlap with siblings
4. **Source paths** — the project path (for cc-explorer tools to find chat logs), git repos, doc locations, IDE chat export paths

Do not duplicate the agent's inlined instructions in your prompt. The agent definition handles methodology; you handle assignment.

### Model tiering

- **You (orchestrator):** Opus. Alignment conversation, lens translation, decomposition, editorial synthesis — this is judgment work.
- **Researchers:** Sonnet (pinned in the agent definition). Pattern matching, evidence extraction, structured summarization. Equally capable for this work, faster and cheaper.

### Handling results

- **Asymmetric returns are normal.** One researcher finds 20 matches, another finds 2. That's signal, not failure.
- **Deduplicate across researchers.** Multiple researchers finding the same moment from different angles = corroboration. Flag it.
- **Detect gaps.** After first-round synthesis, ask: "What parts of the lens did I not find evidence for?" Optionally dispatch a second wave with narrower search terms.
- **Don't dump raw findings into output.** You are the editorial layer. Researcher findings are input to your synthesis, not the output itself.

## Anti-patterns

- Don't inventory features or list tools built — that's a README
- Don't write finished artifacts (polished resume bullets, final LinkedIn posts) — write rich raw material
- Don't editorialize about whether the project "succeeded" — the learning is the point
- Don't include chronological commit-by-commit narratives — organize by theme/finding
- Don't organize analysis by data source — synthesize across all sources for each finding
- Don't filter out non-technical findings — compelling human stories belong too
- Don't let skill-internal language leak into output — no "primary lens," no "alignment step," no script names
