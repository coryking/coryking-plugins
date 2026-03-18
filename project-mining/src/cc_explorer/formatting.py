"""Formatting helpers for cc-explorer MCP output.

All format functions return dicts for structured JSON responses.

Conversation text in grep_session and read_turn output uses pipe-delimited entry line format:
  timestamp|role|turn_id|full_length|display
  - timestamp: unix epoch seconds
  - role: U (user) or A (assistant)
  - turn_id: first 8 chars of turn UUID
  - full_length: character count of the full untruncated entry
  - display: truncated entry text with smart tool call summaries (e.g. → Edit(/path/to/file))
"""

from typing import Any, Optional

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    TextContent,
    ToolUseContent,
    TranscriptEntry,
    summarize_tool_input,
)
from .search import MatchHit, SearchResult, SessionInfo, TriageResult
from .subagents import SubagentInfo
from .utils import iso_timestamp, short_uuid


# =============================================================================
# Entry display helpers
# =============================================================================


def _entry_display(entry: TranscriptEntry, truncate: int = 500) -> str:
    """Call display() on entries that support it. Used by agent trace rendering."""
    if isinstance(entry, BaseTranscriptEntry):
        return entry.display(truncate=truncate)
    return ""


def format_entry_line(entry: TranscriptEntry, truncate: int = 500) -> str:
    """Format entry as pipe-delimited: timestamp|role|turn_id|full_length|display."""
    if not isinstance(entry, BaseTranscriptEntry):
        return f"0|?|{short_uuid(getattr(entry, 'uuid', ''))}|0|[?]"

    # Get full display for length calculation
    full = entry.display(truncate=0)
    full_length = len(full)

    # Get display (truncated or full based on param)
    display = entry.display(truncate=truncate) if truncate else full

    ts = int(entry.timestamp.timestamp()) if entry.timestamp else 0
    role = "U" if isinstance(entry, HumanEntry) else "A"
    turn_id = short_uuid(entry.uuid)
    return f"{ts}|{role}|{turn_id}|{full_length}|{display}"


# =============================================================================
# Conversation tool formatters
# =============================================================================


def format_search_project(
    all_results: list[tuple[str, list[TriageResult]]],
    excerpt_width: int = 150,
) -> dict[str, Any]:
    """Format triage results as pattern-centric output for search_project.

    Input: list of (pattern, [TriageResult, ...]) tuples.
    Output: matches grouped by pattern, sorted by hit count descending.
    Patterns with zero hits are omitted.
    """
    matches: list[dict[str, Any]] = []

    for pat, results in all_results:
        total_hits = sum(r.count for r in results)
        if total_hits == 0:
            continue

        sessions = [r.session.session_id[:8] for r in results]
        examples = []
        for r in results:
            if r.first_match_example:
                examples.append(f"{r.session.session_id[:8]}|{r.first_match_example}")

        entry: dict[str, Any] = {
            "pattern": pat,
            "hits": total_hits,
            "sessions": sessions,
        }
        if examples:
            entry["examples"] = examples
        matches.append(entry)

    matches.sort(key=lambda m: m["hits"], reverse=True)
    return {"matches": matches}


def format_grep_results(
    session_id: str,
    matches: list[MatchHit],
    total: int,
    limit: int,
) -> dict[str, Any]:
    """Format search matches as flat chats arrays for grep_session.

    Each match group = one context window (the match + surrounding turns).
    Uses format_entry_line() for each entry.
    """
    result: dict[str, Any] = {
        "session_id": session_id[:8],
        "showing": len(matches),
        "total_hits": total,
    }

    if len(matches) < total:
        result["overflow"] = "narrow your pattern or use read_turn for specific entries"

    match_blocks: list[dict[str, Any]] = []
    for match in matches:
        chats: list[str] = []
        for e in match.context_before:
            chats.append(format_entry_line(e))
        # The match entry itself — full display for the matched turn
        chats.append(format_entry_line(match.entry))
        for e in match.context_after:
            chats.append(format_entry_line(e))
        match_blocks.append({"chats": chats})

    result["matches"] = match_blocks
    return result


