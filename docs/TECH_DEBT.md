# Tech Debt — cc-explorer

Remaining smells. Ordered by dependency and stink.

## ~~1. `extract_subagents` re-parses raw JSONL~~ FIXED (d502288)

Refactored to use `load_transcript()` and typed entries. `_parse_timestamp` eliminated.

## ~~2. Input token sum is duplicated~~ FIXED (d502288)

`total_input_tokens` is now a `@property` on `SubagentInfo`. `_total_input_tokens` deleted from cli_subagents.

## ~~3. Dead code~~ FIXED (d502288)

`_get_searchable_entries`, unused imports deleted.

## 4. Exception catch-all in parser

**File:** `project-mining/src/cc_explorer/parser.py:213`

```python
except (json.JSONDecodeError, ValueError, Exception):
    continue
```

`Exception` is a superset of both others. This silently swallows every bug in the model hierarchy during development — attribute errors, type errors, Pydantic validation errors. When models break, you get silently empty results instead of errors.

**Fix:** `except (json.JSONDecodeError, ValueError, ValidationError)` with an explicit Pydantic import. Or at minimum count skipped lines and report them so you know when parsing is failing.

## 5. Double-parse in load_sessions + search

**File:** `project-mining/src/cc_explorer/search.py`

`load_sessions` calls `load_transcript` for every session to compute stats and titles. Then `triage` and `search` call `load_transcript` again for the same files. Every session is parsed at least twice per command.

For a project with 30+ conversations this is noticeable but not blocking.

**Fix:** Cache entries on `SessionInfo`, or make loading lazy (parse metadata from first line eagerly, full parse on demand). Lower priority — only matters if performance becomes an issue.

## Parked (not worth fixing yet)

**SubagentInfo god object** — 23 fields across 5 concerns (spawn metadata, completion stats, output file metadata, compaction events, prompt/result text), mutated in 3 sequential stages. Works fine. Revisit if SubagentInfo grows more responsibilities.

**`_strip_system_xml` regex list** (parser.py:259-280) — grows with every new Claude Code XML tag. Inherent fragility of parsing an undocumented format. No obvious fix.

**Mixed stdout/stderr in expand overflow** — the overflow per-session counts go to stdout while CSV commands put metadata on stderr. Minor inconsistency.
