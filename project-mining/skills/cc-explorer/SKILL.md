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

Explores Claude Code chat history stored as JSONL transcripts. Six MCP tools handle all interaction — call them directly, no CLI commands needed.

All tools return structured JSON dicts. Conversation text appears as compact string arrays using entry line format:
- `[U:id] text` — user message (first 8 chars of turn UUID)
- `[A:id] text` — assistant message with smart tool call summaries (e.g., `→ Edit(/path/to/file)`)

## Tools

- **search_chat_history** — DISCOVERY tool: find WHERE things were discussed. Auto-triage: few hits return content with turn UUIDs, many hits return per-session counts with examples. Use it to locate, then switch to `quote_chat_moment` to read.
- **quote_chat_moment** — pull the full untruncated conversation moment around a specific turn UUID.
- **list_chat_sessions** — list all conversations with stats (message count, agents, tokens, dates).
- **list_agent_sessions** — which sessions spawned subagents? Counts and dates.
- **list_session_agents** — what agents did a specific session dispatch? Status, tokens, duration.
- **get_agent_detail** — full prompt, result, stats for specific agent(s). Optional tool trace.

## When to use what

**"What conversations exist?"** → `list_chat_sessions`. The stats (message count, agent count, dates) help you decide where to look.

**"Find where X was discussed"** → `search_chat_history`. Start broad — the auto-triage tells you whether results are sparse (shows content) or dense (shows counts per session). Narrow from there.

**"Show me the full context of that moment"** → `quote_chat_moment`. Takes a turn UUID from search results and returns the complete exchange — what the user said, what the assistant did, what tools were invoked.

**"Did any sessions use subagents?"** → `list_agent_sessions`. Shows which sessions dispatched agents and how many.

**"What did a session's agents do?"** → `list_session_agents`. Lists every agent a session dispatched with status, token usage, and duration.

**"Deep dive on a specific agent"** → `get_agent_detail`. Returns the prompt the agent received, the result it produced, and execution stats. Use the tool trace option to see the chronological sequence of tool calls.

## Key workflows

### Iterative search (broad → narrow)

Start with a broad search to let the data teach you vocabulary. The auto-triage reveals whether your term is rare (content mode, read it directly) or common (count mode, identify hot sessions). Search those sessions specifically, discover new terms from the results, and repeat.

Multiple search patterns are OR'd and always produce counts — useful for sweeping with several candidate terms at once.

### Search → quote loop

The core research loop: search finds patterns and returns turn UUIDs with each match. Use those UUIDs to quote the full conversation moment — the untruncated text, tool calls with parameters, and surrounding context. This is how you go from "it was mentioned somewhere" to "here is exactly what happened."

**Common anti-pattern**: repeatedly searching with more patterns to extract answers from triage examples. Triage examples are 150-char excerpts — they tell you where to look, not what was said. Once you've identified the relevant sessions and turns (usually 2-3 searches), switch to `quote_chat_moment` to actually read the conversations. Five quotes beats fifteen searches.

### Agent inspection

Trace subagent execution top-down: `list_agent_sessions` identifies which sessions spawned agents. `list_session_agents` shows what a specific session dispatched. `get_agent_detail` reveals what each agent was told to do and what it produced. The tool trace option adds the chronological tool-by-tool timeline — what the agent actually did versus what it was asked to do.

## Tips

- Turn UUIDs from search results are the bridge to quoting — always grab them when you find something interesting.
- High agent counts in session listings signal orchestration sessions (fan-out research, multi-step workflows) — worth inspecting.
- Search scope options let you search inside tool calls (Bash commands, file paths, grep patterns) in addition to conversation text.
- Compaction detection in agent detail reveals where agents hit context limits and lost earlier context.
