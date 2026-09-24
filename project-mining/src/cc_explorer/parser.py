# Adapted from claude-code-log by Daniel Demmel (MIT License)
# https://github.com/daaain/claude-code-log
"""JSONL parsing into typed transcript entries.

Core functions:
- load_transcript(path) — parse a JSONL file into typed entries
- load_conversations(project_path) — find all JSONL files for a project,
  pooled across git worktrees
- extract_text — re-exported from models.py
"""

import os
import sys
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Optional, Sequence, Union, cast

import orjson
from cachetools import LRUCache
from pydantic import BaseModel

from .models import (
    AssistantTranscriptEntry,
    ContentItem,
    FileSnapshotEntry,
    HumanEntry,
    ImageContent,
    MetaEntry,
    ProgressEntry,
    QueueOperationTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    UserMessageModel,
)


# =============================================================================
# Content Item Creation
# =============================================================================

CONTENT_ITEM_CREATORS: dict[str, type[BaseModel]] = {
    "text": TextContent,
    "tool_result": ToolResultContent,
    "image": ImageContent,
    "tool_use": ToolUseContent,
    "thinking": ThinkingContent,
}

USER_CONTENT_TYPES: Sequence[str] = ("text", "tool_result", "image")
ASSISTANT_CONTENT_TYPES: Sequence[str] = ("text", "tool_use", "thinking")


def create_content_item(
    item_data: dict[str, Any],
    type_filter: Sequence[str] | None = None,
) -> ContentItem:
    """Create a ContentItem from raw data using the registry."""
    try:
        content_type = item_data.get("type", "")
        if type_filter is None or content_type in type_filter:
            model_class = CONTENT_ITEM_CREATORS.get(content_type)
            if model_class is not None:
                return cast(ContentItem, model_class.model_validate(item_data))
        return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def create_message_content(
    content_data: Any,
    type_filter: Sequence[str] | None = None,
) -> list[ContentItem]:
    """Normalize message content (string or list) into list[ContentItem]."""
    if isinstance(content_data, str):
        return [TextContent(type="text", text=content_data)]
    elif isinstance(content_data, list):
        result: list[ContentItem] = []
        for item in content_data:
            if isinstance(item, dict):
                result.append(create_content_item(item, type_filter))
            else:
                result.append(TextContent(type="text", text=str(item)))
        return result
    else:
        return [TextContent(type="text", text=str(content_data))]


# =============================================================================
# Transcript Entry Creation
# =============================================================================


class UnsupportedTranscriptType(ValueError):
    """A record type the conversation parser does not model (#61)."""


def _create_user_entry(data: dict[str, Any]) -> Union[HumanEntry, ToolResultEntry, MetaEntry]:
    """Create the appropriate user entry type based on flags.

    Split logic:
    - toolUseResult present → ToolResultEntry
    - isMeta=True → MetaEntry
    - otherwise → HumanEntry

    Special case: raw string content containing <task-notification> is
    classified as ToolResultEntry (subagent results delivered as raw strings
    without the toolUseResult flag).
    """
    data_copy = data.copy()

    # Normalize message content
    if "message" in data_copy and "content" in data_copy["message"]:
        data_copy["message"] = data_copy["message"].copy()
        data_copy["message"]["content"] = create_message_content(
            data_copy["message"]["content"],
            USER_CONTENT_TYPES,
        )

    # Parse list-type toolUseResult (MCP tool results)
    if "toolUseResult" in data_copy and isinstance(data_copy["toolUseResult"], list):
        tool_use_result = data_copy["toolUseResult"]
        if (
            tool_use_result
            and isinstance(tool_use_result[0], dict)
            and "type" in tool_use_result[0]
        ):
            data_copy["toolUseResult"] = [
                create_content_item(item)
                for item in tool_use_result
                if isinstance(item, dict)
            ]

    # Dispatch to the right entry type
    if data_copy.get("toolUseResult") is not None:
        return ToolResultEntry.model_validate(data_copy)

    if data_copy.get("isMeta"):
        return MetaEntry.model_validate(data_copy)

    # Check for task-notification in raw string content (subagent results
    # that arrive without the toolUseResult flag)
    raw_content = data_copy.get("message", {}).get("content", "")
    if isinstance(raw_content, str) and "<task-notification>" in raw_content:
        # Treat as tool result even though it lacks the flag
        data_copy["toolUseResult"] = raw_content
        return ToolResultEntry.model_validate(data_copy)

    return HumanEntry.model_validate(data_copy)


