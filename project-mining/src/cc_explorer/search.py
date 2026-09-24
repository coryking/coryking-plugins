"""Search, filter, and triage operations on parsed transcript entries.

Operates on typed entries — filenames are implementation details. The
interface uses session IDs (UUID from filename) and turn UUIDs (the uuid
field on each entry).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from functools import cached_property
from pathlib import Path
from typing import Iterable, Optional, TypeGuard

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    TranscriptStats,
    UserOrigin,
    extract_output_text,
    extract_text,
    extract_thinking_text,
    substantive_human_text,
)
from .corpus import EPOCH as _EPOCH, Corpus, SessionRef, TranscriptSource
from .formatting import _match_example
from .conversion import read_provenance
from .parser import load_transcript
from .providers import Harness, provider_for
from .subagents import collect_agent_files, discover_subagents, resolve_subagents_dir
from .utils import smart_truncate
from .identifiers import ambiguous_id, id_matches, matching_ids


# =============================================================================
# Conversation role
# =============================================================================


class ConversationRole(str, Enum):
    user = "user"
    assistant = "assistant"
    all = "all"


def sort_sessions_newest_first(sessions: list["SessionInfo"]) -> None:
    """Sort sessions in place, newest first; None timestamps sort last."""
    sessions.sort(key=lambda s: s.first_timestamp or _EPOCH, reverse=True)


# =============================================================================
# Search corpus — a session's main transcript plus its subagent transcripts.
# (TranscriptSource itself lives in corpus.py, the identity layer.)
# =============================================================================


def session_sources(
    session: Path | SessionInfo | SessionRef,
) -> list["TranscriptSource"]:
    """Expand a session's transcript path into its whole searchable corpus.

    Takes a bare path, not a SessionInfo: the expansion is a pure filesystem
    walk and nothing in it needs a parsed session, so `failures.py` can reach it
    straight off a `SessionRef` instead of re-deriving agent ids from filenames.
    Callers holding a SessionInfo pass `session.path`.

    The main transcript plus every subagent transcript on disk. We use
    `collect_agent_files` (a pure filesystem walk over `subagents/`, including
    `workflows/<runId>/`) rather than the full `discover_subagents` reconciliation
    — search only needs the transcript *files*, not the dispatch graph, and this
    avoids re-reading the parent transcript. This is what makes a subagent's
    internal activity searchable (#22), not just the result text the parent
    recorded.

    Conversion artifacts (agents whose jsonl carries an x-converter-provenance
    line, i.e. a session/subagent copied via convert_session) are SKIPPED: their
    text is a duplicate of a real transcript already in the corpus, so searching
    them would double-count and surface synthetic copies. They remain visible in
    list_session_agents (labeled), just not searched.
    """
    if isinstance(session, Path):
        transcript_path = session
        harness = Harness.claude
        paths = (session,)
    else:
        transcript_path = session.path
        harness = session.harness
        paths = session.paths or (session.path,)
    sources: list[TranscriptSource] = [TranscriptSource(
        agent_id=None,
        path=transcript_path,
        harness=harness,
        paths=paths,
    )]
    if harness is Harness.codex:
        return sources
    for af in collect_agent_files(resolve_subagents_dir(transcript_path)):
        if af.is_conversion_artifact:
            continue
        sources.append(
            TranscriptSource(
                agent_id=af.agent_id if af.agent_id else None,
                path=af.path,
            )
        )
    return sources


def load_source_transcript(source: TranscriptSource) -> list[TranscriptEntry]:
    """Parse one logical source through the harness that owns its wire format."""
    return provider_for(source.harness).load_transcript(
        source.paths or (source.path,)
    )



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

    session_id: str
    path: Path
    title: str  # auto-generated from first human message
    first_timestamp: Optional[datetime]
    message_count: int
    stats: TranscriptStats = field(default_factory=TranscriptStats)
    worktree: Optional[str] = None
    # The project this session was loaded under (the path passed to load_sessions,
    # i.e. the canonical repo root for worktree-pooled sessions). Carries project
    # provenance through search so cross-project results can name where each hit
    # lives without the tool layer re-threading it.
    project_path: Optional[str] = None
    # Number of human prompts (entries where the user actually spoke), distinct
    # from message_count which also counts assistant turns. Signals how much the
    # human drove the session vs one prompt fanning out into a long agent run.
    # Teammate-injected turns (agent-team orchestration DMs) are EXCLUDED — they
    # are not human attention; counting them would inflate the "single prompt
    # fanned out" signal for worker sessions.
    user_turns: int = 0
    # Agent-team membership (teamName/agentName), stamped on every entry of a
    # team-worker session. None outside agent-team sessions.
    team: Optional[str] = None
    team_role: Optional[str] = None
    # True when this session is a conversion artifact (x-converter-provenance line
    # present). Populated at load time via a cheap head-scan; used to skip
    # artifacts from search/triage (same rationale as agent-shaped skip) while
    # still listing them in session list responses (labeled).
    is_conversion_artifact: bool = False
    # Harness provenance and the physical files composing this logical session.
    harness: Harness = Harness.claude
    paths: tuple[Path, ...] = ()

    @cached_property
    def agents_present(self) -> int:
        """Full discovered subagent population — parent dispatches plus on-disk
        orphans (notably workflow-orchestrated agents). Equals list_session_agents'
        total_agents, unlike stats.agent_count which is top-down dispatches only.

        Lazy: costs a subagents-dir walk (plus a cached transcript read) the
        first time it's touched, so only tools that display or filter on it
        (list_project_sessions) ever pay for it. Conversion artifacts are
        excluded — they are copies, not dispatched runs.
        """
        if self.harness is Harness.codex:
            # Codex child sessions are independent rollouts linked by metadata,
            # not Claude-style nested transcript files.
            return 0
        try:
            return sum(
                1
                for sa in discover_subagents(self.path)
                if not sa.is_conversion_artifact
            )
        except OSError:
            return 0

    @classmethod
    def load(cls, ref: SessionRef) -> Optional["SessionInfo"]:
        """Promote a SessionRef to a fully-derived SessionInfo — THE promotion path.

        Parses exactly one session's transcript (through the bounded cache).
        Returns None for an empty or unreadable session — the sessions
        load_sessions has always skipped. The memory-bounding invariant lives
        at the call sites: nothing holds list[SessionInfo] for an unbounded
        scope; hold SessionRefs and promote only the refs a prefilter or an
        explicit id selected.
        """
        try:
            entries = provider_for(ref.harness).load_transcript(
                ref.paths or (ref.path,)
            )
        except OSError:
            return None
        if not entries:
            return None

        # Count meaningful messages — entries with actual content
        message_count = sum(
            1
            for e in entries
            if isinstance(e, (HumanEntry, AssistantTranscriptEntry))
            and len(e.display(truncate=0)) > 0
        )
        if message_count == 0:
            return None

        # Human prompts only — how many times the user actually spoke. Teammate
        # DMs (orchestration) and interrupt sentinels (mid-turn esc, not a
        # prompt) are user-role turns but not human attention, so both are
        # excluded — consistent with the activity timeline's human_turns.
        user_turns = sum(
            1
            for e in entries
            if isinstance(e, HumanEntry)
            and len(e.display(truncate=0)) > 0
            and e.origin not in (UserOrigin.teammate, UserOrigin.interrupt)
        )

        # Find first timestamp and agent-team membership. first_ts is the first
        # timestamped entry. Team identity is stamped on every entry of a worker
        # session, but the leading entries (summaries, early system records) can
        # lack it, so scan until the first non-null teamName/agentName rather
        # than trusting entry zero (mirrors activity.py's _scan).
        first_ts: Optional[datetime] = None
        team: Optional[str] = None
        team_role: Optional[str] = None
        for e in entries:
            if not isinstance(e, BaseTranscriptEntry):
                continue
            if first_ts is None:
                first_ts = e.timestamp
            if team is None and e.teamName:
                team = e.teamName
            if team_role is None and e.agentName:
                team_role = e.agentName
            if first_ts is not None and team is not None and team_role is not None:
                break

        return cls(
            session_id=ref.session_id,
            path=ref.path,
            title=session_title(entries),
            first_timestamp=first_ts,
            message_count=message_count,
            stats=TranscriptStats.from_entries(entries),
            worktree=ref.worktree,
            user_turns=user_turns,
            team=team,
            team_role=team_role,
            project_path=ref.project_path,
            # Cheap head-scan: is this session a conversion artifact?
            is_conversion_artifact=read_provenance(ref.path) is not None,
            harness=ref.harness,
            paths=ref.paths or (ref.path,),
        )


@dataclass
class TriageResult:
    """Match count for a single session.

    `agent_id` records where the first match was found: None for the main
    transcript, or the subagent id when the first hit was inside a subagent body
    (the count itself sums across the session's whole corpus).
    """

    session: SessionInfo
    count: int
    first_match_example: str = ""  # example excerpt from first matching entry
    agent_id: Optional[str] = None


SearchableEntry = HumanEntry | AssistantTranscriptEntry | ToolResultEntry


@dataclass
class MatchHit:
    """A single search match with surrounding context.

    `agent_id` is None when the match is in the main session transcript and the
    subagent id when it's inside a subagent body; context turns are drawn from
    the same transcript the match came from.
    """

    session_id: str
    turn_uuid: str
    entry: SearchableEntry
    context_before: list[TranscriptEntry]
    context_after: list[TranscriptEntry]
    agent_id: Optional[str] = None


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


def _is_searchable(
    entry: TranscriptEntry, search_types: tuple[type, ...]
) -> TypeGuard[SearchableEntry]:
    """isinstance against the dynamic search-type tuple, narrowed for the checker.

    `search_types` is built at runtime by conversation_types_for(), so a static
    checker can't narrow from `isinstance(entry, search_types)`. But every
    searchable entry kind is HumanEntry, AssistantTranscriptEntry, or
    ToolResultEntry, so a match proves that concrete union. This TypeGuard
    states the invariant once, letting the search loops retain the type detail
    needed by matching and result construction.
    """
    return isinstance(entry, search_types)


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
    """Extract title from the first human turn that carries substantive text.

    Routes through `substantive_human_text` (the single source of truth for
    "what did the human actually say"): bare slash commands, command stdout,
    caveats, and interrupt sentinels all reduce to '' and are skipped, while a
    real prompt — including one recovered from `<command-args>` — wins. Truncates
    to ~60 chars.
    """
    for entry in entries:
        if not isinstance(entry, HumanEntry):
            continue
        text = substantive_human_text(entry)
        if not text:
            continue
        first_line = text.split("\n")[0].strip()
        return smart_truncate(first_line, 60)
    return "(empty session)"


def promote_refs(refs: Iterable[SessionRef]) -> list[SessionInfo]:
    """Promote refs to parsed sessions, dropping empty/unreadable ones.

    The bulk form of `SessionInfo.load` — every promotion of more than one ref
    goes through here, so the "load returned None means skip it" convention
    lives in one place.
    """
    return [info for ref in refs if (info := SessionInfo.load(ref)) is not None]


def load_sessions(project_path: str) -> list[SessionInfo]:
    """Find and load all conversation sessions for a project.

    A thin wrapper over discovery + promotion: `Corpus.discover` lists the
    project's sessions by filename (worktrees pooled), `promote_refs` parses
    each one. Returns SessionInfo list sorted by first_timestamp
    (newest first). This parses the whole project — right for
    list_project_sessions (whose job is the project inventory); scoped tools
    should narrow to refs first and promote only what they need.
    """
    sessions = promote_refs(Corpus.discover([project_path]).refs)
    sort_sessions_newest_first(sessions)
    return sessions


# =============================================================================
# Filtering helpers
# =============================================================================


def search_types_for(
    hide: frozenset[str],
    base_types: tuple[type, ...],
    errors_only: bool = False,
) -> tuple[type, ...]:
    """The entry types a search should MATCH against.

    Normally `conversation_types_for` (role + hide). With `errors_only` the
    answer collapses to ToolResultEntry: only a tool result can carry a failure,
    so restricting the match set is both the filter and the optimization. `role`
    still governs the CONTEXT turns around each hit — you asked to find failures,
    not to stop seeing the conversation they happened in.
    """
    if errors_only:
        return (ToolResultEntry,)
    return conversation_types_for(hide, base_types)


def context_types_for(
    hide: frozenset[str],
    base_types: tuple[type, ...],
    errors_only: bool = False,
) -> tuple[type, ...]:
    """The entry types the CONTEXT around a hit is drawn from.

    Normally just `conversation_types_for`. With `errors_only` the assistant
    side is forced in regardless of `role`: the one turn that explains a failed
    tool result is the assistant `tool_use` that caused it, and at the default
    role='user' the context types are (HumanEntry,) — so the causing call is
    structurally unreachable. Measured over the live corpus (n=11,576 failed
    results), the causing assistant turn is a median of 1 entry back, while the
    nearest preceding human turn is a median of 8 (p90 81, max 677) and for 24
    of them does not exist at all. So role='user' context was showing unrelated
    conversation, or nothing.
    """
    if errors_only and AssistantTranscriptEntry not in base_types:
        base_types = base_types + (AssistantTranscriptEntry,)
    return conversation_types_for(hide, base_types)


def _entry_matches(
    entry: SearchableEntry,
    pattern: re.Pattern,
    hide: frozenset[str] = frozenset(),
    errors_only: bool = False,
) -> bool:
    """Check if an entry's content matches the pattern.

    Search is exhaustive across all content categories not in `hide`:
    - HumanEntry: text (always searched)
    - AssistantTranscriptEntry: text + tool inputs (unless 'inputs' in hide)
      + thinking blocks (unless 'thinking' in hide)
    - ToolResultEntry: output content (unless 'outputs' in hide)

    With `errors_only`, an entry matches only if it is a FAILED tool result
    (`ToolResultEntry.has_failure`) AND the pattern hits its output — so the
    pattern narrows the topic instead of having to guess the error's wording.
    `has_failure`, not `failure`: this runs once per entry PER PATTERN, and the
    kind is thrown away, so classifying here would run the rule table N times
    for an answer nobody reads.
    """
    if errors_only:
        if not isinstance(entry, ToolResultEntry) or not entry.has_failure:
            return False
        output_text = extract_output_text(entry)
        return bool(output_text and pattern.search(output_text))

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
    errors_only: bool = False,
) -> tuple[list[TranscriptEntry], list[TranscriptEntry]]:
    """Get context entries around a match, filtered to visible conversation types."""
    if context <= 0:
        return [], []
    conv_types = context_types_for(hide, base_types, errors_only)

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
    errors_only: bool = False,
) -> list[TriageResult]:
    """Count pattern matches per session. Returns sorted by hit count descending.

    Search is exhaustive across all content not in `hide`. `base_types` controls
    which sides of the conversation are considered (user / assistant / all);
    ToolResultEntry rides along when assistant is in scope and 'outputs' is not hidden.
    `errors_only` restricts matches to failed tool results.
    """
    compiled = re.compile(pattern, re.IGNORECASE)
    search_types = search_types_for(hide, base_types, errors_only)
    results: list[TriageResult] = []

    for session in sessions:
        if session.is_conversion_artifact:
            continue
        count = 0
        first_example = ""
        first_agent_id: Optional[str] = None
        for source in session_sources(session):
            entries = load_source_transcript(source)
            for entry in entries:
                if not _is_searchable(entry, search_types):
                    continue
                if _entry_matches(entry, compiled, hide, errors_only):
                    count += 1
                    if not first_example:
                        first_example = _match_example(
                            entry.display(truncate=0, hide=hide), compiled, width=example_width
                        )
                        first_agent_id = source.agent_id
        if count > 0:
            results.append(
                TriageResult(
                    session=session,
                    count=count,
                    first_match_example=first_example,
                    agent_id=first_agent_id,
                )
            )

    results.sort(key=lambda r: r.count, reverse=True)
    return results


def triage_multi(
    sessions: list[SessionInfo],
    patterns: list[str],
    base_types: tuple[type, ...] = (HumanEntry,),
    example_width: int = 150,
    hide: frozenset[str] = frozenset(),
    errors_only: bool = False,
) -> PatternTriageResults:
    """Count matches for multiple patterns in a single pass over each session.

    Loads each session's transcript once and checks all patterns per entry.
    Returns PatternTriageResults — same type consumed by SearchProjectsResponse.from_triage.
    `errors_only` restricts matches to failed tool results.
    """
    compiled = [(pat, re.compile(pat, re.IGNORECASE)) for pat in patterns]
    search_types = search_types_for(hide, base_types, errors_only)

    # Per-pattern accumulators: {pattern_index: {session_index: (count, first_example, first_agent_id)}}
    accum: dict[int, dict[int, tuple[int, str, Optional[str]]]] = {
        i: {} for i in range(len(compiled))
    }

    for si, session in enumerate(sessions):
        if session.is_conversion_artifact:
            continue
        # Search the whole corpus: main transcript + every subagent body (#22).
        for source in session_sources(session):
            entries = load_source_transcript(source)
            for entry in entries:
                if not _is_searchable(entry, search_types):
                    continue
                for pi, (_, regex) in enumerate(compiled):
                    if _entry_matches(entry, regex, hide, errors_only):
                        count, example, agent_id = accum[pi].get(si, (0, "", None))
                        if not example:
                            example = _match_example(
                                entry.display(truncate=0, hide=hide), regex, width=example_width
                            )
                            agent_id = source.agent_id
                        accum[pi][si] = (count + 1, example, agent_id)

    results: PatternTriageResults = []
    for pi, (pat, _) in enumerate(compiled):
        session_results: list[TriageResult] = []
        for si, (count, example, agent_id) in accum[pi].items():
            session_results.append(
                TriageResult(
                    session=sessions[si],
                    count=count,
                    first_match_example=example,
                    agent_id=agent_id,
                )
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
    errors_only: bool = False,
) -> dict[str, list[tuple[str, list[MatchHit], int]]]:
    """Search N patterns across N sessions in a single pass per session.

    Mirrors triage_multi's accumulator shape but holds full MatchHit objects
    (with surrounding context) instead of count-only TriageResult. Each
    session's transcript is loaded once and every pattern is checked against
    every entry — vs the alternative of looping the single-pattern `search()`
    which would re-walk the transcript N times per session.

    Returns: {session_id: [(pattern, matches, total_hits), ...]} where
    `matches` is capped at `max_results_per_pattern` per (session, pattern)
    cell and `total_hits` is the uncapped count for that cell so callers can
    surface overflow. `errors_only` restricts matches to failed tool results.
    """
    compiled = [(pat, re.compile(pat, re.IGNORECASE)) for pat in patterns]
    search_types = search_types_for(hide, base_types, errors_only)

    out: dict[str, list[tuple[str, list[MatchHit], int]]] = {}

    for session in sessions:
        if session.is_conversion_artifact:
            continue
        # Per-pattern accumulator for this session: pi -> list[MatchHit]
        per_pattern: dict[int, list[MatchHit]] = {i: [] for i in range(len(compiled))}
        per_pattern_totals: dict[int, int] = {i: 0 for i in range(len(compiled))}

        # Walk the whole corpus: main transcript + every subagent body (#22).
        # Context is drawn from within the same source the match came from.
        for source in session_sources(session):
            entries = load_source_transcript(source)
            for idx, entry in enumerate(entries):
                if not _is_searchable(entry, search_types):
                    continue
                for pi, (_, regex) in enumerate(compiled):
                    if not _entry_matches(entry, regex, hide, errors_only):
                        continue
                    per_pattern_totals[pi] += 1
                    if len(per_pattern[pi]) >= max_results_per_pattern:
                        continue  # over the cap; only the total grows
                    before, after = _get_context(
                        entries, idx, context, base_types, hide, errors_only
                    )
                    per_pattern[pi].append(
                        MatchHit(
                            session_id=session.session_id,
                            turn_uuid=entry.uuid,
                            entry=entry,
                            context_before=before,
                            context_after=after,
                            agent_id=source.agent_id,
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
        target_sessions = matching_ids(sessions, session_id, lambda s: (s.session_id,))
        if len(target_sessions) > 1:
            raise ambiguous_id(session_id, "Session", (
                f"{s.session_id} in {s.project_path}" for s in target_sessions
            ))

    for session in target_sessions:
        if session.is_conversion_artifact:
            continue
        session_matches: list[MatchHit] = []

        for source in session_sources(session):
            entries = load_source_transcript(source)
            for idx, entry in enumerate(entries):
                if not _is_searchable(entry, search_types):
                    continue
                if not _entry_matches(entry, compiled, hide):
                    continue

                before, after = _get_context(entries, idx, context, base_types, hide)
                session_matches.append(
                    MatchHit(
                        session_id=session.session_id,
                        turn_uuid=entry.uuid,
                        entry=entry,
                        context_before=before,
                        context_after=after,
                        agent_id=source.agent_id,
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


@dataclass
class TurnContext:
    session: SessionInfo
    turn: str
    entries: list[TranscriptEntry]
    agent_id: str | None = None


@dataclass
class BrowseWindow:
    entries: list[TranscriptEntry]
    total: int
    anchor: str | None = None


def get_turn_context(
    sessions: list[SessionInfo],
    turn_uuid: str,
    context: int = 3,
    hide: frozenset[str] = frozenset(),
    session_id: str | None = None,
) -> TurnContext | None:
    """Resolve across every source, rejecting ambiguous references before returning context.

    Return the canonical turn identity with its window. A copied turn can exist
    in multiple sessions, so even a complete turn id can require session scope.
    """
    conv_types = conversation_types_for(hide)
    target_sessions = sessions
    if session_id:
        target_sessions = matching_ids(sessions, session_id, lambda s: (s.session_id,))
    candidates: list[TurnContext] = []
    for session in target_sessions:
        for source in session_sources(session):
            entries = load_source_transcript(source)
            for idx, entry in enumerate(entries):
                if isinstance(entry, BaseTranscriptEntry) and id_matches(entry.uuid, turn_uuid):
                    before, after = _get_context(entries, idx, context, conv_types, hide)
                    candidates.append(TurnContext(session, entry.uuid, before + [entry] + after, source.agent_id))
    matches = matching_ids(candidates, turn_uuid, lambda c: (c.turn,))
    if len(matches) > 1:
        raise ambiguous_id(turn_uuid, "Turn", (
            f"{c.turn} in session {c.session.session_id} ({c.session.project_path}; agent {c.agent_id or 'main'})"
            for c in matches
        ))
    return matches[0] if matches else None


def browse_session_turns(
    transcript: Path | SessionRef | SessionInfo,
    position: str,
    turns: int = 10,
    anchor_turn: str | None = None,
    entry_types: tuple[type, ...] = (HumanEntry, AssistantTranscriptEntry),
) -> BrowseWindow:
    """Return first or last N conversation turns from a transcript file.

    Takes a bare path so any transcript browses — a session's main file or a
    subagent body — without promoting a SessionInfo first.
    Filters to entry_types (default: HumanEntry + AssistantTranscriptEntry).
    If anchor_turn is set, tail reads forward from anchor, head reads up to anchor.
    Returns the window, total count, and resolved canonical anchor.
    """
    if isinstance(transcript, Path):
        entries = load_transcript(transcript)
    else:
        entries = load_source_transcript(session_sources(transcript)[0])
    conversation = [e for e in entries if isinstance(e, entry_types)]
    total = len(conversation)

    anchor = None
    if anchor_turn:
        matches = matching_ids(
            [(i, e) for i, e in enumerate(conversation) if isinstance(e, BaseTranscriptEntry)],
            anchor_turn, lambda pair: (pair[1].uuid,),
        )
        if len(matches) > 1:
            raise ambiguous_id(anchor_turn, "Turn", (e.uuid for _, e in matches))
        anchor_idx = matches[0][0] if matches else None
        if anchor_idx is None:
            return BrowseWindow([], total)

        anchor = matches[0][1].uuid
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

    return BrowseWindow(sliced, total, anchor)
