"""Search, filter, and triage operations on parsed transcript entries.

Operates on typed entries — filenames are implementation details. The
interface uses session IDs (UUID from filename) and turn UUIDs (the uuid
field on each entry).
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    ToolUseContent,
    TranscriptEntry,
    TranscriptStats,
)
from .models import extract_text
from .parser import load_conversations, load_transcript


# =============================================================================
# Scope enum and tool text extraction
# =============================================================================


class ScopeType(str, Enum):
    messages = "messages"
    tools = "tools"
    all = "all"


class ConversationRole(str, Enum):
    user = "user"
    assistant = "assistant"
    all = "all"


# =============================================================================
# Project resolution
# =============================================================================


def resolve_project(project: Optional[str] = None) -> str:
    """Resolve project path: explicit value or CWD.

    Accepts:
    - None → CWD
    - Full/relative path → resolved as-is
    - Bare name (no slashes) → expanded to ~/projects/<name> if that directory exists
    """
    if not project:
        return str(Path.cwd())

    if "/" not in project and "\\" not in project:
        expanded = Path.home() / "projects" / project
        if expanded.exists():
            return str(expanded)

    return project



# Map tool names to the input keys that contain searchable text
_TOOL_TEXT_KEYS: dict[str, list[str]] = {
    "Bash": ["command", "description"],
    "Read": ["file_path"],
    "Edit": ["file_path"],
    "Write": ["file_path"],
    "Glob": ["pattern", "path"],
    "Grep": ["pattern", "path"],
    "Agent": ["prompt", "description"],
    "Task": ["prompt", "description"],
    "TaskCreate": ["prompt", "description"],
    "WebFetch": ["url"],
    "WebSearch": ["query"],
}


def extract_tool_text(entry: AssistantTranscriptEntry) -> str:
    """Extract searchable text from tool_use blocks in an assistant entry.

    Walks ToolUseContent items and pulls text from known input fields.
    Unknown tools get all string values from their input dict.
    """
    parts: list[str] = []
    for item in entry.message.content:
        if not isinstance(item, ToolUseContent):
            continue
        parts.append(item.name)
        keys = _TOOL_TEXT_KEYS.get(item.name)
        if keys:
            for key in keys:
                val = item.input.get(key)
                if isinstance(val, str):
                    parts.append(val)
        else:
            # Unknown tool: grab all string values
            for val in item.input.values():
                if isinstance(val, str):
                    parts.append(val)
    return "\n".join(parts)


# =============================================================================
# Data classes
# =============================================================================


@dataclass
class SessionInfo:
    """Metadata about a conversation session."""

    session_id: str
    path: Path
    title: str  # auto-generated from first human message
    first_timestamp: Optional[datetime]
    message_count: int
    stats: TranscriptStats = field(default_factory=TranscriptStats)


@dataclass
class TriageResult:
    """Match count for a single session."""

    session: SessionInfo
    count: int
    first_match_example: str = ""  # example excerpt from first matching entry


@dataclass
class MatchHit:
    """A single search match with surrounding context."""

    session_id: str
    turn_uuid: str
    entry: TranscriptEntry
    context_before: list[TranscriptEntry]
    context_after: list[TranscriptEntry]


@dataclass
class SearchResult:
    """Results from a search operation."""

    pattern: str
    matches: list[MatchHit]
    overflow: bool = False
    total_matches: int = 0
    per_session: list[TriageResult] = field(default_factory=list)


# Pattern string paired with per-session triage results
PatternTriageResults = list[tuple[str, list[TriageResult]]]


# =============================================================================
# Entry type mapping
# =============================================================================

# Map string names to entry type tuples for scope filtering
ENTRY_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "user": (HumanEntry,),
    "human": (HumanEntry,),  # legacy alias
    "assistant": (AssistantTranscriptEntry,),
    "all": (HumanEntry, AssistantTranscriptEntry),
}


# =============================================================================
# Session loading
# =============================================================================


def session_title(entries: list[TranscriptEntry]) -> str:
    """Extract title from first non-meta, non-tool-result human message.

    Truncates to ~60 chars. Strips XML wrappers (skill invocations).
    """
    for entry in entries:
        if not isinstance(entry, HumanEntry):
            continue
        text = extract_text(entry)
        if not text:
            continue
        # Strip leading XML-like content (skill invocations)
        text = re.sub(r"^<[^>]+>[\s\S]*?</[^>]+>\s*", "", text)
        text = text.strip()
        if not text:
            continue
        # Single line, truncated
        first_line = text.split("\n")[0].strip()
        if len(first_line) > 60:
            return first_line[:57] + "..."
        return first_line
    return "(empty session)"


def load_sessions(project_path: str) -> list[SessionInfo]:
    """Find and load all conversation sessions for a project.

    Returns SessionInfo list sorted by first_timestamp (newest first).
    """
    conversations = load_conversations(project_path)
    sessions: list[SessionInfo] = []

    for session_id, path in conversations.items():
        entries = load_transcript(path)
        if not entries:
            continue

        # Count meaningful messages — entries with actual content
        message_count = sum(
            1
            for e in entries
            if isinstance(e, (HumanEntry, AssistantTranscriptEntry))
            and len(e.display(truncate=0)) > 0
        )
        if message_count == 0:
            continue

        # Find first timestamp from any entry that has one (typed access)
        first_ts: Optional[datetime] = None
        for e in entries:
            if isinstance(e, BaseTranscriptEntry):
                first_ts = e.timestamp
                break

        title = session_title(entries)
        stats = TranscriptStats.from_entries(entries)
        sessions.append(
            SessionInfo(
                session_id=session_id,
                path=path,
                title=title,
                first_timestamp=first_ts,
                message_count=message_count,
                stats=stats,
            )
        )

    # Sort newest first (None timestamps sort last)
    sessions.sort(key=lambda s: s.first_timestamp or datetime.min, reverse=True)
    return sessions


# =============================================================================
# Filtering helpers
# =============================================================================


def _match_example(text: str, pattern: re.Pattern, width: int = 150) -> str:
    """Extract an example excerpt starting at the first match within text.

    Centers the window so the match start is visible (not the midpoint of a
    greedy span). Snaps slice boundaries to word breaks to avoid fragments.
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