def _create_assistant_entry(data: dict[str, Any]) -> AssistantTranscriptEntry:
    """Create an AssistantTranscriptEntry from raw data."""
    data_copy = data.copy()
    if "message" in data_copy and "content" in data_copy["message"]:
        message_copy = data_copy["message"].copy()
        message_copy["content"] = create_message_content(
            message_copy["content"],
            ASSISTANT_CONTENT_TYPES,
        )
        data_copy["message"] = message_copy
    return AssistantTranscriptEntry.model_validate(data_copy)


def _create_queue_operation_entry(data: dict[str, Any]) -> QueueOperationTranscriptEntry:
    data_copy = data.copy()
    if "content" in data_copy and isinstance(data_copy["content"], list):
        data_copy["content"] = create_message_content(data_copy["content"])
    return QueueOperationTranscriptEntry.model_validate(data_copy)


def create_transcript_entry(data: dict[str, Any]) -> TranscriptEntry:
    """Create a typed TranscriptEntry from a JSON dictionary.

    Dispatches on the 'type' field. Unknown types raise ValueError.
    """
    entry_type = data.get("type")
    if entry_type == "user":
        return _create_user_entry(data)
    elif entry_type == "assistant":
        return _create_assistant_entry(data)
    elif entry_type == "summary":
        return SummaryTranscriptEntry.model_validate(data)
    elif entry_type == "system":
        return SystemTranscriptEntry.model_validate(data)
    elif entry_type == "queue-operation":
        return _create_queue_operation_entry(data)
    elif entry_type == "progress":
        return ProgressEntry.model_validate(data)
    elif entry_type == "file-history-snapshot":
        return FileSnapshotEntry.model_validate(data)
    else:
        raise UnsupportedTranscriptType(f"Unknown transcript entry type: {entry_type}")


# =============================================================================
# High-level API
# =============================================================================


@dataclass
class CachedTranscript:
    """Parsed transcript entries with the file mtime at parse time.

    `nbytes` is the entry's estimated heap cost (raw file size x the measured
    heap factor) — what the byte-capped cache charges for holding it.
    """

    mtime: float
    entries: list[TranscriptEntry]
    nbytes: int
    malformed: int = 0
    unsupported: int = 0


@dataclass
class ParserDiagnostics:
    """One operation's counts; retain paths for deduplication, never raw records."""

    malformed: int = 0
    unsupported: int = 0
    _paths: set[Path] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, path: Path, malformed: int, unsupported: int) -> None:
        if not (malformed or unsupported):
            return
        with self._lock:
            if path in self._paths:
                return
            self._paths.add(path)
            self.malformed += malformed
            self.unsupported += unsupported
            if os.environ.get("CC_EXPLORER_PARSER_DEBUG") == "1":
                print(
                    f"[cc-explorer parser debug] {str(path)!r}: "
                    f"{malformed} malformed, {unsupported} unsupported line(s)",
                    file=sys.stderr,
                )

    def report(self) -> None:
        if self._paths:
            # Constant shape: no filenames or record-type lists in ordinary
            # output. stdout belongs exclusively to the MCP protocol.
            print(
                f"[cc-explorer parser] skipped {self.malformed + self.unsupported} "
                f"line(s) across {len(self._paths)} transcript(s): "
                f"{self.malformed} malformed, {self.unsupported} unsupported. "
                "Per-file details: CC_EXPLORER_PARSER_DEBUG=1.",
                file=sys.stderr,
            )


