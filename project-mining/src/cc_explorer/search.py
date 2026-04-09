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
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    TranscriptStats,
    extract_output_text,
    extract_text,
    extract_thinking_text,
)
from .formatting import _match_example
from .parser import load_conversations, load_transcript
from .utils import PrefixId, smart_truncate


# =============================================================================
# Conversation role
# =============================================================================


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
    """Metadata about a conversation session.

    `worktree` is the git worktree name the session lived in, or None for
    the project's main worktree. Claude Desktop dispatch creates linked
    worktrees under `<project>/.claude-worktrees/<name>/`, so dispatched
    sessions come back labeled with their basename (e.g. 'happy-lehmann').
    """

    session_id: PrefixId
    path: Path
    title: str  # auto-generated from first human message
    first_timestamp: Optional[datetime]
    message_count: int
    stats: TranscriptStats = field(default_factory=TranscriptStats)
    worktree: Optional[str] = None


@dataclass
class TriageResult:
    """Match count for a single session."""

    session: SessionInfo
    count: int
    first_match_example: str = ""  # example excerpt from first matching entry


@dataclass
class MatchHit:
    """A single search match with surrounding context."""

    session_id: PrefixId
    turn_uuid: PrefixId
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

# Map role name to base entry types. ToolResultEntry is added dynamically
# via conversation_types_for() when outputs are visible and assistant is in scope.
ENTRY_TYPE_MAP: dict[str, tuple[type, ...]] = {
    "user": (HumanEntry,),
    "assistant": (AssistantTranscriptEntry,),
    "all": (HumanEntry, AssistantTranscriptEntry),
}


def conversation_types_for(
    hide: frozenset[str] = frozenset(),
    base_types: tuple[type, ...] = (HumanEntry, AssistantTranscriptEntry),
) -> tuple[type, ...]:
    """Determine entry types to include given `hide` and the caller's base types.

    ToolResultEntry is a consequence of assistant tool calls — it rides with
    assistant turns. Include it when:
      - 'outputs' is not hidden, AND
      - AssistantTranscriptEntry is in the base types (i.e., the caller wants
        the assistant side of the conversation).
    """
    if "outputs" not in hide and AssistantTranscriptEntry in base_types:
        return base_types + (ToolResultEntry,)
    return base_types


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
        return smart_truncate(first_line, 60)
    return "(empty session)"


def load_sessions(project_path: str) -> list[SessionInfo]:
    """Find and load all conversation sessions for a project.

    Returns SessionInfo list sorted by first_timestamp (newest first).
    """
    conversations = load_conversations(project_path)
    sessions: list[SessionInfo] = []

    for session_id, ref in conversations.items():
        entries = load_transcript(ref.path)
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
                path=ref.path,
                title=title,
                first_timestamp=first_ts,
                message_count=message_count,
                stats=stats,
                worktree=ref.worktree,
            )
        )

    # Sort newest first (None timestamps sort last)
    sessions.sort(key=lambda s: s.first_timestamp or datetime.min, reverse=True)
    return sessions


# =============================================================================
# Filtering helpers
# =============================================================================


def _entry_matches(
    entry: TranscriptEntry,
    pattern: re.Pattern,
    hide: frozenset[str] = frozenset(),
) -> bool:
    """Check if an entry's content matches the pattern.

    Search is exhaustive across all content categories not in `hide`:
    - HumanEntry: text (always searched)
    - AssistantTranscriptEntry: text + tool inputs (unless 'inputs' in hide)
      + thinking blocks (unless 'thinking' in hide)
    - ToolResultEntry: output content (unless 'outputs' in hide)
    """
    if isinstance(entry, HumanEntry):
        text = extract_text(entry)
        return bool(pattern.search(text))

    if isinstance(entry, AssistantTranscriptEntry):
        # Text always searched
        text = extract_text(entry)
        if text and pattern.search(text):
            return True
        if "inputs" not in hide:
            tool_text = extract_tool_text(entry)
            if tool_text and pattern.search(tool_text):
                return True
        if "thinking" not in hide:
            thinking = extract_thinking_text(entry)
            if thinking and pattern.search(thinking):
                return True
        return False

    if isinstance(entry, ToolResultEntry):
        if "outputs" in hide:
            return False
        output_text = extract_output_text(entry)
        return bool(output_text and pattern.search(output_text))

    return False


