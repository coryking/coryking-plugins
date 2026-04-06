"""Low-level formatting helpers for cc-explorer output.

Entry-line formatting and trace rendering used by response model classmethods.
Builder logic lives on the response models themselves (responses.py).

Pipe-delimited entry line format:
  turn_id|timestamp|role|full_length|display
  - turn_id: first 8 chars of turn UUID (via PrefixId.__str__) — leads so it's
    the first thing grabbed when an agent extracts it for read_turn
  - timestamp: unix epoch seconds
  - role: U (user), A (assistant), or T (tool result)
  - full_length: character count of the full untruncated entry
  - display: body only (truncated text + tool summaries)
"""

from __future__ import annotations

import re
from datetime import datetime

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    TextContent,
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    format_tool_input,
)
from .subagents import SubagentInfo
from .utils import PrefixId, smart_truncate


# =============================================================================
# Session reference helpers
# =============================================================================


def format_session_date(timestamp: datetime | None) -> str:
    """Format a session date as YYYY-MM-DD, or empty string if no timestamp."""
    if timestamp:
        return timestamp.strftime("%Y-%m-%d")
    return ""


# =============================================================================
# Excerpt extraction
# =============================================================================


def _match_example(text: str, pattern: re.Pattern, width: int = 150) -> str:
    """Extract an example excerpt centered on the first match within text.

    Centers the window so the match start is visible (not the midpoint of a
    greedy span). Snaps slice boundaries to word breaks to avoid fragments.
    Used for both per-session triage examples and grep_session match-line
    rendering — anywhere a long body needs to surface a specific hit.
    """
    # Collapse whitespace for display
    text = re.sub(r"\s+", " ", text).strip()
    m = pattern.search(text)
    if not m:
        return text[:width]
    # Start the window a few words before the match so there's leading context
    match_start = m.start()
    lead = min(30, match_start)  # up to 30 chars of leading context
    start = max(0, match_start - lead)
    end = min(len(text), start + width)
    # If we hit the end, pull the start back
    if end - start < width:
        start = max(0, end - width)
    # Snap start forward to a word boundary (space) if we're mid-word
    if start > 0:
        space = text.find(" ", start)
        if space != -1 and space < match_start:
            start = space + 1
    # Snap end back to a word boundary
    if end < len(text):
        space = text.rfind(" ", start, end)
        if space > start:
            end = space
    snippet = text[start:end]
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{snippet}{suffix}"


# =============================================================================
# Entry display helpers
# =============================================================================


def format_entry_line(
    entry: TranscriptEntry,
    truncate: int,
    hide: frozenset[str] = frozenset(),
    center_pattern: re.Pattern | None = None,
) -> str:
    """Format entry as pipe-delimited: turn_id|timestamp|role|full_length|display.

    turn_id leads so agents grab it first when extracting for read_turn.

    When `center_pattern` is supplied and `truncate` is non-zero, the displayed
    text is an excerpt centered on the first pattern match rather than the
    front of the entry. Used for match lines in grep_session output so the
    matched content is always visible even when it's mid-entry.
    """
    if not isinstance(entry, BaseTranscriptEntry):
        uuid = getattr(entry, 'uuid', None)
        turn_id = uuid if isinstance(uuid, PrefixId) else PrefixId(uuid or '')
        return f"{turn_id}|0|?|0|[?]"

    # Get full display for length calculation
    full = entry.display(truncate=0, hide=hide)
    full_length = len(full)

    # Resolve the displayed text
    if truncate:
        if center_pattern is not None:
            # Center the excerpt on the first match so mid-entry hits stay visible
            display = _match_example(full, center_pattern, width=truncate)
        else:
            display = entry.display(truncate=truncate, hide=hide)
    else:
        display = full

    ts = int(entry.timestamp.timestamp()) if entry.timestamp else 0
    if isinstance(entry, HumanEntry):
        role = "U"
    elif isinstance(entry, ToolResultEntry):
        role = "T"
    else:
        role = "A"
    # Escape newlines for pipe-delimited single-line output
    display = display.replace("\n", "\\n")
    return f"{entry.uuid}|{ts}|{role}|{full_length}|{display}"


# =============================================================================
# Trace rendering
# =============================================================================


def render_trace(
    entries: list[TranscriptEntry], show_reasoning: bool = True, *, truncate: int,
) -> list[str]:
    """Render a chronological trace of tool calls and reasoning."""
    lines: list[str] = []

    for entry in entries:
        if not isinstance(entry, AssistantTranscriptEntry):
            continue

        ts = (
            entry.timestamp.strftime("%H:%M:%S") if entry.timestamp else "        "
        )

        for item in entry.message.content:
            if isinstance(item, ToolUseContent):
                summary = format_tool_input(item.name, item.input, truncate=truncate)
                lines.append(f"{ts}  {item.name:<20s}{summary}")
                ts = "        "
            elif isinstance(item, TextContent) and show_reasoning:
                text = item.text.strip()
                if not text:
                    continue
                text_lines = text.split("\n")
                for line in text_lines[:5]:
                    lines.append(f'          "{smart_truncate(line, 100)}"')
                if len(text_lines) > 5:
                    lines.append(f"          ... ({len(text_lines) - 5} more lines)")
                ts = "        "

    return lines


# =============================================================================
# ID matching helper
# =============================================================================


def matches_id(sa: SubagentInfo, prefix: str) -> bool:
    """Check if a subagent matches an agent_id or tool_use_id prefix."""
    return sa.agent_id == prefix or sa.tool_use_id == prefix