_diagnostics: ContextVar[ParserDiagnostics | None] = ContextVar(
    "parser_diagnostics", default=None
)


@contextmanager
def collect_parser_diagnostics() -> Iterator[ParserDiagnostics]:
    """Aggregate a batch into one stderr summary, including on failure.

    MCP middleware owns the outer scope. ContextVars propagate into FastMCP's
    sync worker threads and keep concurrent requests independent. Library
    callers can wrap their own batches; nested scopes share the outer counts.
    Cached reads participate so warm and cold requests report the same facts.
    """
    existing = _diagnostics.get()
    if existing is not None:
        yield existing
        return
    diagnostics = ParserDiagnostics()
    token = _diagnostics.set(diagnostics)
    try:
        yield diagnostics
    finally:
        _diagnostics.reset(token)
        diagnostics.report()


# Parsed pydantic entry graphs occupy ~1.8x the raw file bytes on the heap
# (measured against the real corpus: a 3.13 GB corpus parsed to a 5.5 GB RSS).
# Used to charge cache entries by estimated heap cost without walking the
# object graph.
_HEAP_FACTOR = 1.8

_DEFAULT_CACHE_MB = 200


def _cache_max_bytes() -> int:
    """Cache cap in bytes (CC_EXPLORER_CACHE_MB env, default 200 MB).

    Only a positive value is honored; an unparseable or non-positive override
    falls back to the default rather than silently disabling the cache or
    unbounding it.
    """
    raw = os.environ.get("CC_EXPLORER_CACHE_MB")
    if raw:
        try:
            mb = float(raw)
            if mb > 0:
                return int(mb * 1024 * 1024)
        except ValueError:
            pass
    return _DEFAULT_CACHE_MB * 1024 * 1024


class TranscriptCache:
    """Byte-capped LRU of parsed transcripts, keyed by resolved path.

    Entries are charged their estimated heap cost via `CachedTranscript.nbytes`,
    so total retained memory stays near the cap regardless of how much of the
    corpus a tool call touches. This replaces the unbounded module dict that
    retained every transcript ever parsed — one cross-project search over a
    ~3 GB corpus left a 5.5 GB process behind. Staleness is the caller's
    concern (compare mtime); a single transcript larger than the whole cap is
    served uncached rather than erroring.
    """

    def __init__(self, max_bytes: int) -> None:
        self._lru: LRUCache[Path, CachedTranscript] = LRUCache(
            maxsize=max_bytes, getsizeof=lambda v: v.nbytes
        )
        # cachetools.LRUCache mutates its recency order on both get and put, and
        # isn't thread-safe; FastMCP dispatches sync tools on a thread pool
        # (anyio.to_thread), so concurrent tool calls can otherwise corrupt the
        # LRU bookkeeping.
        self._lock = threading.Lock()

    def get(self, path: Path) -> Optional[CachedTranscript]:
        with self._lock:
            return self._lru.get(path)

    def put(self, path: Path, value: CachedTranscript) -> None:
        with self._lock:
            try:
                self._lru[path] = value
            except ValueError:
                # Value alone exceeds the cache cap — serve it uncached.
                pass


_cache = TranscriptCache(_cache_max_bytes())


# Structural header line types that are part of the wire format but are not
# conversation data: session mode/permission/title/prompt headers, worktree and
# PR bookkeeping, and our own conversion-provenance sentinel. Skipped silently
# (they are expected in well-formed files). #61 owns harvesting provenance from
# these and unsupported records; #80 owns diagnostics, not their exposure.
_STRUCTURAL_LINE_TYPES = frozenset(
    {
        "mode",
        "permission-mode",
        "custom-title",
        "ai-title",
        "last-prompt",
        "attachment",
        "agent-name",
        "relocated",
        "worktree-state",
        "pr-link",
        "x-converter-provenance",
    }
)


