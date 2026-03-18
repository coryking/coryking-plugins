# MCP Tool Descriptions (docstrings + parameter descriptions)

These are the verbatim docstrings and Field descriptions for the executor to use when implementing the tool split. Copy-paste into mcp_server.py.

## Entry line format (module docstring update)

```
Conversation text in results uses pipe-delimited entry line format:
  timestamp|role|turn_id|full_length|display
  - timestamp: unix epoch seconds
  - role: U (user) or A (assistant)
  - turn_id: first 8 chars of turn UUID
  - full_length: character count of the full untruncated entry
  - display: truncated entry text with smart tool call summaries (e.g. → Edit(/path/to/file))
```

---

## list_project_sessions

Replaces: `list_chat_sessions`

**Docstring:**
```python
"""List conversations in a project with stats: dates, message counts, token usage, tool calls, agent dispatches.

This is the orientation step — like `ls -la` on the project's chat history. Use it to see what exists before searching.
"""
```

Parameters unchanged from current `list_chat_sessions`.

---

## search_project

Replaces: `search_chat_history` (multi-pattern / triage behavior only)

**Docstring:**
```python
"""Scan a project's chat history for patterns and report where they appear.

Like `rg -c` across all sessions — tells you which patterns are productive and which sessions are hot. Use this to orient before drilling into a specific session with grep_session.

Returns pattern-centric results: each pattern shows its hit count, which sessions contain it, and a few centered excerpts. Patterns with zero hits are omitted.

Excerpts embed the session ID: "session_id|...centered text around match..."
Use the session IDs to decide where to grep next.
"""
```

**Parameters:**
```python
patterns: Annotated[
    list[str],
    Field(description="Regex patterns to scan for (case-insensitive). Results grouped by pattern, sorted by hit count."),
]
project: Annotated[
    str | None,
    Field(description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."),
] = None
role: Annotated[
    Literal["user", "assistant", "all"],
    Field(description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."),
] = "user"
scope: Annotated[
    Literal["messages", "tools", "all"],
    Field(description="Content scope: 'messages' for conversation text, 'tools' for tool inputs (Bash commands, file paths, grep patterns), 'all' for both. Using 'tools' or 'all' searches both roles regardless of the role parameter."),
] = "messages"
excerpt_width: Annotated[
    int,
    Field(description="Character width of centered excerpt examples."),
] = 150
```

**Output shape:**
```json
{
  "matches": [
    {
      "pattern": "frustrat",
      "hits": 20,
      "sessions": ["4471fb60", "2e2de6fb"],
      "examples": [
        "4471fb60|...I'm getting frustrated with the way this keeps...",
        "2e2de6fb|...frustrated that the mock tests passed but prod..."
      ]
    }
  ]
}
```

Patterns with zero hits are omitted (sparse output).

---

## grep_session

New tool (replaces single-pattern content mode of `search_chat_history`)

**Docstring:**
```python
"""Show matches for a pattern within a single conversation, with surrounding context.

Like `rg -C3` on a single file — returns matching entries centered and truncated, with surrounding turns for context. Each entry includes its full character length so you can gauge size before calling read_turn.

Results are grouped into match blocks (like grep's `--` separator between context groups). Each block is a window of turns around a match, returned in chronological order.

Entry format in chats arrays: timestamp|role|turn_id|full_length|display
"""
```

**Parameters:**
```python
session: Annotated[
    str,
    Field(description="Session ID or prefix. Required — use search_project to find session IDs first."),
]
pattern: Annotated[
    str,
    Field(description="Regex pattern to search for (case-insensitive)."),
]
project: Annotated[
    str | None,
    Field(description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."),
] = None
context: Annotated[
    int,
    Field(description="Number of surrounding turns to include with each match (like grep -C)."),
] = 2
role: Annotated[
    Literal["user", "assistant", "all"],
    Field(description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."),
] = "user"
scope: Annotated[
    Literal["messages", "tools", "all"],
    Field(description="Content scope: 'messages' for conversation text, 'tools' for tool inputs (Bash commands, file paths, grep patterns), 'all' for both."),
] = "messages"
limit: Annotated[
    int,
    Field(description="Max matches to return (like head -N). Overflow is truncated, not mode-switched."),
] = 30
```

**Output shape:**
```json
{
  "showing": 5,
  "total_hits": 5,
  "session_id": "4471fb60",
  "matches": [
    {
      "chats": [
        "1710644400|A|7276b50a|3200|Here's the refactored version with the new pattern... → Edit(src/auth.py)",
        "1710644401|U|d7ab570b|147|ugh, that's not what I meant. I said keep the original structure",
        "1710644403|A|4743da8f|890|You're right, I apologize. Let me revert to the original... → Edit(src/auth.py)"
      ]
    },
    {
      "chats": [
        "1710645100|U|a1b2c3d4|82|can you check if the slashdot import actually finished",
        "1710645102|A|e5f6a7b8|4832|Let me search for that. → search_chat_history({'patterns': ['slashdot.*finished'...",
        "1710645104|A|c9d0e1f2|210|The import completed successfully with 2,847 stories imported."
      ]
    }
  ]
}
```

When truncated:
```json
{
  "showing": 30,
  "total_hits": 847,
  "overflow": "narrow your pattern or use read_turn for specific entries",
  "session_id": "4471fb60",
  "matches": [...]
}
```

`overflow` field only present when results are truncated.

---

## read_turn

Replaces: `quote_chat_moment`

**Docstring:**
```python
"""Read a specific moment in a conversation at full fidelity.

Like `sed -n '450,470p'` — reads a specific section of the conversation without pattern matching. Takes a turn UUID (from grep_session output) and returns the surrounding conversation with full untruncated entry text.

Use the full_length values from grep_session output to gauge how large entries are before reading. If an entry is very large, use the limit parameter to cap per-entry character output.

Entry format in chats array: timestamp|role|turn_id|full_length|display
(When limit is not set, display contains the full untruncated text — full_length == len(display).)
"""
```

**Parameters:**
```python
turn: Annotated[
    str,
    Field(description="Turn UUID or prefix to center on (from grep_session output)."),
]
project: Annotated[
    str | None,
    Field(description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."),
] = None
context: Annotated[
    int,
    Field(description="Number of turns before and after to include."),
] = 3
limit: Annotated[
    int | None,
    Field(description="Max characters per entry in output. Entries exceeding this are truncated with '... [truncated, full_length chars total]'. Omit for full text."),
] = None
```

**Output shape:**
```json
{
  "session_id": "4471fb60",
  "turn_id": "d7ab570b",
  "chats": [
    "1710644390|U|f1e2d3c4|312|I need to figure out why the slashdot import stalled. The logs show it got to about 500 requests and then just stopped without an error.",
    "1710644392|A|a5b6c7d8|4832|Let me check the import logs. → Bash('tail -50 /var/log/import.log')",
    "1710644393|A|d7ab570b|890|The import stalled because the rate limiter kicked in at 500 requests. The slashdot API returns 429 after hitting their rate limit, and our fetcher treats 429 as a fatal error instead of backing off.",
    "1710644410|U|b9c0d1e2|43|ok so we need to add backoff",
    "1710644412|A|e3f4a5b6|2100|Right. I'll add exponential backoff to the fetcher. → Edit(src/fetcher.py, old_string='if resp.status_code != 200: raise FetchError(f\"HTTP {resp.status_code}\")', new_string='if resp.status_code == 429: ...')"
  ]
}
```
