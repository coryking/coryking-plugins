---
name: cc-explorer
description: >
  Explore Claude Code chat history via MCP tools. Use when the user wants to search conversations,
  find what was discussed, trace subagent execution, inspect what an agent did, or browse chat logs.
  Triggers on: "search my chats", "what did that agent do", "trace that session", "look at my
  conversations", "check my chat history", "find where we talked about X", "which sessions used
  agents", "ask a past session", "convert a session into a subagent", "question that old conversation".
  Do NOT use for behavioral evidence mining or evidence-document work — that's the project-mining skill.
---

# cc-explorer

Explores Claude Code chat history stored as JSONL transcripts. MCP tools handle all interaction — call them directly, no CLI commands needed.

## The conversation exploration tools

Tools for exploring chat content, each operating at a different scope — like `ls`, `rg -c`, `rg -C3`, and `sed -n` on a set of JSONL files:

| Tool | Scope | Job |
|------|-------|-----|
| `list_projects` | all projects | Orient — which projects exist (one row per repo, worktrees flattened) |
| `list_project_sessions` | project(s) | Orient — what conversations exist, with stats (defaults to CWD) |
| `search_projects` | projects (all by default) | Scan — which patterns hit, in which project/session |
| `grep_session` | session | Examine — matches with context inside one conversation |
| `grep_sessions` | sessions | Fan out — same patterns across N sessions in one call |
| `read_turn` | turn | Read — full fidelity text around a specific moment |
| `get_activity_timeline` | projects (all by default) | Reconstruct — cross-project attention over a time window (a bucket_minutes-grain grid, default 5 min, of turn counts + pre-computed rollups) |

Project selection is uniform: every tool takes a `projects` list (paths or bare names). Omit it and the search/locate tools sweep **all** projects — the recall path when you remember a conversation but not where it happened. `list_project_sessions` is the exception: it defaults to the current project (use `list_projects` for the cross-project overview). Each result carries its `project`, so pass that back to scope follow-ups.

**The search corpus is complete.** Search reads subagent transcripts too — `<sessionId>/subagents/agent-*.jsonl`, including workflow-orchestrated orphans — not just the main session. A subagent's own tool calls, thinking, and Bash are searchable, and any match that came from a subagent body names the `agent` it lives in (drill in with `get_agent_detail`).

Each step narrows scope and increases fidelity. No tool switches modes or changes output shape based on hit volume.

Entry text in `grep_session`, `read_turn`, and `browse_session` output uses pipe-delimited format: `turn_id|timestamp|role|full_length|display`. `turn_id` leads so it's the first field to grab when feeding `read_turn`. Roles: `U` = user, `A` = assistant, `T` = tool result (output from a tool call). The tool descriptions document this format in detail. In agent-team sessions, a `U` turn that is a teammate DM (orchestration, not the human) renders labeled as `[teammate: <sender> → <recipient>] ...` rather than as raw `<teammate-message>` XML.

### Controlling assistant turn detail with `hide`

`grep_session`, `read_turn`, and `browse_session` accept a `hide` parameter — a comma-separated set of assistant-turn content atoms to suppress from both search and display:

| Atom | What it suppresses |
|------|--------------|
| `thinking` | Extended thinking blocks (prefixed with `[thinking]`) |
| `inputs` | Tool call summaries (`→ Bash(git status)`) |
| `outputs` | Tool results (separate `T`-role entries interleaved after assistant turns) |

Default is `""` (show and search everything). Pass `hide="outputs"` to suppress noisy tool results, or `hide="inputs,outputs,thinking"` for a text-only view. Text is always shown. When tool outputs are huge, control volume with `truncate` — `hide` is for category filtering, not size management.

## The agent inspection tools

Tools for tracing subagent execution — a separate axis from conversation content:

- **`list_project_sessions(min_agents=1)`** — which sessions spawned subagents? The entry point: filter the normal session list down to the ones that dispatched agents.
- **`list_session_agents`** — what agents did a specific session dispatch? Status, tokens, duration.
- **`get_agent_detail`** — full prompt, result, stats for specific agent(s). Optional tool trace.
- **`audit_session_tools`** — for every subagent in a session, tool counts + error rates + chronological tool-call traces. Use this to answer "are my agents using my tools right?" — see which tools land vs fail, where retries happened, which agents over-call.

