"""Formatting helpers for cc-explorer MCP output.

All format functions return dicts for structured JSON responses.
Conversation text appears as compact string arrays using [U:id]/[A:id] entry line format.
"""

from collections import OrderedDict
from typing import Any, Optional

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    TextContent,
    ToolUseContent,
    TranscriptEntry,
    summarize_tool_input,
)
from .search import MatchHit, SearchResult, SessionInfo, TriageResult
from .subagents import SubagentInfo
from .utils import iso_timestamp


# =============================================================================
# Entry display helper
# =============================================================================


def _entry_display(entry: TranscriptEntry, truncate: int = 500) -> str:
    """Call display() on entries that support it."""
    if isinstance(entry, BaseTranscriptEntry):
        return entry.display(truncate=truncate)
    return ""


# =============================================================================
# Search result formatting
# =============================================================================


def format_triage_results(all_results: list[tuple[str, TriageResult]]) -> dict[str, Any]:
    """Format triage/count results as structured dict, grouped by session."""
    total = sum(r.count for _, r in all_results)

    # Group by session_id, preserving insertion order
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for pat, r in all_results:
        sid = r.session.session_id
        if sid not in grouped:
            grouped[sid] = {
                "session_id": sid,
                "date": iso_timestamp(r.session.first_timestamp),
                "patterns": [],
            }
        grouped[sid]["patterns"].append({
            "pattern": pat,
            "hits": r.count,
            "example": r.first_match_example or r.session.title,
        })

    # Sort sessions by total hits descending
    sessions = sorted(
        grouped.values(),
        key=lambda s: sum(p["hits"] for p in s["patterns"]),
        reverse=True,
    )

    return {
        "total": total,
        "sessions": sessions,
    }


def _format_match(match: MatchHit) -> dict[str, Any]:
    """Format a single search match with context."""
    return {
        "session_id": match.session_id,
        "turn_id": match.turn_uuid,
        "context_before": [_entry_display(e) for e in match.context_before],
        "match": _entry_display(match.entry, truncate=0),
        "context_after": [_entry_display(e) for e in match.context_after],
    }


def format_search_results(result: SearchResult, pattern: str) -> dict[str, Any]:
    """Format search results — content mode or overflow with samples."""
    if result.overflow:
        return {
            "total": result.total_matches,
            "overflow": True,
            "sessions": [
                {
                    "session_id": r.session.session_id,
                    "date": iso_timestamp(r.session.first_timestamp),
                    "hits": r.count,
                    "example": r.first_match_example or r.session.title,
                }
                for r in result.per_session
            ],
            "sample_matches": [_format_match(m) for m in result.matches],
        }
    return {
        "total": result.total_matches,
        "matches": [_format_match(m) for m in result.matches],
    }


def format_quote(
    session_info: Optional[SessionInfo],
    turn: str,
    context: int,
    entries: list[TranscriptEntry],
) -> dict[str, Any]:
    """Format a quote view centered on a specific turn."""
    return {
        "session_id": session_info.session_id if session_info else None,
        "turn_id": turn,
        "context_size": context,
        "entries": [_entry_display(e, truncate=0) for e in entries],
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