def load_transcript(path: Path) -> list[TranscriptEntry]:
    """Load a JSONL file into typed transcript entries.

    Caches results by (path, mtime) — the MCP server is a persistent process,
    so the cache lives across tool calls. Re-parses only when the file changes.
    The cache is byte-capped (CC_EXPLORER_CACHE_MB, default 200 MB), so a
    corpus-wide operation can't accumulate the whole corpus in memory.

    Structural headers stay silent. Malformed and unsupported records have
    separate counts, aggregated by MCP call (or collect_parser_diagnostics for
    library batches). An individual library load has its own summary.
    """
    with collect_parser_diagnostics() as diagnostics:
        cached = _load_transcript(path)
        diagnostics.record(path.resolve(), cached.malformed, cached.unsupported)
        return cached.entries


def _load_transcript(path: Path) -> CachedTranscript:
    resolved = path.resolve()
    stat = resolved.stat()
    mtime = stat.st_mtime

    cached = _cache.get(resolved)
    if cached is not None and cached.mtime == mtime:
        return cached

    entries: list[TranscriptEntry] = []
    malformed = 0
    unsupported = 0
    with open(resolved, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = orjson.loads(line)
            except orjson.JSONDecodeError:
                malformed += 1
                continue
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("type"), str)
                or not data["type"]
            ):
                malformed += 1
                continue
            if data["type"] in _STRUCTURAL_LINE_TYPES:
                continue
            try:
                entries.append(create_transcript_entry(data))
            except UnsupportedTranscriptType:
                unsupported += 1
            except Exception:
                malformed += 1

    cached = CachedTranscript(
        mtime=mtime,
        entries=entries,
        nbytes=int(stat.st_size * _HEAP_FACTOR),
        malformed=malformed,
        unsupported=unsupported,
    )
    _cache.put(resolved, cached)
    return cached


@dataclass(frozen=True)
class ConversationRef:
    """A located JSONL conversation, plus which worktree it came from.

    `worktree=None` means the session belongs to the main worktree of the
    project (the one `git worktree list` reports first — the repo root).
    `worktree="<name>"` is the basename of a linked worktree directory
    (e.g. `"happy-lehmann"` for `<project>/.claude-worktrees/happy-lehmann`).
    Claude Desktop dispatch creates these as real git worktrees, so they
    appear in `git worktree list` automatically.
    """

    path: Path
    worktree: Optional[str]