def format_read_turn(
    session_info: Optional[SessionInfo],
    turn: str,
    context: int,
    entries: list[TranscriptEntry],
    limit: int | None = None,
) -> dict[str, Any]:
    """Format a turn read at full fidelity for read_turn.

    When limit is None, truncate=0 gives full text.
    When limit is set, entries exceeding it get truncated.
    """
    truncate = limit if limit else 0
    chats = [format_entry_line(e, truncate=truncate) for e in entries]

    return {
        "session_id": session_info.session_id[:8] if session_info else None,
        "turn_id": turn[:8],
        "chats": chats,
    }


# =============================================================================
# Session listing
# =============================================================================


def _format_session_summary(s: SessionInfo) -> dict[str, Any]:
    """Format a single session's metadata and stats."""
    return {
        "session_id": s.session_id,
        "date": iso_timestamp(s.first_timestamp),
        "title": s.title,
        "messages": s.message_count,
        "agents": s.stats.agent_count,
        "context_tokens": s.stats.context_tokens,
        "output_tokens": s.stats.output_tokens,
        "tools": s.stats.tool_use_count,
    }


def format_conversation_list(sessions: list[SessionInfo]) -> dict[str, Any]:
    """Format conversation listing with usage stats."""
    return {
        "total": len(sessions),
        "sessions": [_format_session_summary(s) for s in sessions],
    }


def format_manifest_view(agent_sessions: list[SessionInfo]) -> dict[str, Any]:
    """Format manifest view: sessions that spawned agents."""
    return {
        "total": len(agent_sessions),
        "sessions": [_format_session_summary(s) for s in agent_sessions],
    }


# =============================================================================
# Agent inspection
# =============================================================================


def format_session_view(
    target: SessionInfo,
    agents: list[SubagentInfo],
    compaction: bool = False,
) -> dict[str, Any]:
    """Format session view: all agents spawned by a session."""
    return {
        "session_id": target.session_id,
        "date": iso_timestamp(target.first_timestamp),
        "title": target.title,
        "total_agents": len(agents),
        "agents": [
            {
                "agent_id": sa.agent_id,
                "tool_use_id": sa.tool_use_id,
                "date": iso_timestamp(sa.timestamp),
                "type": sa.subagent_type or "",
                "status": sa.status,
                "description": sa.description or "",
                "input_tokens": sa.total_input_tokens,
                "output_tokens": sa.output_tokens,
                "tools": sa.total_tool_use_count,
                "duration_ms": sa.total_duration_ms,
            }
            for sa in agents
        ],
    }


def format_agent_detail(
    found: SubagentInfo,
    found_session: SessionInfo,
    trace: bool = False,
    no_reasoning: bool = False,
    entries_map: Optional[dict] = None,
    compaction: bool = False,
) -> dict[str, Any]:
    """Format full detail for a single agent."""
    result: dict[str, Any] = {
        "session_id": found_session.session_id,
        "date": iso_timestamp(found_session.first_timestamp),
        "title": found_session.title,
        "agent_id": found.agent_id,
        "tool_use_id": found.tool_use_id,
        "type": found.subagent_type or "",
        "status": found.status,
        "date_started": iso_timestamp(found.timestamp),
        "input_tokens": found.total_input_tokens,
        "output_tokens": found.output_tokens,
        "tools": found.total_tool_use_count,
        "tool_counts": found.tool_name_counts or {},
        "duration_ms": found.total_duration_ms,
    }

    if found.output_file_exists:
        result["output_file"] = {
            "path": found.output_file_resolved,
            "size_bytes": found.output_file_size,
            "entries": found.output_entry_count,
            "compactions": len(found.compaction_events),
        }
    else:
        result["output_file"] = None

    result["prompt"] = found.prompt or None
    result["result"] = found.result_text or None

    if trace and entries_map and found.agent_id in entries_map:
        result["trace"] = render_trace(
            entries_map[found.agent_id],
            show_reasoning=not no_reasoning,
        )
    else:
        result["trace"] = None

    return result


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
    if sa.agent_id and sa.agent_id.startswith(prefix):
        return True
    if sa.tool_use_id.startswith(prefix):
        return True
    return False