def _entry_matches(
    entry: TranscriptEntry,
    pattern: re.Pattern,
    scope: ScopeType = ScopeType.messages,
) -> bool:
    """Check if an entry's text content matches the pattern.

    Scope controls what's searched:
    - messages: text content only (default, backward-compatible)
    - tools: tool_use input fields only (Bash commands, file paths, etc.)
    - all: both text content and tool inputs
    """
    if not isinstance(entry, (HumanEntry, AssistantTranscriptEntry)):
        return False

    check_messages = scope in (ScopeType.messages, ScopeType.all)
    check_tools = scope in (ScopeType.tools, ScopeType.all)

    if check_messages:
        text = extract_text(entry)
        if pattern.search(text):
            return True

    if check_tools and isinstance(entry, AssistantTranscriptEntry):
        tool_text = extract_tool_text(entry)
        if pattern.search(tool_text):
            return True

    return False


def _get_context(
    entries: list[TranscriptEntry],
    idx: int,
    context: int,
    entry_types: tuple[type, ...],
) -> tuple[list[TranscriptEntry], list[TranscriptEntry]]:
    """Get context entries around a match, filtered to conversation types."""
    # For context, include both human and assistant entries regardless of search type
    conversation_types = (HumanEntry, AssistantTranscriptEntry)

    before: list[TranscriptEntry] = []
    count = 0
    for i in range(idx - 1, -1, -1):
        if isinstance(entries[i], conversation_types):
            before.insert(0, entries[i])
            count += 1
            if count >= context:
                break

    after: list[TranscriptEntry] = []
    count = 0
    for i in range(idx + 1, len(entries)):
        if isinstance(entries[i], conversation_types):
            after.append(entries[i])
            count += 1
            if count >= context:
                break

    return before, after


# =============================================================================
# Core operations
# =============================================================================


def triage(
    sessions: list[SessionInfo],
    pattern: str,
    entry_types: tuple[type, ...] = (HumanEntry,),
    example_width: int = 150,
    scope: ScopeType = ScopeType.messages,
) -> list[TriageResult]:
    """Count pattern matches per session. Returns sorted by hit count descending."""
    compiled = re.compile(pattern, re.IGNORECASE)
    results: list[TriageResult] = []

    for session in sessions:
        entries = load_transcript(session.path)
        count = 0
        first_example = ""
        for entry in entries:
            if isinstance(entry, entry_types) and _entry_matches(entry, compiled, scope):
                count += 1
                if not first_example and isinstance(entry, (HumanEntry, AssistantTranscriptEntry)):
                    first_example = _match_example(entry.display(truncate=0), compiled, width=example_width)
        if count > 0:
            results.append(TriageResult(session=session, count=count, first_match_example=first_example))

    results.sort(key=lambda r: r.count, reverse=True)
    return results