def load_conversations(project_path: str) -> dict[str, ConversationRef]:
    """Find all JSONL conversation files for a project, pooled across worktrees.

    Returns {session_id: ConversationRef} where session_id is the UUID from
    the filename. Sessions from every git worktree of the project are merged
    into one pool — the main worktree gets `worktree=None`, linked worktrees
    get labeled with their directory basename.

    Path resolution (long-path hash suffixes, Bun/Node hash mismatches,
    CLAUDE_CONFIG_DIR) and the `git worktree list --porcelain` shell-out that
    discovers worktree paths come from `_claude_paths`, our vendored copy of
    the Claude Code CLI's directory-naming logic. See that module's header for
    provenance and the "check for updates" recipe.

    When git is unavailable or `project_path` is not inside a repo, falls
    back to scanning the single project directory (all sessions get
    `worktree=None`).
    """
    from cc_explorer._claude_paths import (
        _canonicalize_path,
        _find_project_dir,
        _get_worktree_paths,
    )

    project_path_resolved = str(Path(project_path).expanduser().resolve())
    canonical = _canonicalize_path(project_path_resolved)

    # Worktree paths from `git worktree list --porcelain`. The main worktree
    # is always first in git's output — we use that ordering to decide which
    # sessions are "main" (worktree=None) vs labeled.
    worktree_paths = _get_worktree_paths(canonical)

    # Fallback: no git / not a repo / scan failed → single-dir behavior.
    if not worktree_paths:
        claude_dir = _find_project_dir(canonical)
        if claude_dir is None or not claude_dir.exists():
            return {}
        return {
            jsonl.stem: ConversationRef(path=jsonl, worktree=None)
            for jsonl in claude_dir.glob("*.jsonl")
        }

    result: dict[str, ConversationRef] = {}
    seen_dirs: set[Path] = set()
    for i, wt_path in enumerate(worktree_paths):
        claude_dir = _find_project_dir(wt_path)
        if claude_dir is None or not claude_dir.exists():
            continue
        seen_dirs.add(claude_dir)
        label: Optional[str] = None if i == 0 else Path(wt_path).name
        for jsonl in claude_dir.glob("*.jsonl"):
            session_id = jsonl.stem
            # First-wins on dupes. A session UUID should only exist in one
            # worktree's project dir, but if the same file somehow appears
            # in multiple (shared cache, weird symlink), keep the earlier
            # one — main worktree (i=0) takes priority by construction.
            if session_id not in result:
                result[session_id] = ConversationRef(path=jsonl, worktree=label)

    # Fold in orphaned dispatch-worktree dirs: a pruned/deleted worktree leaves
    # its transcripts under ~/.claude/projects/ with no live `git worktree list`
    # entry, so the loop above never reaches them. They belong to this repo by
    # path structure — recover and merge them so every tool (search, listing,
    # activity) sees the full session population, not just live worktrees.
    for enc_dir, _wt_cwd, wt_name in _orphan_worktree_dirs(canonical, seen_dirs):
        for jsonl in enc_dir.glob("*.jsonl"):
            session_id = jsonl.stem
            if session_id not in result:
                result[session_id] = ConversationRef(path=jsonl, worktree=wt_name)
    return result


def _orphan_worktree_dirs(
    repo_root: str, seen_dirs: set[Path]
) -> list[tuple[Path, str, str]]:
    """Encoded project dirs for dispatch worktrees of `repo_root` that git missed.

    Scans ~/.claude/projects for dirs whose recovered cwd path-folds into
    `repo_root` via the `.claude/worktrees` convention but weren't already
    discovered through `git worktree list` (pruned/deleted worktrees). Returns
    (encoded_dir, worktree_cwd, worktree_name) tuples. Cheap: a prefix-gated dir
    scan plus a shallow transcript read per candidate, no git.

    Known accepted limitations:
    - Hash-truncated sanitized names: for very deep repo paths the encoded dir
      name is hash-truncated, defeating the `prefix + "-"` startswith gate, so
      such orphans aren't recovered (accepted — they're rare and the gate keeps
      the scan cheap).
    - Orphan recovery requires a functional git main worktree: the repo root is
      recovered from the worktree path structure, so a repo whose main worktree
      no longer exists has nothing to pool the orphans back into.
    """
    from cc_explorer._claude_paths import (
        _canonicalize_path,
        _get_projects_dir,
        _sanitize_path,
    )
    from cc_explorer.corpus import _cwd_from_transcripts, _repo_root_from_worktree_path

    prefix = _sanitize_path(repo_root)
    projects_dir = _get_projects_dir()
    try:
        candidates = [
            d
            for d in projects_dir.iterdir()
            if d.is_dir() and d not in seen_dirs and d.name.startswith(prefix + "-")
        ]
    except OSError:
        return []

    out: list[tuple[Path, str, str]] = []
    for d in candidates:
        jsonls = sorted(d.glob("*.jsonl"))
        if not jsonls:
            continue
        cwd = _cwd_from_transcripts(jsonls)
        if not cwd:
            continue
        wt_root = _repo_root_from_worktree_path(cwd)
        if wt_root is None or _canonicalize_path(wt_root) != repo_root:
            continue
        out.append((d, cwd, Path(cwd).name))
    return out


# extract_text and _strip_system_xml moved to models.py
from .models import extract_text  # noqa: F401 — re-export for external consumers
