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

from datetime import datetime

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    TextContent,
    ToolUseContent,
    TranscriptEntry,
    summarize_tool_input,
)
from .subagents import SubagentInfo
from .utils import PrefixId


# =============================================================================
# Session reference helpers
# =============================================================================


def format_session_ref(session_id: str, timestamp: datetime | None) -> str:
    """Format a session reference: 'session_id (YYYY-MM-DD)' or bare id if no timestamp."""
    if timestamp:
        return f"{session_id} ({timestamp.strftime('%Y-%m-%d')})"
    return str(session_id)


# =============================================================================
# Entry display helpers
# =============================================================================


def format_entry_line(entry: TranscriptEntry, truncate: int = 500) -> str:
    """Format entry as pipe-delimited: timestamp|role|turn_id|full_length|display."""
    if not isinstance(entry, BaseTranscriptEntry):
        uuid = getattr(entry, 'uuid', None)
        turn_id = uuid if isinstance(uuid, PrefixId) else PrefixId(uuid or '')
        return f"0|?|{turn_id}|0|[?]"

    # Get full display for length calculation
    full = entry.display(truncate=0)
    full_length = len(full)

    # Get display (truncated or full based on param)
    display = entry.display(truncate=truncate) if truncate else full

    ts = int(entry.timestamp.timestamp()) if entry.timestamp else 0
    role = "U" if isinstance(entry, HumanEntry) else "A"
    return f"{ts}|{role}|{entry.uuid}|{full_length}|{display}"


# =============================================================================
# Trace rendering
# =============================================================================


def render_trace(
    entries: list[TranscriptEntry], show_reasoning: bool = True
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
                summary = summarize_tool_input(item.name, item.input)
                lines.append(f"{ts}  {item.name:<20s}{summary}")
                ts = "        "
            elif isinstance(item, TextContent) and show_reasoning:
                text = item.text.strip()
                if not text:
                    continue
                text_lines = text.split("\n")
                for line in text_lines[:5]:
                    truncated = line[:100] + "..." if len(line) > 100 else line
                    lines.append(f'          "{truncated}"')
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
