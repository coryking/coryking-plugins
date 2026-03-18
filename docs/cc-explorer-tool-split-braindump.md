# cc-explorer tool split braindump

From a session in cory-skills analyzing how social-media-history agents use cc-explorer. 2026-03-18.

## The problem

Watched session `4471fb60` in social-media-history. Agent was asked to look up what happened in yesterday's chat. It made **16 search_chat_history calls** in a row, narrowing and re-narrowing patterns, before finally getting to quote_chat_moment. The loop:

1. Searches with multiple patterns (natural instinct — "find me stuff about Trillian, Digsby, imports")
2. Multi-pattern forces count mode → gets back session IDs and hit counts, no content
3. Doesn't understand why it didn't get content → refines patterns and tries again
4. Repeat 4-9 more times
5. Eventually discovers quote_chat_moment and starts actually reading

The auto-triage (content vs count mode switching based on hit volume + multi-pattern forcing counts) is designed for the mining researcher's deliberate methodology but confuses every other agent. When you call something "search" you expect search results.

## The idea: split into distinct tools modeled on unix

If you were doing this with unix tools on JSONL files:

```bash
# Discovery: which files have hits? how many?
rg -c "frustrat" ~/.claude/projects/foo/*.jsonl | sort -t: -k2 -rn

# Search: show me the matches with context
rg -C2 "frustrat" ~/.claude/projects/foo/file1.jsonl

# Read: pull a specific section
sed -n '450,470p' file1.jsonl | jq .
```

Three tools, three jobs, no mode switching:

| Unix | cc-explorer | Job |
|------|------------|-----|
| `rg -c` | `triage_chat_history` | counts per session × pattern |
| `rg -C2` | `grep_chat_history` | matching entries with context |
| `sed`/`less` | `quote_chat_moment` | full untruncated read around a turn |

## grep vs search naming

"grep" sets better expectations than "search". You call grep, you get matching lines. Nobody expects grep to silently return counts instead.

## Overflow in grep

Since this is MCP (not a pipe), we have to own the output size. But overflow should be **truncation**, not a mode switch. "Showing 30 of 847 matches, narrow your pattern or add session" — still returns actual matches, just not all of them. Like `rg | head -30`.

The `limit` param becomes "how many matches to return" (like head -N), not "threshold for switching modes."

## triage stays for mining

The mining researcher prompt already teaches the triage workflow explicitly: "multi-pattern → counts, single-pattern on hot sessions → content, quote the gold." That's a deliberate methodology. `triage_chat_history` is the mining researcher's first step. Regular agents never touch it.

## Output format issues noticed

The current match format with separate `context_before`, `match`, `context_after` fields is needlessly complex:

```json
{
  "context_before": ["[A:7276b50a] → ToolSearch(...)"],
  "match": "[A:d7ab570b] → search_chat_history(...)",
  "context_after": ["[A:4743da8f] → search_chat_history(...)"]
}
```

Should just be a flat entries array like quote_chat_moment uses. Standardize the output of a turn across all tools.

Also: turns have timestamps (the `timestamp` field on BaseTranscriptEntry). Not sure if we're exposing those or sorting by them. Worth thinking about — at minimum, results should be in chronological order within a session.

## What about the mining researcher prompt?

The researcher agent prompt (`agents/mining-researcher.md`) has a whole section teaching the auto-triage search→quote workflow. If we split the tools, that section needs updating to reference `triage_chat_history` + `grep_chat_history` + `quote_chat_moment` as three distinct steps instead of one auto-switching tool.

## Source sessions

- `4471fb60` in social-media-history (2026-03-17) — 16 search calls, the worst spiral
- `2e2de6fb` in social-media-history (2026-03-18) — 5 search calls, similar pattern
- Analysis done by reading the cc-explorer code in `~/.claude/plugins/cache/coryking-plugins/project-mining/2.3.0/src/cc_explorer/`
