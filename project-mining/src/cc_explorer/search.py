"""Search, filter, and triage operations on parsed transcript entries.

Operates on typed entries — filenames are implementation details. The
interface uses session IDs (UUID from filename) and turn UUIDs (the uuid
field on each entry).
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional, TypeGuard

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
    substantive_human_text,
)
from .formatting import _match_example
from .parser import load_conversations, load_transcript
from .subagents import collect_agent_files, discover_subagents, resolve_subagents_dir
from .utils import PrefixId, smart_truncate


# =============================================================================
# Conversation role
# =============================================================================


class ConversationRole(str, Enum):
    user = "user"
    assistant = "assistant"
    all = "all"


# Sentinel for sessions/projects with no timestamp: sorts last under newest-first.
# tz-aware so it never collides with aware first_timestamps (mixing aware + naive
# in a sort key raises TypeError).
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def sort_sessions_newest_first(sessions: list["SessionInfo"]) -> None:
    """Sort sessions in place, newest first; None timestamps sort last."""
    sessions.sort(key=lambda s: s.first_timestamp or _EPOCH, reverse=True)


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


def resolve_projects(projects: Optional[list[str]] = None) -> list[str]:
    """Resolve a projects selector to concrete project paths.

    Empty / None ⇒ every project on disk (cross-project search), discovered and
    flattened across git worktrees by `discover_projects`. A non-empty list ⇒
    those projects, each run through `resolve_project` (bare name → ~/projects/<name>,
    path as-is). De-duplicates while preserving order.
    """
    if not projects:
        return [p.path for p in discover_projects()]

    seen: set[str] = set()
    resolved: list[str] = []
    for p in projects:
        path = resolve_project(p)
        if path not in seen:
            seen.add(path)
            resolved.append(path)
    return resolved


# =============================================================================
# Project discovery (cross-project) — enumerate ~/.claude/projects, flatten
# worktrees back into their repo so one logical project is one entry.
# =============================================================================


# Claude Code dispatch creates linked worktrees under a fixed in-repo location.
# Two conventions have shipped: the current `<repo>/.claude/worktrees/<name>` and
# the older `<repo>/.claude-worktrees/<name>`. The path *structure* alone names
# the repo — everything before the marker segment is the main worktree root.
_WORKTREE_MARKERS = ("/.claude/worktrees/", "/.claude-worktrees/")


def _repo_root_from_worktree_path(cwd: str) -> Optional[str]:
    """Recover a repo root from a Claude-dispatch worktree cwd by path structure.

    `git worktree list` is the authoritative pooling source, but it only knows
    about worktrees that still exist on disk: a pruned/deleted worktree leaves its
    transcripts behind under `~/.claude/projects/` with no live git entry, so the
    shell-out returns nothing and the orphaned sessions float as their own
    fragment "project" (labeled with the worktree basename). The dispatch path
    convention is stable, so we can fold those orphans back by string structure
    alone — no git, no disk access. Returns None when cwd is not a dispatch
    worktree (the caller then falls back to git / cwd-as-repo).
    """
    for marker in _WORKTREE_MARKERS:
        idx = cwd.find(marker)
        if idx > 0:
            return cwd[:idx]
    return None


@dataclass
class ProjectInfo:
    """A logical project discovered on disk, pooled across its git worktrees.

    `path` is the canonical main-worktree path (the git repo root); `encoded_dirs`
    are every `~/.claude/projects/<encoded>/` directory that pools into it (main
    plus linked worktrees). `session_count` and `last_active` are cheap aggregates
    over those dirs (file count and max mtime — no transcript parse).
    """

    path: str
    name: str
    encoded_dirs: list[Path] = field(default_factory=list)
    session_count: int = 0
    last_active: Optional[datetime] = None


def _cwd_from_transcripts(jsonls: list[Path], scan_lines: int = 20) -> Optional[str]:
    """Recover a project's real cwd from its transcripts.

    The encoded dir name is a one-way sanitization (non-alphanumeric → '-'), so
    the real path can't be un-mangled — it has to be read back out of a
    transcript. Entries carry `cwd` (BaseTranscriptEntry); leading lines are
    sometimes summaries without one, so scan the first `scan_lines` of each file
    in deterministic (sorted) order until a 'cwd' key turns up. Cheap by design:
    first parsable cwd wins, no full parse.
    """
    for jsonl in jsonls:
        try:
            with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
                for _ in range(scan_lines):
                    line = f.readline()
                    if not line:
                        break
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    cwd = data.get("cwd")
                    if isinstance(cwd, str) and cwd:
                        return cwd
        except OSError:
            continue
    return None


def discover_projects() -> list[ProjectInfo]:
    """Enumerate every project under ~/.claude/projects, flattening worktrees.

    Each encoded dir is mapped to its real cwd (read from a transcript), then
    pooled into its git repo's main worktree via the same `_get_worktree_paths`
    machinery `load_conversations` uses for single-project pooling. Git calls are
    cached per repo (siblings prepopulated from one `git worktree list`). cwds
    not inside a git repo pool under themselves.

    Returns ProjectInfo list sorted by `last_active` (newest first). Dirs with no
    parsable cwd are skipped.
    """
    from ._claude_paths import (
        _canonicalize_path,
        _get_projects_dir,
        _get_worktree_paths,
    )

    projects_dir = _get_projects_dir()
    try:
        encoded_dirs = [d for d in projects_dir.iterdir() if d.is_dir()]
    except OSError:
        return []

    # cwd → main-worktree path, cached. Siblings from one `git worktree list`
    # are prepopulated so each repo shells out at most once.
    main_cache: dict[str, str] = {}

    def main_worktree(cwd: str) -> str:
        canonical = _canonicalize_path(cwd)
        if canonical in main_cache:
            return main_cache[canonical]
        worktrees = _get_worktree_paths(canonical)
        if not worktrees:
            # git knows nothing (no repo, or a pruned worktree whose transcripts
            # outlived it). Fold a dispatch worktree back into its repo by path
            # structure; otherwise the cwd pools under itself.
            root = _repo_root_from_worktree_path(canonical) or canonical
            main_cache[canonical] = root
            return root
        main = worktrees[0]
        for wt in worktrees:
            main_cache.setdefault(wt, main)
        main_cache[canonical] = main
        return main

    pooled: dict[str, ProjectInfo] = {}
    for enc in encoded_dirs:
        # One sorted enumeration of the dir, reused for cwd recovery + counts.
        jsonls = sorted(enc.glob("*.jsonl"))
        if not jsonls:
            continue
        cwd = _cwd_from_transcripts(jsonls)
        if not cwd:
            continue
        repo = main_worktree(cwd)

        proj = pooled.get(repo)
        if proj is None:
            proj = ProjectInfo(path=repo, name=Path(repo).name)
            pooled[repo] = proj
        proj.encoded_dirs.append(enc)

        for jsonl in jsonls:
            proj.session_count += 1
            try:
                mtime = datetime.fromtimestamp(jsonl.stat().st_mtime, tz=timezone.utc)
            except OSError:
                continue
            if proj.last_active is None or mtime > proj.last_active:
                proj.last_active = mtime

    result = list(pooled.values())
    result.sort(key=lambda p: p.last_active or _EPOCH, reverse=True)
    return result


# =============================================================================
# Search corpus — a session's main transcript plus its subagent transcripts.
# =============================================================================


@dataclass
class TranscriptSource:
    """One searchable transcript file within a session's corpus.

    `agent_id` is None for the main session transcript and the subagent's id for
    a `subagents/agent-*.jsonl` body (including workflow-orchestrated orphans).
    """

    agent_id: Optional[PrefixId]
    path: Path


def session_sources(session: "SessionInfo") -> list["TranscriptSource"]:
    """Expand a session into every transcript that belongs to its search corpus.

    The main transcript plus every subagent transcript on disk. We use
    `collect_agent_files` (a pure filesystem walk over `subagents/`, including
    `workflows/<runId>/`) rather than the full `discover_subagents` reconciliation
    — search only needs the transcript *files*, not the dispatch graph, and this
    avoids re-reading the parent transcript. This is what makes a subagent's
    internal activity searchable (#22), not just the result text the parent
    recorded.

    NOTE: this walks every session's subagent dir, so a cross-project search
    touches the whole tree — the known cost tracked as the whole-corpus perf
    follow-up. Correctness first.
    """
    sources: list[TranscriptSource] = [TranscriptSource(agent_id=None, path=session.path)]
    for af in collect_agent_files(resolve_subagents_dir(session.path)):
        sources.append(
            TranscriptSource(
                agent_id=PrefixId(af.agent_id) if af.agent_id else None,
                path=af.path,
            )
        )
    return sources



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
    # The project this session was loaded under (the path passed to load_sessions,
    # i.e. the canonical repo root for worktree-pooled sessions). Carries project
    # provenance through search so cross-project results can name where each hit
    # lives without the tool layer re-threading it.
    project_path: Optional[str] = None
    # Number of human prompts (entries where the user actually spoke), distinct
    # from message_count which also counts assistant turns. Signals how much the
    # human drove the session vs one prompt fanning out into a long agent run.
    user_turns: int = 0
    # Full discovered subagent population — parent dispatches plus on-disk
    # orphans (notably workflow-orchestrated agents). Equals list_session_agents'
    # total_agents, unlike stats.agent_count which is top-down dispatches only.
    agents_present: int = 0


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
    agent_id: Optional[PrefixId] = None


@dataclass
class MatchHit:
    """A single search match with surrounding context.

    `agent_id` is None when the match is in the main session transcript and the
    subagent id when it's inside a subagent body; context turns are drawn from
    the same transcript the match came from.
    """

    session_id: PrefixId
    turn_uuid: PrefixId
    entry: TranscriptEntry
    context_before: list[TranscriptEntry]
    context_after: list[TranscriptEntry]
    agent_id: Optional[PrefixId] = None


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
) -> TypeGuard[BaseTranscriptEntry]:
    """isinstance against the dynamic search-type tuple, narrowed for the checker.

    `search_types` is built at runtime by conversation_types_for(), so a static
    checker can't narrow from `isinstance(entry, search_types)`. But every
    searchable entry kind subclasses BaseTranscriptEntry, so a match proves that
    — this TypeGuard states the invariant once, letting the search loops use a
    single check (and reach `.uuid` / `.display`) instead of a redundant second
    `isinstance(entry, BaseTranscriptEntry)`.
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


def load_sessions(
    project_path: str, *, with_agents_present: bool = False
) -> list[SessionInfo]:
    """Find and load all conversation sessions for a project.

    Returns SessionInfo list sorted by first_timestamp (newest first).

    `with_agents_present` populates SessionInfo.agents_present by walking each
    session's on-disk subagents dir (reconciled against parent dispatches). That
    walk is per-session filesystem I/O, so it's opt-in — only list_project_sessions,
    which displays and filters on the count, needs it. Every other tool loads
    sessions purely for their transcripts and leaves agents_present at 0.
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

        # Human prompts only — how many times the user actually spoke.
        user_turns = sum(
            1
            for e in entries
            if isinstance(e, HumanEntry) and len(e.display(truncate=0)) > 0
        )

        # Find first timestamp from any entry that has one (typed access)
        first_ts: Optional[datetime] = None
        for e in entries:
            if isinstance(e, BaseTranscriptEntry):
                first_ts = e.timestamp
                break

        title = session_title(entries)
        stats = TranscriptStats.from_entries(entries)
        # Reconcile parent dispatches with the on-disk subagent walk so the
        # count matches list_session_agents and catches workflow orphans. Reuses
        # the already-parsed entries — only the tiny subagents/ dir is walked.
        # Gated: most tools don't need it and shouldn't pay the per-session walk.
        agents_present = (
            len(discover_subagents(ref.path, entries=entries))
            if with_agents_present
            else 0
        )
        sessions.append(
            SessionInfo(
                session_id=session_id,
                path=ref.path,
                title=title,
                first_timestamp=first_ts,
                message_count=message_count,
                stats=stats,
                worktree=ref.worktree,
                user_turns=user_turns,
                agents_present=agents_present,
                project_path=project_path,
            )
        )

    # Sort newest first (None timestamps sort last)
    sort_sessions_newest_first(sessions)
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
        count = 0
        first_example = ""
        first_agent_id: Optional[PrefixId] = None
        for source in session_sources(session):
            entries = load_transcript(source.path)
            for entry in entries:
                if not _is_searchable(entry, search_types):
                    continue
                if _entry_matches(entry, compiled, hide):
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
) -> PatternTriageResults:
    """Count matches for multiple patterns in a single pass over each session.

    Loads each session's transcript once and checks all patterns per entry.
    Returns PatternTriageResults — same type consumed by SearchProjectsResponse.from_triage.
    """
    compiled = [(pat, re.compile(pat, re.IGNORECASE)) for pat in patterns]
    search_types = conversation_types_for(hide, base_types)

    # Per-pattern accumulators: {pattern_index: {session_index: (count, first_example, first_agent_id)}}
    accum: dict[int, dict[int, tuple[int, str, Optional[PrefixId]]]] = {
        i: {} for i in range(len(compiled))
    }

    for si, session in enumerate(sessions):
        # Search the whole corpus: main transcript + every subagent body (#22).
        for source in session_sources(session):
            entries = load_transcript(source.path)
            for entry in entries:
                if not _is_searchable(entry, search_types):
                    continue
                for pi, (_, regex) in enumerate(compiled):
                    if _entry_matches(entry, regex, hide):
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
        # Per-pattern accumulator for this session: pi -> list[MatchHit]
        per_pattern: dict[int, list[MatchHit]] = {i: [] for i in range(len(compiled))}
        per_pattern_totals: dict[int, int] = {i: 0 for i in range(len(compiled))}

        # Walk the whole corpus: main transcript + every subagent body (#22).
        # Context is drawn from within the same source the match came from.
        for source in session_sources(session):
            entries = load_transcript(source.path)
            for idx, entry in enumerate(entries):
                if not _is_searchable(entry, search_types):
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
                            turn_uuid=PrefixId(entry.uuid or ""),
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
        target_sessions = [s for s in sessions if s.session_id == session_id]

    for session in target_sessions:
        session_matches: list[MatchHit] = []

        for source in session_sources(session):
            entries = load_transcript(source.path)
            for idx, entry in enumerate(entries):
                if not _is_searchable(entry, search_types):
                    continue
                if not _entry_matches(entry, compiled, hide):
                    continue

                before, after = _get_context(entries, idx, context, base_types, hide)
                session_matches.append(
                    MatchHit(
                        session_id=session.session_id,
                        turn_uuid=PrefixId(entry.uuid or ""),
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


def get_turn_context(
    sessions: list[SessionInfo],
    turn_uuid: str,
    context: int = 3,
    hide: frozenset[str] = frozenset(),
    session_id: str | None = None,
) -> tuple[SessionInfo | None, list[TranscriptEntry], Optional[PrefixId]]:
    """Find a turn by UUID across sessions and return surrounding entries.

    Turn UUIDs are globally unique — session_id is optional and used only to
    narrow the search when the caller wants to be explicit. The turn may live in
    the main transcript or in any subagent body (#22), so the whole corpus is
    scanned per session.

    Returns (session_info, entries, agent_id) where entries includes context and
    agent_id names the subagent body the turn was found in (None for main).
    """
    conv_types = conversation_types_for(hide)

    target_sessions = sessions
    if session_id:
        target_sessions = [s for s in sessions if s.session_id == session_id]

    for session in target_sessions:
        for source in session_sources(session):
            entries = load_transcript(source.path)
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

                return session, result, source.agent_id

    return None, [], None


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
