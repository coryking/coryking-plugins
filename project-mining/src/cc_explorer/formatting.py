"""Low-level formatting helpers for cc-explorer output.

Entry-line formatting and trace rendering used by response model classmethods.
Builder logic lives on the response models themselves (responses.py).

Pipe-delimited entry line format:
  timestamp|role|turn_id|full_length|display
  - timestamp: unix epoch seconds
  - role: U (user) or A (assistant)
  - turn_id: first 8 chars of turn UUID (via PrefixId.__str__)
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


def format_session_ref(session_id: str, timestamp: datetime | None) -> str:
    """Format a session reference: 'session_id (YYYY-MM-DD)' or bare id if no timestamp."""
    if timestamp:
        return f"{session_id} ({timestamp.strftime('%Y-%m-%d')})"
    return str(session_id)


def format_session_date(timestamp: datetime | None) -> str:
    """Format a session date as YYYY-MM-DD, or empty string if no timestamp."""
    if timestamp:
        return timestamp.strftime("%Y-%m-%d")
    return ""


# =============================================================================
# Entry display helpers
# =============================================================================


def format_entry_line(
    entry: TranscriptEntry,
    truncate: int,
    hide: frozenset[str] = frozenset(),
    center_pattern: re.Pattern | None = None,
) -> str:
    """Format entry as pipe-delimited: timestamp|role|turn_id|full_length|display.

    When `center_pattern` is supplied and `truncate` is non-zero, the displayed
    text is an excerpt centered on the first pattern match rather than the
    front of the entry. Used for match lines in grep_session output so the
    matched content is always visible even when it's mid-entry.
    """
    if not isinstance(entry, BaseTranscriptEntry):
        uuid = getattr(entry, 'uuid', None)
        turn_id = uuid if isinstance(uuid, PrefixId) else PrefixId(uuid or '')
        return f"0|?|{turn_id}|0|[?]"

    # Get full display for length calculation
    full = entry.display(truncate=0, hide=hide)
    full_length = len(full)

    # Resolve the displayed text
    if truncate:
        if center_pattern is not None:
            # Center the excerpt on the first match so mid-entry hits stay visible
            from .search import _match_example
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
    return f"{ts}|{role}|{entry.uuid}|{full_length}|{display}"


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
