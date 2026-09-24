"""Failure survey — "what broke?" as a first-class query over the corpus.

Before this, failure was not a queryable axis: you could slice transcripts by
project, session, agent, role, pattern and date, but to find failed tool calls
you had to guess what the errors *said* and regex for the prose. The signal was
always structured — `is_error` on every tool result — just unreachable.

This module walks the corpus, pairs every tool call with its result, classifies
the failures (models.FailureKind), and rolls them up. Two design rules:

- **Cost scales with the answer, not the corpus.** `"is_error":true` is a
  literal in the raw JSONL, so ripgrep names the ~43% of files that can possibly
  contribute before anything is parsed. Only those files are parsed, and only
  the ones whose mtime allows an in-window entry.
- **Nothing here holds the corpus in memory.** Aggregation is counters plus a
  bounded set of examples; per-error records are never accumulated.

Denominator scope, stated once because it is load-bearing: `calls` counts tool
invocations in the transcripts that were SCANNED — i.e. those carrying at least
one flagged error. Error-free transcripts are never parsed, so this is not a
corpus-wide denominator. It answers "which tools fail disproportionately in the
sessions where things went wrong", which is the question a failure survey is
actually asking.
"""

from __future__ import annotations

import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .conversion import read_provenance
from .corpus import Corpus, ScannerError, SessionRef
from .models import (
    FailureKind,
    ToolResultContent,
    canonical_server,
    collect_tool_calls,
    split_tool_name,
)
from .parser import load_transcript
from .search import session_sources
from .utils import collapse_ws


# The raw-JSONL literal that names every transcript file able to contribute a
# failure. Authored against the on-disk encoding (hence `matching_files`, which
# has no rg_safe gate): compact serialization writes `"is_error":true`, and the
# optional whitespace keeps the pattern honest if that ever changes.
ERROR_FLAG_PATTERN = r'"is_error":\s*true'

# How much of an error's text an example carries, and how much of it defines an
# "unclassified shape". Short on purpose — the payload orients, it does not
# reproduce the error. Drill in with grep_session / read_turn for the full text.
EXAMPLE_CHARS = 160
SHAPE_CHARS = 60


# =============================================================================
# Aggregation result — plain data the response model renders
# =============================================================================


@dataclass
class FailureExample:
    """One representative failure: enough to recognize it, not to read it."""

    text: str
    tool: str
    session: str
    project: str
    agent: Optional[str] = None


@dataclass
class KindTally:
    kind: FailureKind
    count: int = 0
    sessions: set[str] = field(default_factory=set)
    example: Optional[FailureExample] = None


@dataclass
class ToolTally:
    """One tool's exposure and failure load, accumulated under its FULL name.

    Full name, not `short_name`: `mcp__serverA__search` and
    `mcp__serverB__search` are different tools with different failure rates, and
    29 short names in the live corpus are shared by two or three servers.
    Spellings of the same tool are reconciled once at the end, by
    `_resolve_tool_identities`, which also picks each row's `display` name.
    """

    name: str
    errors: int = 0
    calls: int = 0
    kinds: Counter[FailureKind] = field(default_factory=Counter)
    display: str = ""

    @property
    def server(self) -> Optional[str]:
        """MCP server this tool belongs to; None when the name carries none."""
        return split_tool_name(self.name)[0]

    @property
    def server_key(self) -> Optional[str]:
        """`server`, folded so its spelling variants compare equal."""
        server = self.server
        return canonical_server(server) if server is not None else None

    @property
    def short_name(self) -> str:
        return split_tool_name(self.name)[1]

    def absorb(self, other: "ToolTally") -> None:
        """Fold another recording of the SAME tool into this one."""
        self.errors += other.errors
        self.calls += other.calls
        self.kinds.update(other.kinds)


@dataclass
class SessionTally:
    session: str
    project: str
    errors: int = 0
    kinds: Counter[FailureKind] = field(default_factory=Counter)
    first: Optional[datetime] = None
    last: Optional[datetime] = None