Use when tracing what an agent did, correlating outputs with sessions, or building timelines that distinguish "discussed doing X" from "dispatched agents to do X."

## The conversion tools

Tools that create, mutate, or remove transcripts — the one mutating axis in the toolset. `convert_session` only ever copies; `rewind_transcript` and `delete_conversions` mutate or delete, but **only conversion artifacts** (files carrying the `x-converter-provenance` line) — a real session or dispatched subagent is never touched.

> **Prerequisite for `session_to_subagent` + `SendMessage`:** resuming a converted subagent uses the agent-teams runtime, which exists only when the calling session was started with `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` in `settings.json` (env block) followed by a Claude Code restart. Without it the conversion still writes a correct file, but nothing can resume it — `SendMessage` returns *"no transcript to resume"*. Quick check: if `SendMessage` is not in your toolset, agent-teams is off — don't convert; use `grep_session`/`read_turn` on the source instead. (`subagent_to_session` → `claude -r` needs no env var.)

- **`convert_session`** — copy a session into a subagent under the calling session (direction `session_to_subagent`), or a subagent out to a top-level session (direction `subagent_to_session`).
- **`rewind_transcript`** — truncate a conversion artifact (session or subagent) **in place** at a chosen turn, discarding everything after, so it resumes from that earlier point. Eligible only for conversion artifacts — a real session or dispatched subagent is refused untouched. Destructive (the cut tail is gone); use `convert_session` first if you want to keep the original.
- **`delete_conversions`** — remove subagent artifacts the converter created. Refuses everything else, including converted sessions. Permanent — no undo.

> You don't have to clean up after yourself: a pristine (never-resumed) `session_to_subagent` fork is auto-reaped once it's older than ~24h, swept when any cc-explorer server in that project starts or stops. Resumed forks are kept (they hold unique conversation). `delete_conversions` is only for tidying up *now* instead of waiting for the sweep.

Convert a session to a subagent when the question needs the session itself, not excerpts from it: the reasoning behind a decision, a synthesis of the whole arc, a judgment call on evidence it hasn't seen, or a domain expert whose built-up context would be expensive to rebuild. Resume the new agent with `SendMessage(to: <created_id>)`; its reply is its final message; message it again to follow up. For facts, quotes, locations, and tool-call ground truth, stay on the read tools.

Convert a subagent to a session when the user wants to read or continue an agent's run themselves — hand them the `claude -r` command from the response.

Rewind a conversion artifact when you want to replay it from a fixed point — re-run a skill from a known starting state, or regenerate a different user prompt. Read the artifact (`browse_session`/`read_turn`) to find the turn uuid, then `rewind_transcript(src_id, turn, cut)`. `cut="after"` (default) keeps the named turn as the new tail; `cut="before"` drops the named turn onward — use it to rewind to just *before* a user prompt so you can re-drive from there. The tail is auto-trimmed to a resumable boundary (dangling `tool_use`, trailing noise), and `lines_at_creation` is re-stamped so the artifact stays deletable. Resume the rewound artifact the same way you'd resume the original (`SendMessage` for a subagent, `claude -r` for a session).

### Composing the first message to a converted conversation

Open with the `suggested_handoff` from the tool response. The conversation has no way to know its interlocutor changed — messages arrive unlabeled, and you occupy the same `user` role its human did — so the handoff's one job is to resolve the contradiction between its runtime ("you are a subagent") and its history (an interactive session, mid-relationship with someone else). Then say who you are and what you want, in whatever role fits the intent: witness, fact-checker, expert consult, plain continuation. The framing controls stance, not correctness — without one, the conversation defaults to being its old user's assistant and offers to resume the relationship.

Mind the knowledge asymmetry (the emic/etic gap): you've read excerpts from the outside; it lived the whole thing and is the authority on what its conversation was about. Offer your reading as a reading — "my understanding from the outside is this session was about X; correct me if that's off" — never as established fact. A presupposed frame invites it to elaborate your guess instead of reporting its reality, and when the premise is flat wrong it has to spend its whole answer fighting the frame. Converted conversations do push back rather than play along — but don't bet on that against a confidently-asserted wrong premise.

Its knowledge ends where the conversation ended. If the answer should account for the present, brief it on what changed first, then ask for the judgment. Relay `environment` facts from the response only when the ask depends on them — e.g. it will run tools in a cwd it doesn't remember having left.

