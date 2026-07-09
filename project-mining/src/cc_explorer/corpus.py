"""Corpus identity layer — sessions known by filename alone, plus the raw-byte
scanners that prefilter them.

NO transcript parsing happens in this module. Everything here is built from
directory listings, filename structure, and raw-byte scans (ripgrep or a
streaming regex fallback). Promotion to a parsed session happens exactly once,
in `search.SessionInfo.load(ref)` — the invariant that bounds memory by
construction is that nothing holds a `list[SessionInfo]` for an unbounded
scope; callers hold `list[SessionRef]` and promote only the refs a prefilter
or an explicit id selected.

Prefilter semantics: the scanners match against RAW JSONL BYTES, while the
matcher of record (`search._entry_matches`) matches against EXTRACTED text
(post system-XML-stripping, blocks joined with real newlines). Raw bytes are a
superset of extracted text EXCEPT where JSON string escaping rewrites
characters (newline/tab/quote/backslash become \\n \\t \\" \\\\) — a pattern
that needs to match one of those can miss in raw bytes what the matcher finds
in extracted text. `rg_safe` gates exactly those patterns to the scan-all
path, so the prefilter can only ever over-select, never drop a true hit.

The superset guarantee holds for ASCII case folding. rg's `--ignore-case` and
Python's `re.IGNORECASE` (used by the typed matcher and `PyScanner`) diverge
on Unicode special-folding pairs (e.g. U+0130 İ / U+0131 ı), so a non-ASCII-
cased literal can, in principle, be missed by the RgScanner prefilter.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Optional, Protocol, Sequence

import orjson

from .parser import load_conversations
from .subagents import resolve_subagents_dir
from .utils import PrefixId


# Sentinel for sessions/projects with no timestamp: sorts last under newest-first.
# tz-aware so it never collides with aware timestamps in a sort key.
EPOCH = datetime.min.replace(tzinfo=timezone.utc)

# Ids shorter than this never match as prefixes — the floor that keeps a stray
# short id from sweeping the corpus, especially via the destructive tools
# (rewind, delete). `resolve.py` raises the user-facing too-short error;
# narrowing here just skips them.
MIN_ID_LEN = 6


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
                        data = orjson.loads(line)
                    except orjson.JSONDecodeError:
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
            # structure; otherwise the cwd pools under itself. Canonicalize the
            # recovered root so it pools under the SAME key the main worktree uses
            # (which also goes through _canonicalize_path) — an uncanonicalized
            # root would float as its own fragment project.
            recovered = _repo_root_from_worktree_path(canonical)
            root = _canonicalize_path(recovered) if recovered else canonical
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
    result.sort(key=lambda p: p.last_active or EPOCH, reverse=True)
    return result


# =============================================================================
# Session identity
# =============================================================================


@dataclass
class TranscriptSource:
    """One searchable transcript file within a session's corpus.

    `agent_id` is None for the main session transcript and the subagent's id for
    a `subagents/agent-*.jsonl` body (including workflow-orchestrated orphans).
    """

    agent_id: Optional[PrefixId]
    path: Path


@dataclass(frozen=True)
class SessionRef:
    """A session known by filename alone — the cheap identity half of the old
    SessionInfo. Everything on it comes from directory structure; deriving
    title/counts/stats requires promotion via `search.SessionInfo.load(ref)`.

    Replaces the `_ArtifactSession` shadow type the MCP layer grew to dodge
    the full-parse cost of SessionInfo.
    """

    session_id: PrefixId
    path: Path
    project_path: str
    worktree: Optional[str] = None

    def transcript_files(self) -> list[Path]:
        """The main transcript plus every on-disk subagent transcript.

        A pure filename walk (no meta.json or provenance reads — this feeds the
        raw-byte prefilter, which may over-include; conversion artifacts are
        excluded later by the matcher of record, not here).
        """
        files = [self.path]
        subdir = resolve_subagents_dir(self.path)
        if subdir.is_dir():
            files.extend(sorted(subdir.rglob("agent-*.jsonl")))
        return files


@dataclass
class Corpus:
    """The SessionRefs in scope for one tool call. Built from dir listings only."""

    refs: list[SessionRef]

    @classmethod
    def discover(cls, projects: Optional[list[str]] = None) -> "Corpus":
        """Every session across the selected projects (omit/empty ⇒ all projects).

        Pools across git worktrees per project (load_conversations) and
        de-duplicates by full session id across projects, guarding the case
        where an explicit `projects` list names two worktrees of the same repo.
        """
        refs: list[SessionRef] = []
        seen: set[str] = set()
        for proj in resolve_projects(projects):
            for sid, cref in load_conversations(proj).items():
                sid = sid if isinstance(sid, PrefixId) else PrefixId(sid)
                if sid.full in seen:
                    continue
                seen.add(sid.full)
                refs.append(
                    SessionRef(
                        session_id=sid,
                        path=cref.path,
                        project_path=proj,
                        worktree=cref.worktree,
                    )
                )
        return cls(refs)

    def narrow_to_ids(self, ids: Sequence[str]) -> "Corpus":
        """Refs whose session id matches any of the given ids/prefixes."""
        wanted = [PrefixId(i) for i in ids]
        return Corpus([r for r in self.refs if any(r.session_id == w for w in wanted)])

    def narrow_to_artifact_ids(self, ids: Sequence[str]) -> "Corpus":
        """Refs matching an id as a SESSION id or HOLDING an agent file for it.

        An agent id matches `<session>/subagents/**/agent-<id>*.jsonl` by pure
        filesystem walk (agent files are `agent-a…` so a session uuid never
        collides). One recursive glob per encoded project dir covers every id
        at once — the agent files it yields carry their session dir as the
        first path component, so ids prefix-match in Python with no per-session
        globbing. Ids shorter than MIN_ID_LEN are skipped here (the resolver
        raises its own too-short error).
        """
        session_matches = self.narrow_to_ids(ids).refs
        out = list(session_matches)
        by_session = set(session_matches)

        wanted = [i for i in ids if len(i) >= MIN_ID_LEN]
        if not wanted:
            return Corpus(out)

        by_dir: dict[Path, list[SessionRef]] = {}
        for r in self.refs:
            by_dir.setdefault(r.path.parent, []).append(r)

        for enc, dir_refs in by_dir.items():
            hit_stems: set[str] = set()
            for f in enc.glob("*/subagents/**/agent-*.jsonl"):
                agent_id = f.name[len("agent-") : -len(".jsonl")]
                if any(agent_id.startswith(i) for i in wanted):
                    hit_stems.add(f.relative_to(enc).parts[0])
            if not hit_stems:
                continue
            out.extend(
                r
                for r in dir_refs
                if r not in by_session and r.path.stem in hit_stems
            )
        return Corpus(out)

    def candidate_refs(self, patterns: Sequence[str]) -> list[SessionRef]:
        """Refs that MAY match any pattern — a superset by construction.

        rg-safe patterns run through the raw-byte prefilter (rg, or the
        streaming Python fallback when rg is missing); any rg-unsafe pattern —
        or a scanner failure — forces scan-all, where every ref is a candidate.
        The typed matcher (`search._entry_matches`) remains the matcher of
        record over whatever this returns.
        """
        if not self.refs or not patterns:
            return list(self.refs)
        if not all(rg_safe(p) for p in patterns):
            return list(self.refs)

        ref_files: list[tuple[SessionRef, list[Path]]] = []
        files: list[Path] = []
        for ref in self.refs:
            tf = ref.transcript_files()
            ref_files.append((ref, tf))
            files.extend(tf)

        try:
            hits = make_scanner().files_with_match(patterns, files)
        except ScannerError as e:
            # stderr, never stdout — stdout is the stdio MCP protocol channel.
            print(
                f"[cc-explorer corpus] prefilter failed ({e}); falling back to full scan",
                file=sys.stderr,
            )
            return list(self.refs)

        return [ref for ref, tf in ref_files if any(f in hits for f in tf)]


# =============================================================================
# Scanners — file-level raw-byte prefilter
# =============================================================================


class ScannerError(Exception):
    """A scanner could not produce a trustworthy candidate set — callers fall
    back to scan-all (memory-safe now that the parse cache is bounded)."""


class Scanner(Protocol):
    def files_with_match(
        self, patterns: Sequence[str], files: Sequence[Path]
    ) -> set[Path]: ...


# Regex constructs that can FALSE-NEGATIVE against raw JSONL bytes. JSON string
# encoding rewrites newline/tab/CR/quote/backslash into two-char escapes, and a
# physical JSONL line is one whole entry (line anchors mean something entirely
# different than in extracted text). Patterns matching any of these route to
# the scan-all path instead of the prefilter:
#   "            literal quote (raw has \")
#   \\           literal backslash (raw has \\\\)
#   \s \S        whitespace classes (a real newline in extracted text is a
#                two-char \n in raw bytes)
#   \n \r \t     escaped whitespace literals
#   \W \D        negated classes that match newline
#   \A \Z        string anchors
#   \x \u \0-\9  hex/unicode escapes and backreferences
#   [^           negated character class (matches newline)
#   ^ $          line anchors
#   (?           groups with flags/lookaround (also rust-regex limits)
#   bare .       a bare `.` (including `.?`/`.{n}`) matches exactly one char,
#                so it can require matching one byte where raw bytes hold a
#                two-char JSON escape. `.*`/`.+` are fine — they can absorb the
#                extra byte — and an escaped `\.` is fine too, since a literal
#                dot byte is never rewritten by JSON string escaping.
_RG_UNSAFE = re.compile(
    r'"'
    r"|\\[snrtSWDAZux0-9]"
    r"|\\\\"
    r"|\[\^"
    r"|[\^$]"
    r"|\(\?"
    r"|(?<!\\)\.(?![*+])"
)


def rg_safe(pattern: str) -> bool:
    """True when a raw-byte prefilter cannot drop a true hit for this pattern.

    Scoped to ASCII case folding: rg's `--ignore-case` doesn't Unicode-fold
    the way Python's `re.IGNORECASE` does (e.g. U+0130 İ / U+0131 ı), so a
    non-ASCII-cased literal can still be missed by RgScanner even when this
    returns True. That gap isn't gated here — see the module docstring.
    """
    return not _RG_UNSAFE.search(pattern)


@lru_cache(maxsize=1)
def _rg_path() -> Optional[str]:
    return shutil.which("rg")


# Cumulative argv chars of file paths per rg invocation. Linux ARG_MAX is ~2 MB;
# this leaves generous headroom for the command itself and the environment.
_ARGV_BUDGET = 400_000


class RgScanner:
    """File-level prefilter via ripgrep over raw JSONL bytes.

    One logical scan may fan out over several rg invocations (file lists are
    passed as argv, batched under _ARGV_BUDGET — rg has no --files-from). Exit
    code 0/1 = matched/none; anything else raises ScannerError (a pattern the
    rust regex engine rejects, or an I/O failure mid-scan whose partial output
    can't be trusted as a superset).
    """

    def __init__(self, rg: str) -> None:
        self._rg = rg

    def files_with_match(
        self, patterns: Sequence[str], files: Sequence[Path]
    ) -> set[Path]:
        hits: set[Path] = set()
        if not files or not patterns:
            return hits

        base = [
            self._rg,
            "--files-with-matches",
            "--ignore-case",
            "--no-messages",
            "--no-config",
        ]
        for p in patterns:
            base += ["--regexp", p]
        base.append("--")

        def run(batch: list[str]) -> None:
            if not batch:
                return
            try:
                proc = subprocess.run(
                    base + batch,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=300,
                )
            except (OSError, subprocess.SubprocessError) as e:
                raise ScannerError(str(e))
            if proc.returncode not in (0, 1):
                raise ScannerError(
                    f"rg exited {proc.returncode}: {proc.stderr.strip()[:500]}"
                )
            for line in proc.stdout.splitlines():
                if line:
                    hits.add(Path(line))

        batch: list[str] = []
        size = 0
        for f in files:
            s = str(f)
            batch.append(s)
            size += len(s) + 1
            if size >= _ARGV_BUDGET:
                run(batch)
                batch, size = [], 0
        run(batch)
        return hits


class PyScanner:
    """Streaming line-regex fallback when rg is unavailable.

    Same raw-byte semantics as RgScanner (superset for rg-safe patterns).
    Parses nothing, retains nothing — one line in memory at a time. A file
    that vanished between listing and opening is skipped (a gone file can't
    be parsed later either, so "no match" is consistent); any other open/read
    failure raises ScannerError so the caller falls back to scan-all instead
    of silently under-selecting.
    """

    def files_with_match(
        self, patterns: Sequence[str], files: Sequence[Path]
    ) -> set[Path]:
        try:
            compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
        except re.error as e:
            raise ScannerError(f"pattern failed to compile: {e}")
        hits: set[Path] = set()
        for f in files:
            try:
                with open(f, "r", encoding="utf-8", errors="replace") as fh:
                    for line in fh:
                        if any(rx.search(line) for rx in compiled):
                            hits.add(f)
                            break
            except FileNotFoundError:
                continue
            except OSError as e:
                raise ScannerError(str(e))
        return hits


def make_scanner() -> Scanner:
    """rg when it's on PATH (it ships with Claude Code, so it always should be),
    the streaming Python fallback otherwise."""
    rg = _rg_path()
    return RgScanner(rg) if rg else PyScanner()