@dataclass
class ShapeTally:
    """A distinct *shape* of unclassified failure, with how often it recurred.

    Grouping by leading text is what turns a pile of unknown errors into a
    ranked list of things worth naming — the frequency-analysis move, done for
    the caller so it never has to page through raw errors to find the pattern.
    """

    shape: str
    count: int = 0
    sessions: set[str] = field(default_factory=set)
    example: Optional[FailureExample] = None


@dataclass
class FailureSurvey:
    """Everything a survey learned. Rendered (and capped) by the response model."""

    total: int = 0
    sessions_affected: int = 0
    sessions_scanned: int = 0
    transcripts_scanned: int = 0
    cascade_suppressed: int = 0
    prefiltered: bool = True
    after: Optional[datetime] = None
    before: Optional[datetime] = None
    kinds_filter: Optional[list[FailureKind]] = None
    by_kind: list[KindTally] = field(default_factory=list)
    by_tool: list[ToolTally] = field(default_factory=list)
    by_session: list[SessionTally] = field(default_factory=list)
    unclassified: list[ShapeTally] = field(default_factory=list)
    unclassified_total: int = 0


# =============================================================================
# Scan
# =============================================================================


def _as_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Naive datetimes are read as UTC — same convention as _filter_by_date."""
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def error_candidate_files(
    corpus: Corpus,
) -> tuple[list[tuple[SessionRef, list[Path]]], bool]:
    """Transcript files that can possibly hold a failure, per session.

    THE error prefilter — `survey_failures` and search's `errors_only` both go
    through here, so the degrade path exists once. Returns (pairs, prefiltered).
    A scanner failure is not fatal: it falls back to every transcript, which is
    slow but never silently wrong. Callers surface `prefiltered` so a degraded
    run is legible rather than mysterious.
    """
    try:
        return corpus.matching_files([ERROR_FLAG_PATTERN]), True
    except ScannerError as e:
        # stderr, never stdout — stdout is the stdio MCP protocol channel.
        print(
            f"[cc-explorer failures] error prefilter failed ({e}); scanning all "
            f"transcripts",
            file=sys.stderr,
        )
        return [(ref, ref.transcript_files()) for ref in corpus.refs], False


def narrow_to_error_sessions(corpus: Corpus) -> tuple[Corpus, bool]:
    """The session-granular view of `error_candidate_files`, for errors_only."""
    pairs, prefiltered = error_candidate_files(corpus)
    return Corpus([ref for ref, _ in pairs]), prefiltered


def _example(
    result: ToolResultContent,
    tool: str,
    ref: SessionRef,
    project: str,
    agent_id: Optional[str],
) -> FailureExample:
    """Build a representative failure. Excerpting error text is the expensive
    part of a survey, so this is called only where a tally has no example yet —
    at most once per kind and once per unclassified shape."""
    return FailureExample(
        text=collapse_ws(result.text, EXAMPLE_CHARS),
        tool=tool,
        session=ref.session_id,
        project=project,
        agent=agent_id,
    )


def _resolve_tool_identities(tallies: list[ToolTally]) -> list[ToolTally]:
    """Collapse each tool to ONE row, then name it as briefly as stays unambiguous.

    A transcript does not name a tool the same way every time, and keyed by the
    raw name every spelling is its own row — so one tool's failures land in two
    or three of them. Two spelling drifts are live in the corpus:

    - **Server spelling.** The same server appears as
      `plugin_project-mining_cc-explorer` and `plugin_project_mining_cc_explorer`
      (and `claude-in-chrome` / `Claude_in_Chrome`), because the harness
      sanitizes a configured name differently across versions. `server_key`
      folds those together; the surviving row keeps the most-called spelling,
      so the name shown is the one the caller will recognize.
    - **Missing server.** A model that reached an MCP tool through the
      deferred-tool path recorded it BARE (`search_projects`) while every other
      call recorded it qualified. Worse, the stray bare row then looks like a
      second server exposing that short name, so the display step below expands
      its qualified twin to the full name while sibling tools stay short — the
      mixed naming of issue #67. A server-less tally is therefore folded into
      the qualified one when EXACTLY ONE server in the scan exposes that tool
      name; with two or more there is nothing to attribute it to, and it stays
      its own row rather than being assigned to a server by coin flip.
      Attribution assumes a bare name matching exactly one server's tool IS
      that tool — in the whole live corpus that is the only bare/qualified name
      collision, and a built-in allow-list to guard the hypothetical
      `mcp__x__Bash` would go stale faster than it would help.

    Display then names each surviving row as briefly as is still unambiguous:
    `Bash` stays `Bash` and a lone MCP tool shows as `grep_session`, but two
    servers exposing the same short name keep their full `mcp__server__tool`
    names — otherwise their rows merge and the `error_rate` denominator, which
    is the whole point of `by_tool`, becomes a blend of two different tools.
    """
    # One row per (server, tool). Most-called spelling first, so it is the one
    # that survives to name the row.
    merged: dict[tuple[Optional[str], str], ToolTally] = {}
    for t in sorted(tallies, key=lambda t: (-t.calls, t.name)):
        key = (t.server_key, t.short_name)
        held = merged.get(key)
        if held is None:
            merged[key] = t
        else:
            held.absorb(t)

    qualified: dict[str, list[ToolTally]] = {}
    for (server, tool), t in merged.items():
        if server is not None:
            qualified.setdefault(tool, []).append(t)

    resolved: list[ToolTally] = []
    for (server, tool), t in merged.items():
        twins = qualified.get(tool, []) if server is None else []
        if len(twins) == 1:
            twins[0].absorb(t)
            continue
        resolved.append(t)

    shared = Counter(t.short_name for t in resolved)
    for t in resolved:
        t.display = t.short_name if shared[t.short_name] == 1 else t.name
    return resolved


def survey_failures(
    projects: Optional[list[str]] = None,
    after: Optional[datetime] = None,
    before: Optional[datetime] = None,
    kinds: Optional[Sequence[FailureKind]] = None,
    include_cascade: bool = False,
) -> FailureSurvey:
    """Roll every failed tool call in scope into kind / tool / session tallies.

    `kinds` keeps only those FailureKinds in the error tallies (denominators
    still count every call, since they measure exposure, not failure).
    `include_cascade` re-admits the sibling-cancellation artifacts that are
    suppressed by default — 1246 of them in a real corpus, every one of which
    triples a parallel batch's apparent failure count if left in. Naming
    `cascade` in `kinds` implies `include_cascade`: otherwise the suppression
    runs first and `kinds=['cascade']` is a query that can never return a row.
    """
    lo = _as_utc(after)
    hi = _as_utc(before)
    wanted = set(kinds) if kinds else None
    keep_cascade = include_cascade or (
        wanted is not None and FailureKind.cascade in wanted
    )

    corpus = Corpus.discover(projects)
    pairs, prefiltered = error_candidate_files(corpus)

    survey = FailureSurvey(
        after=lo, before=hi, prefiltered=prefiltered,
        kinds_filter=list(kinds) if kinds else None,
    )

    kind_tallies: dict[FailureKind, KindTally] = {}
    tool_tallies: dict[str, ToolTally] = {}
    session_tallies: dict[str, SessionTally] = {}
    shape_tallies: dict[str, ShapeTally] = {}

    # mtime pruning: transcripts are append-only, so a file last written before
    # the window opened cannot hold an in-window entry. Mirrors activity.py.
    lo_ts = lo.timestamp() if lo else None

    for ref, files in pairs:
        # A conversion artifact is a copy of a transcript already in scope —
        # counting it would double every failure the original recorded.
        if read_provenance(ref.path) is not None:
            continue

        sid = ref.session_id
        project = ref.project_path or ""
        touched = False

        # `session_sources` already knows a session's searchable corpus: the
        # main transcript plus every subagent body, agent ids attached and
        # conversion artifacts dropped. Intersect it with the rg hits rather
        # than re-deriving agent ids from filenames and re-reading provenance.
        hit_files = set(files)
        for source in session_sources(ref.path):
            path = source.path
            if path not in hit_files:
                continue
            if lo_ts is not None:
                try:
                    if path.stat().st_mtime < lo_ts:
                        continue
                except OSError:
                    continue

            try:
                entries = load_transcript(path)
            except Exception:
                continue
            survey.transcripts_scanned += 1
            touched = True
            agent_id = source.agent_id

            for call in collect_tool_calls(entries):
                ts = call.timestamp
                if lo is not None and (ts is None or ts < lo):
                    continue
                if hi is not None and (ts is None or ts >= hi):
                    continue

                tally = tool_tallies.get(call.name)
                if tally is None:
                    tally = tool_tallies[call.name] = ToolTally(name=call.name)
                tally.calls += 1

                # Gate on the flag before classifying: `has_failure` is a bool
                # read, `failure` runs the whole rule table, and the vast
                # majority of scanned calls succeeded.
                if not call.has_failure:
                    continue
                assert call.result is not None  # has_failure implies a result
                kind = call.result.failure
                assert kind is not None
                if kind is FailureKind.cascade and not keep_cascade:
                    survey.cascade_suppressed += 1
                    continue
                if wanted is not None and kind not in wanted:
                    continue

                survey.total += 1
                tally.errors += 1
                tally.kinds[kind] += 1

                kt = kind_tallies.get(kind)
                if kt is None:
                    kt = kind_tallies[kind] = KindTally(kind=kind)
                kt.count += 1
                kt.sessions.add(sid)
                if kt.example is None:
                    kt.example = _example(
                        call.result, tally.short_name, ref, project, agent_id
                    )

                st = session_tallies.get(sid)
                if st is None:
                    st = session_tallies[sid] = SessionTally(
                        session=ref.session_id, project=project
                    )
                st.errors += 1
                st.kinds[kind] += 1
                if ts is not None:
                    if st.first is None or ts < st.first:
                        st.first = ts
                    if st.last is None or ts > st.last:
                        st.last = ts

                if kind is FailureKind.unclassified:
                    shape = collapse_ws(call.result.text, SHAPE_CHARS)
                    sh = shape_tallies.get(shape)
                    if sh is None:
                        sh = shape_tallies[shape] = ShapeTally(shape=shape)
                    sh.count += 1
                    sh.sessions.add(sid)
                    if sh.example is None:
                        sh.example = _example(
                            call.result, tally.short_name, ref, project, agent_id
                        )

        if touched:
            survey.sessions_scanned += 1

    survey.sessions_affected = len(session_tallies)
    survey.by_kind = sorted(
        kind_tallies.values(), key=lambda k: k.count, reverse=True
    )
    # Resolved over EVERY tool seen, not just the error-bearing rows that get
    # rendered: if two servers expose `search` and only one of them failed,
    # calling that row `search` reads as "all search calls", which it is not —
    # and a bare-named alias can only be attributed by looking at the whole scan.
    survey.by_tool = sorted(
        (t for t in _resolve_tool_identities(list(tool_tallies.values())) if t.errors),
        key=lambda t: t.errors,
        reverse=True,
    )
    survey.by_session = sorted(
        session_tallies.values(), key=lambda s: s.errors, reverse=True
    )
    survey.unclassified = sorted(
        shape_tallies.values(), key=lambda s: s.count, reverse=True
    )
    survey.unclassified_total = sum(s.count for s in survey.unclassified)
    return survey


__all__ = [
    "ERROR_FLAG_PATTERN",
    "FailureExample",
    "FailureSurvey",
    "KindTally",
    "SessionTally",
    "ShapeTally",
    "ToolTally",
    "survey_failures",
]