## Worktree pooling

Sessions from every git worktree of a project are pooled under one project — and this holds cross-project too: `list_projects` and an omitted-`projects` search show one row per repo, not one per worktree. Claude Desktop dispatch creates real git worktrees under `<project>/.claude-worktrees/<name>/`, so dispatched work shows up alongside interactive sessions automatically — no need to specify a worktree or know which branch work happened on.

Each session carries a `worktree` field (absent for the main worktree, set to the worktree's directory basename like `happy-lehmann` for linked worktrees) and a `project` field (the repo it belongs to). `list_project_sessions`, `grep_session`, `read_turn`, `browse_session`, `list_session_agents`, `get_agent_detail`, and `audit_session_tools` all surface both.

**Why it matters for mining:** labeled sessions are usually dispatch-driven, meaning the "user" turn is often a programmatically-constructed prompt, not a human typing in-the-moment. Weight signal accordingly — dispatch sessions are weaker evidence for "user's own words" but stronger evidence for "what the agent decided autonomously." The worktree label also doubles as a git branch bridge: `happy-lehmann` in the session metadata points you at the `happy-lehmann` branch when cross-referencing with git history.

## When to use what

**"What conversations exist?"** → `list_project_sessions`. Stats (message count, agent count, dates) help you decide where to look.

**"Find where X was discussed"** → `search_projects` with candidate patterns (omit `projects` to sweep everything when you don't know which project it was). Results show which patterns are productive and which project/session contains them. Then `grep_session` (scoped to the returned project) to see actual matches in context.

**"Show me what was said"** → `grep_session` for pattern-matched content with context, or `read_turn` to read a specific moment at full fidelity. Use `full_length` values in grep output to gauge entry size before reading.

**"Trace agent execution"** → `list_project_sessions(min_agents=1)` → `list_session_agents` → `get_agent_detail` (or `audit_session_tools` for the whole-session tool-usage view). Top-down zoom from project to session to individual agent.

**"Why did that session decide X? What did it learn?"** → `convert_session`, then `SendMessage` (needs `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` — see the conversion-tools prerequisite above). Grep finds what was said; a converted session can answer for what it meant.

## Key workflows

### Progressive search (broad → narrow)

Start with `search_projects` using all your candidate patterns in one call. The tool accepts an array and scans every session in a single pass regardless of pattern count — 20 patterns costs the same as 1. Front-load everything you can think of. The results show which terms land (high hit count) vs dead weight (zero hits, omitted from output), and tag each hit with its `project` (and `agent`, if it came from a subagent body). Omit `projects` to sweep everything; pass it once you know where to look.

Then `grep_session` on the hot sessions with your best patterns, passing the `project` the search returned. Like `search_projects`, it takes a `patterns` list and returns per-pattern hit counts plus match blocks with surrounding context — front-load all your candidate terms in one call instead of OR-ing them into a kitchen-sink regex. When you find the moment you need, `read_turn` gives you the full untruncated text.

### The search → grep → read loop

`search_projects` locates the project + sessions. `grep_session` examines matches within a session. `read_turn` reads specific moments at full fidelity. Each step uses output from the previous one:
- search_projects → project + session IDs → grep_session
- grep_session → turn UUIDs + full_length → read_turn

### Agent inspection

Trace subagent execution top-down: `list_project_sessions(min_agents=1)` identifies which sessions spawned agents. `list_session_agents` shows what a specific session dispatched. `get_agent_detail` reveals what each agent was told to do and what it produced (the tool trace option adds the chronological tool-by-tool timeline), while `audit_session_tools` gives the whole-session view of how every agent used its tools.

## Tips

- Turn UUIDs from grep_session are the bridge to read_turn — grab them when you find something interesting.
- `full_length` in grep output tells you how big an entry is before you read it. Large values (5000+) mean tool results or long outputs — use `read_turn` with a `limit` to avoid pulling in too much.
- High agent counts in session listings signal orchestration sessions (fan-out research, multi-step workflows).
- Search is exhaustive by default — patterns match against text, tool inputs (Bash commands, file paths, grep patterns), tool outputs, and assistant thinking. Write tight regex to narrow noisy searches.
- Converted conversations push back on wrong premises rather than play along — but offer your understanding as understanding anyway; don't make them fight a frame you asserted as fact.
