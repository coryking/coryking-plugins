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

Four tools for exploring chat content, each operating at a different scope — like `ls`, `rg -c`, `rg -C3`, and `sed -n` on a set of JSONL files:

| Tool | Scope | Job |
|------|-------|-----|
| `list_project_sessions` | project | Orient — what conversations exist, with stats |
| `search_project` | project | Scan — which patterns hit, which sessions are hot |
| `grep_session` | session | Examine — matches with context inside one conversation |
| `read_turn` | turn | Read — full fidelity text around a specific moment |

Each step narrows scope and increases fidelity. No tool switches modes or changes output shape based on hit volume.

Entry text in `grep_session` and `read_turn` output uses pipe-delimited format: `timestamp|role|turn_id|full_length|display`. The tool descriptions document this format in detail.

## The agent inspection tools

Four tools for tracing subagent execution — a separate axis from conversation content:

- **`list_agent_sessions`** — which sessions spawned subagents? Counts and dates.
- **`list_session_agents`** — what agents did a specific session dispatch? Status, tokens, duration.
- **`get_agent_detail`** — full prompt, result, stats for specific agent(s). Optional tool trace.

Use when tracing what an agent did, correlating outputs with sessions, or building timelines that distinguish "discussed doing X" from "dispatched agents to do X."

## When to use what

**"What conversations exist?"** → `list_project_sessions`. Stats (message count, agent count, dates) help you decide where to look.

**"Find where X was discussed"** → `search_project` with candidate patterns. Results show which patterns are productive and which sessions contain them. Then `grep_session` on the hot sessions to see actual matches in context.

**"Show me what was said"** → `grep_session` for pattern-matched content with context, or `read_turn` to read a specific moment at full fidelity. Use `full_length` values in grep output to gauge entry size before reading.

**"Trace agent execution"** → `list_agent_sessions` → `list_session_agents` → `get_agent_detail`. Top-down zoom from project to session to individual agent.

## Key workflows

### Progressive search (broad → narrow)

Start with `search_project` using several candidate patterns. The results show which terms land (high hit count) vs dead weight (zero hits, omitted from output). The session IDs tell you where to drill in.

Then `grep_session` on the hot sessions with your best pattern. Matches appear with surrounding context — enough to understand what was happening. When you find the moment you need, `read_turn` gives you the full untruncated text.

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
- `scope: "tools"` searches inside tool call inputs (Bash commands, file paths, grep patterns) — useful for finding what agents *did*, not just what was *said*.