def triage_multi(
    sessions: list[SessionInfo],
    patterns: list[str],
    entry_types: tuple[type, ...] = (HumanEntry,),
    example_width: int = 150,
    scope: ScopeType = ScopeType.messages,
) -> PatternTriageResults:
    """Count matches for multiple patterns in a single pass over each session.

    Loads each session's transcript once and checks all patterns per entry.
    Returns PatternTriageResults — same type consumed by SearchProjectResponse.from_triage.
    """
    compiled = [(pat, re.compile(pat, re.IGNORECASE)) for pat in patterns]

    # Per-pattern accumulators: {pattern_index: {session_index: (count, first_example)}}
    accum: dict[int, dict[int, tuple[int, str]]] = {i: {} for i in range(len(compiled))}

    for si, session in enumerate(sessions):
        entries = load_transcript(session.path)
        for entry in entries:
            if not isinstance(entry, entry_types):
                continue
            for pi, (_, regex) in enumerate(compiled):
                if _entry_matches(entry, regex, scope):
                    count, example = accum[pi].get(si, (0, ""))
                    if not example and isinstance(entry, (HumanEntry, AssistantTranscriptEntry)):
                        example = _match_example(entry.display(truncate=0), regex, width=example_width)
                    accum[pi][si] = (count + 1, example)

    results: PatternTriageResults = []
    for pi, (pat, _) in enumerate(compiled):
        session_results: list[TriageResult] = []
        for si, (count, example) in accum[pi].items():
            session_results.append(
                TriageResult(session=sessions[si], count=count, first_match_example=example)
            )
        session_results.sort(key=lambda r: r.count, reverse=True)
        results.append((pat, session_results))

    return results


def search(
    sessions: list[SessionInfo],
    pattern: str,
    entry_types: tuple[type, ...] = (HumanEntry,),
    context: int = 1,
    session_id: str | None = None,
    max_results: int = 30,
    scope: ScopeType = ScopeType.messages,
) -> SearchResult:
    """Search for pattern across sessions. Returns matching entries with context.

    When matches exceed max_results: returns overflow response with a sample
    of hits spread across sessions plus per-session counts (triage data).
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    all_matches: list[MatchHit] = []
    per_session_counts: list[TriageResult] = []

    target_sessions = sessions
    if session_id:
        target_sessions = [s for s in sessions if s.session_id == session_id]

    for session in target_sessions:
        entries = load_transcript(session.path)
        session_matches: list[MatchHit] = []

        for idx, entry in enumerate(entries):
            if not isinstance(entry, entry_types):
                continue
            if not _entry_matches(entry, compiled, scope):
                continue

            before, after = _get_context(entries, idx, context, entry_types)
            session_matches.append(
                MatchHit(
                    session_id=session.session_id,
                    turn_uuid=getattr(entry, "uuid", ""),
                    entry=entry,
                    context_before=before,
                    context_after=after,
                )
            )

        if session_matches:
            per_session_counts.append(
                TriageResult(session=session, count=len(session_matches))
            )
            all_matches.extend(session_matches)

    per_session_counts.sort(key=lambda r: r.count, reverse=True)
    total = len(all_matches)

    if total <= max_results:
        return SearchResult(
            pattern=pattern,
            matches=all_matches,
            overflow=False,
            total_matches=total,
            per_session=per_session_counts,
        )

    # Overflow: sample hits spread across sessions
    sample: list[MatchHit] = []
    # Take up to 2 from each session, round-robin
    per_session_limit = max(1, max_results // len(per_session_counts))
    session_match_map: dict[str, list[MatchHit]] = {}
    for m in all_matches:
        session_match_map.setdefault(m.session_id, []).append(m)

    for sid, matches in session_match_map.items():
        sample.extend(matches[:per_session_limit])
        if len(sample) >= max_results:
            break

    return SearchResult(
        pattern=pattern,
        matches=sample[:max_results],
        overflow=True,
        total_matches=total,
        per_session=per_session_counts,
    )


def get_turn_context(
    sessions: list[SessionInfo],
    turn_uuid: str,
    context: int = 3,
) -> tuple[SessionInfo | None, list[TranscriptEntry]]:
    """Find a turn by UUID across all sessions and return surrounding entries.

    Turn UUIDs are globally unique — no need to specify session.
    Returns (session_info, entries) where entries includes context.
    """
    conversation_types = (HumanEntry, AssistantTranscriptEntry)

    for session in sessions:
        entries = load_transcript(session.path)
        for idx, entry in enumerate(entries):
            if not isinstance(entry, BaseTranscriptEntry):
                continue
            if entry.uuid != turn_uuid:
                continue

            # Found it — gather context
            result: list[TranscriptEntry] = []

            # Before
            count = 0
            before_start = idx
            for i in range(idx - 1, -1, -1):
                if isinstance(entries[i], conversation_types):
                    before_start = i
                    count += 1
                    if count >= context:
                        break

            # Collect from before_start through context after
            count_after = 0
            for i in range(before_start, len(entries)):
                if isinstance(entries[i], conversation_types):
                    result.append(entries[i])
                    if i > idx:
                        count_after += 1
                        if count_after >= context:
                            break

            return session, result

    return None, []
