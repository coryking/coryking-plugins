# Entry Registry Design — Unifying Search and Display

## The Problem

Three separate text extraction paths that don't agree:

1. **`display()`** on model classes — message text + tool summaries (`→ Bash(...)`), with truncation. Used by `format_entry_line` for grep/read output.
2. **`extract_text()`** — raw message text only, no tool summaries. Was used by search for examples (now fixed to use `display()`).
3. **`extract_tool_text()`** + `_TOOL_TEXT_KEYS` — tool input fields (commands, paths, patterns). Used by `scope=tools` search.

`display()` is vestigial — it started as "each model knows how to display itself" but accumulated truncation and newline escaping that belong in the formatting layer. The only unique logic left is `AssistantTranscriptEntry` building tool summaries.

## The Idea

Replace the scattered extraction with a **registry** — a data structure that controls both "what text do I search?" and "how do I display this?" per entry type.

### Current state: `_TOOL_TEXT_KEYS`

```python
_TOOL_TEXT_KEYS: dict[str, list[str]] = {
    "Bash": ["command", "description"],
    "Write": ["file_path"],
    "Edit": ["file_path"],
    ...
}
```

Only covers tool calls. Only controls search. Display is elsewhere.

### Proposed: entry-level registry with callables

Instead of methods on model classes and a separate tool key map, one registry that handles all entry types. Each entry gets:

- **search text** — a callable that produces searchable text from the entry
- **display text** — a callable that produces the display representation (what the user sees in grep/read output)

For tool calls specifically, the registry could control which input fields are searchable vs payload (the Write.content / Edit.old_string distinction we just fixed by removing them).

### Sketch

```python
# Not final — just the shape

@dataclass
class EntryHandler:
    search: Callable[[TranscriptEntry], str]     # text to match against
    display: Callable[[TranscriptEntry], str]     # text to show in output

ENTRY_REGISTRY: dict[type, EntryHandler] = {
    HumanEntry: EntryHandler(
        search=extract_text,
        display=extract_text,
    ),
    AssistantTranscriptEntry: EntryHandler(
        search=lambda e: extract_text(e),          # or include tool summaries?
        display=lambda e: build_assistant_display(e),  # text + → Bash(...) summaries
    ),
}
```

For tool calls within assistant entries, the registry could also carry per-tool-name config:

```python
@dataclass
class ToolHandler:
    search_fields: list[str]       # which input keys to search
    display: Callable[[str, dict], str]  # tool name + input → display string

TOOL_REGISTRY: dict[str, ToolHandler] = {
    "Bash": ToolHandler(
        search_fields=["command", "description"],
        display=lambda name, inp: f"→ Bash({inp.get('command', '')[:80]})",
    ),
    "Write": ToolHandler(
        search_fields=["file_path"],   # NOT content — that's payload
        display=lambda name, inp: f"→ Write({inp.get('file_path', '')})",
    ),
}
```

### What this kills

- `display()` on all entry model classes
- `summarize_tool_input()` as a standalone function
- `_TOOL_TEXT_KEYS` as a separate data structure
- The mismatch between what we search and what we display

### What this enables

- `scope=tools` naturally uses the same registry that display does
- Adding a new tool type is one entry in the registry, not edits across models/search/formatting
- Truncation lives in `format_entry_line` (the formatting layer), not on models

## Open Questions

- Should the entry-level registry and the tool-level registry be one structure or two? Tools are nested inside assistant entries — there's a natural hierarchy.
- `display()` currently lives on the Pydantic models. Moving it to a registry means the models become pure data. That's cleaner but changes how the codebase reads — you look up behavior in a registry instead of calling a method. Is that better or worse for this codebase?
- The `scope=tools` vs `scope=messages` distinction maps to "search the tool registry" vs "search the entry registry." Does that framing hold?

## Context

This came out of a session (2026-03-31) where we observed:
- Claude Desktop using cc-explorer via bare MCP (no skills) and making 5 separate search_project calls
- search_project examples showing wrong text (tool-scope bug)
- Greedy regex excerpts landing in random spots (centering bug)
- Write.content (14KB source code) being searched as tool text (noise)
- `display()` escaping newlines that then leaked into search excerpts

Each fix was targeted, but the root cause is the same: search, display, and tool extraction are three systems that should be one.