def _get_context(
    entries: list[TranscriptEntry],
    idx: int,
    context: int,
    base_types: tuple[type, ...],
    hide: frozenset[str] = frozenset(),
) -> tuple[list[TranscriptEntry], list[TranscriptEntry]]:
    """Get context entries around a match, filtered to visible conversation types."""
    conv_types = conversation_types_for(hide, base_types)

    before: list[TranscriptEntry] = []
    count = 0
    for i in range(idx - 1, -1, -1):
        if isinstance(entries[i], conv_types):
            before.insert(0, entries[i])
            count += 1
            if count >= context:
                break

    after: list[TranscriptEntry] = []
    count = 0
    for i in range(idx + 1, len(entries)):
        if isinstance(entries[i], conv_types):
            after.append(entries[i])
            count += 1
            if count >= context:
                break

    return before, after


# =============================================================================
# Core operations
# =============================================================================


# Kept as the single-pattern reference implementation — test_triage_multi.py
# uses it as the oracle in the equivalence test for triage_multi(). Delete
# only if that equivalence test goes away.
def triage(
    sessions: list[SessionInfo],
    pattern: str,
    base_types: tuple[type, ...] = (HumanEntry,),
    example_width: int = 150,
    hide: frozenset[str] = frozenset(),
) -> list[TriageResult]:
    """Count pattern matches per session. Returns sorted by hit count descending.

    Search is exhaustive across all content not in `hide`. `base_types` controls
    which sides of the conversation are considered (user / assistant / all);
    ToolResultEntry rides along when assistant is in scope and 'outputs' is not hidden.
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    search_types = conversation_types_for(hide, base_types)
    results: list[TriageResult] = []

    for session in sessions:
        entries = load_transcript(session.path)
        count = 0
        first_example = ""
        for entry in entries:
            if not isinstance(entry, search_types):
                continue
            if not isinstance(entry, BaseTranscriptEntry):
                continue
            if _entry_matches(entry, compiled, hide):
                count += 1
                if not first_example:
                    first_example = _match_example(
                        entry.display(truncate=0, hide=hide), compiled, width=example_width
                    )
        if count > 0:
            results.append(TriageResult(session=session, count=count, first_match_example=first_example))

    results.sort(key=lambda r: r.count, reverse=True)
    return results


def triage_multi(
    sessions: list[SessionInfo],
    patterns: list[str],
    base_types: tuple[type, ...] = (HumanEntry,),
    example_width: int = 150,
    hide: frozenset[str] = frozenset(),
) -> PatternTriageResults:
    """Count matches for multiple patterns in a single pass over each session.

    Loads each session's transcript once and checks all patterns per entry.
    Returns PatternTriageResults — same type consumed by SearchProjectResponse.from_triage.
    """
    compiled = [(pat, re.compile(pat, re.IGNORECASE)) for pat in patterns]
    search_types = conversation_types_for(hide, base_types)

    # Per-pattern accumulators: {pattern_index: {session_index: (count, first_example)}}
    accum: dict[int, dict[int, tuple[int, str]]] = {i: {} for i in range(len(compiled))}

    for si, session in enumerate(sessions):
        entries = load_transcript(session.path)
        for entry in entries:
            if not isinstance(entry, search_types):
                continue
            if not isinstance(entry, BaseTranscriptEntry):
                continue
            for pi, (_, regex) in enumerate(compiled):
                if _entry_matches(entry, regex, hide):
                    count, example = accum[pi].get(si, (0, ""))
                    if not example:
                        example = _match_example(
                            entry.display(truncate=0, hide=hide), regex, width=example_width
                        )
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


def search_multi(
    sessions: list[SessionInfo],
    patterns: list[str],
    *,
    base_types: tuple[type, ...] = (HumanEntry,),
    context: int = 1,
    max_results_per_pattern: int = 30,
    hide: frozenset[str] = frozenset(),
) -> dict[PrefixId, list[tuple[str, list[MatchHit], int]]]:
    """Search N patterns across N sessions in a single pass per session.

    Mirrors triage_multi's accumulator shape but holds full MatchHit objects
    (with surrounding context) instead of count-only TriageResult. Each
    session's transcript is loaded once and every pattern is checked against
    every entry — vs the alternative of looping the single-pattern `search()`
    which would re-walk the transcript N times per session.

    Returns: {session_id: [(pattern, matches, total_hits), ...]} where
    `matches` is capped at `max_results_per_pattern` per (session, pattern)
    cell and `total_hits` is the uncapped count for that cell so callers can
    surface overflow.
    """
    compiled = [(pat, re.compile(pat, re.IGNORECASE)) for pat in patterns]
    search_types = conversation_types_for(hide, base_types)

    out: dict[PrefixId, list[tuple[str, list[MatchHit], int]]] = {}

    for session in sessions:
        entries = load_transcript(session.path)
        # Per-pattern accumulator for this session: pi -> list[MatchHit]
        per_pattern: dict[int, list[MatchHit]] = {i: [] for i in range(len(compiled))}
        per_pattern_totals: dict[int, int] = {i: 0 for i in range(len(compiled))}

        for idx, entry in enumerate(entries):
            if not isinstance(entry, search_types):
                continue
            if not isinstance(entry, BaseTranscriptEntry):
                continue
            for pi, (_, regex) in enumerate(compiled):
                if not _entry_matches(entry, regex, hide):
                    continue
                per_pattern_totals[pi] += 1
                if len(per_pattern[pi]) >= max_results_per_pattern:
                    continue  # over the cap; only the total grows
                before, after = _get_context(entries, idx, context, base_types, hide)
                per_pattern[pi].append(
                    MatchHit(
                        session_id=session.session_id,
                        turn_uuid=PrefixId(getattr(entry, "uuid", "") or ""),
                        entry=entry,
                        context_before=before,
                        context_after=after,
                    )
                )

        out[session.session_id] = [
            (compiled[pi][0], per_pattern[pi], per_pattern_totals[pi])
            for pi in range(len(compiled))
        ]

    return out


def search(
    sessions: list[SessionInfo],
    pattern: str,
    base_types: tuple[type, ...] = (HumanEntry,),
    context: int = 1,
    session_id: str | None = None,
    max_results: int = 30,
    hide: frozenset[str] = frozenset(),
) -> SearchResult:
    """Search for pattern across sessions. Returns matching entries with context.

    Search is exhaustive across all content not in `hide`. `base_types` controls
    which sides of the conversation are considered; ToolResultEntry rides along
    when assistant is in scope and 'outputs' is not hidden.

    When matches exceed max_results: returns overflow response with a sample
    of hits spread across sessions plus per-session counts (triage data).
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    search_types = conversation_types_for(hide, base_types)
    all_matches: list[MatchHit] = []
    per_session_counts: list[TriageResult] = []

    target_sessions = sessions
    if session_id:
        target_sessions = [s for s in sessions if s.session_id == session_id]

    for session in target_sessions:
        entries = load_transcript(session.path)
        session_matches: list[MatchHit] = []

        for idx, entry in enumerate(entries):
            if not isinstance(entry, search_types):
                continue
            if not _entry_matches(entry, compiled, hide):
                continue

            before, after = _get_context(entries, idx, context, base_types, hide)
            session_matches.append(
                MatchHit(
                    session_id=session.session_id,
                    turn_uuid=PrefixId(getattr(entry, "uuid", "") or ""),
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
    hide: frozenset[str] = frozenset(),
    session_id: str | None = None,
) -> tuple[SessionInfo | None, list[TranscriptEntry]]:
    """Find a turn by UUID across sessions and return surrounding entries.

    Turn UUIDs are globally unique — session_id is optional and used only to
    narrow the search when the caller wants to be explicit.

    Returns (session_info, entries) where entries includes context.
    """
    conv_types = conversation_types_for(hide)

    target_sessions = sessions
    if session_id:
        target_sessions = [s for s in sessions if s.session_id == session_id]

    for session in target_sessions:
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
                if isinstance(entries[i], conv_types):
                    before_start = i
                    count += 1
                    if count >= context:
                        break

            # Collect from before_start through context after
            # Always include the target turn even if it's not in conv_types
            count_after = 0
            for i in range(before_start, len(entries)):
                if i == idx or isinstance(entries[i], conv_types):
                    result.append(entries[i])
                    if i > idx:
                        count_after += 1
                        if count_after >= context:
                            break

            return session, result

    return None, []


def browse_session_turns(
    session: SessionInfo,
    position: str,
    turns: int = 10,
    anchor_turn: str | None = None,
    entry_types: tuple[type, ...] = (HumanEntry, AssistantTranscriptEntry),
) -> tuple[list[TranscriptEntry], int]:
    """Return first or last N conversation turns from a session.

    Filters to entry_types (default: HumanEntry + AssistantTranscriptEntry).
    If anchor_turn is set, tail reads forward from anchor, head reads up to anchor.
    Returns (sliced_entries, total_conversation_turns).
    """
    entries = load_transcript(session.path)
    conversation = [e for e in entries if isinstance(e, entry_types)]
    total = len(conversation)

    if anchor_turn:
        anchor_idx = None
        for i, e in enumerate(conversation):
            if isinstance(e, BaseTranscriptEntry) and e.uuid == anchor_turn:
                anchor_idx = i
                break
        if anchor_idx is None:
            return [], total

        if position == "tail":
            sliced = conversation[anchor_idx : anchor_idx + turns]
        else:
            start = max(0, anchor_idx - turns + 1)
            sliced = conversation[start : anchor_idx + 1]
    else:
        if position == "tail":
            sliced = conversation[-turns:]
        else:
            sliced = conversation[:turns]

    return sliced, total
