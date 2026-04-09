---
name: cc-explorer
description: >
  Explore Claude Code chat history via MCP tools. Use when the user wants to search conversations,
  find what was discussed, trace subagent execution, inspect what an agent did, or browse chat logs.
  Triggers on: "search my chats", "what did that agent do", "trace that session", "look at my
  conversations", "check my chat history", "find where we talked about X", "which sessions used
  agents". Do NOT use for behavioral evidence mining or resume work — that's the project-mining skill.
---

# cc-explorer

Explores Claude Code chat history stored as JSONL transcripts. MCP tools handle all interaction — call them directly, no CLI commands needed.

## The conversation exploration tools

Five tools for exploring chat content, each operating at a different scope — like `ls`, `rg -c`, `rg -C3`, and `sed -n` on a set of JSONL files:

| Tool | Scope | Job |
|------|-------|-----|
| `list_project_sessions` | project | Orient — what conversations exist, with stats |
| `search_project` | project | Scan — which patterns hit, which sessions are hot |
| `grep_session` | session | Examine — matches with context inside one conversation |
| `grep_sessions` | sessions | Fan out — same patterns across N sessions in one call |
| `read_turn` | turn | Read — full fidelity text around a specific moment |

Each step narrows scope and increases fidelity. No tool switches modes or changes output shape based on hit volume.

Entry text in `grep_session`, `read_turn`, and `browse_session` output uses pipe-delimited format: `turn_id|timestamp|role|full_length|display`. `turn_id` leads so it's the first field to grab when feeding `read_turn`. Roles: `U` = user, `A` = assistant, `T` = tool result (output from a tool call). The tool descriptions document this format in detail.

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

- **`list_agent_sessions`** — which sessions spawned subagents? Counts and dates.
- **`list_session_agents`** — what agents did a specific session dispatch? Status, tokens, duration.
- **`get_agent_detail`** — full prompt, result, stats for specific agent(s). Optional tool trace.
- **`session_tool_audit`** — for every subagent in a session, tool counts + error rates + chronological tool-call traces. Use this to answer "are my agents using my tools right?" — see which tools land vs fail, where retries happened, which agents over-call.

Use when tracing what an agent did, correlating outputs with sessions, or building timelines that distinguish "discussed doing X" from "dispatched agents to do X."

## Worktree pooling

Sessions from every git worktree of a project are pooled under one project. Claude Desktop dispatch creates real git worktrees under `<project>/.claude-worktrees/<name>/`, so dispatched work shows up alongside interactive sessions automatically — no need to specify a worktree or know which branch work happened on.

Each session carries a `worktree` field: absent for the main worktree, set to the worktree's directory basename (e.g. `happy-lehmann`) for linked worktrees. `list_project_sessions`, `grep_session`, `read_turn`, `browse_session`, `list_session_agents`, `get_agent_detail`, and `session_tool_audit` all surface it.

**Why it matters for mining:** labeled sessions are usually dispatch-driven, meaning the "user" turn is often a programmatically-constructed prompt, not a human typing in-the-moment. Weight signal accordingly — dispatch sessions are weaker evidence for "user's own words" but stronger evidence for "what the agent decided autonomously." The worktree label also doubles as a git branch bridge: `happy-lehmann` in the session metadata points you at the `happy-lehmann` branch when cross-referencing with git history.

## When to use what

**"What conversations exist?"** → `list_project_sessions`. Stats (message count, agent count, dates) help you decide where to look.

**"Find where X was discussed"** → `search_project` with candidate patterns. Results show which patterns are productive and which sessions contain them. Then `grep_session` on the hot sessions to see actual matches in context.

**"Show me what was said"** → `grep_session` for pattern-matched content with context, or `read_turn` to read a specific moment at full fidelity. Use `full_length` values in grep output to gauge entry size before reading.

**"Trace agent execution"** → `list_agent_sessions` → `list_session_agents` → `get_agent_detail`. Top-down zoom from project to session to individual agent.

## Key workflows

### Progressive search (broad → narrow)

Start with `search_project` using all your candidate patterns in one call. The tool accepts an array and scans all sessions in a single pass regardless of pattern count — 20 patterns costs the same as 1. Front-load everything you can think of. The results show which terms land (high hit count) vs dead weight (zero hits, omitted from output). Session IDs include dates, so you can reason about chronology directly without a separate `list_project_sessions` call.

Then `grep_session` on the hot sessions with your best patterns. Like `search_project`, it takes a `patterns` list and returns per-pattern hit counts plus match blocks with surrounding context — front-load all your candidate terms in one call instead of OR-ing them into a kitchen-sink regex. When you find the moment you need, `read_turn` gives you the full untruncated text.

### The search → grep → read loop

`search_project` locates sessions. `grep_session` examines matches within a session. `read_turn` reads specific moments at full fidelity. Each step uses output from the previous one:
- search_project → session IDs → grep_session
- grep_session → turn UUIDs + full_length → read_turn

### Agent inspection

Trace subagent execution top-down: `list_agent_sessions` identifies which sessions spawned agents. `list_session_agents` shows what a specific session dispatched. `get_agent_detail` reveals what each agent was told to do and what it produced. The tool trace option adds the chronological tool-by-tool timeline.

## Tips

- Turn UUIDs from grep_session are the bridge to read_turn — grab them when you find something interesting.
- `full_length` in grep output tells you how big an entry is before you read it. Large values (5000+) mean tool results or long outputs — use `read_turn` with a `limit` to avoid pulling in too much.
- High agent counts in session listings signal orchestration sessions (fan-out research, multi-step workflows).
- Search is exhaustive by default — patterns match against text, tool inputs (Bash commands, file paths, grep patterns), tool outputs, and assistant thinking. Write tight regex to narrow noisy searches.
