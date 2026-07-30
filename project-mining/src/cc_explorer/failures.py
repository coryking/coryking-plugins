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
    collect_tool_calls,
)
from .parser import load_transcript
from .utils import PrefixId


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
    session: PrefixId
    project: str
    agent: Optional[PrefixId] = None


@dataclass
class KindTally:
    kind: FailureKind
    count: int = 0
    sessions: set[str] = field(default_factory=set)
    example: Optional[FailureExample] = None


@dataclass
class ToolTally:
    tool: str
    errors: int = 0
    calls: int = 0
    kinds: Counter[FailureKind] = field(default_factory=Counter)


@dataclass
class SessionTally:
    session: PrefixId
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


def _agent_id_for(path: Path, main: Path) -> Optional[PrefixId]:
    """The subagent id a transcript file belongs to, or None for the main file."""
    if path == main:
        return None
    stem = path.stem
    return PrefixId(stem[len("agent-"):]) if stem.startswith("agent-") else None


def _excerpt(text: str, limit: int) -> str:
    """Whitespace-collapsed leading slice — the form both shapes and examples use."""
    return " ".join(text.split())[:limit]


def _candidate_files(
    corpus: Corpus,
) -> tuple[list[tuple[SessionRef, list[Path]]], bool]:
    """Transcript files that can possibly hold a failure, per session.

    Returns (pairs, prefiltered). A scanner failure is not fatal — it degrades
    to scanning everything, which is slow but never silently wrong. The caller
    surfaces `prefiltered` so a degraded run is legible rather than mysterious.
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
    suppressed by default — 774+ of them in a real corpus, every one of which
    triples a parallel batch's apparent failure count if left in.
    """
    lo = _as_utc(after)
    hi = _as_utc(before)
    wanted = set(kinds) if kinds else None

    corpus = Corpus.discover(projects)
    pairs, prefiltered = _candidate_files(corpus)

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

        sid = ref.session_id.full
        project = ref.project_path or ""
        touched = False

        for path in files:
            if lo_ts is not None:
                try:
                    if path.stat().st_mtime < lo_ts:
                        continue
                except OSError:
                    continue
            if path != ref.path and read_provenance(path) is not None:
                continue

            try:
                entries = load_transcript(path)
            except Exception:
                continue
            survey.transcripts_scanned += 1
            touched = True
            agent_id = _agent_id_for(path, ref.path)

            for call in collect_tool_calls(entries):
                ts = call.timestamp
                if lo is not None and (ts is None or ts < lo):
                    continue
                if hi is not None and (ts is None or ts >= hi):
                    continue

                tool = call.short_name
                tally = tool_tallies.get(tool)
                if tally is None:
                    tally = tool_tallies[tool] = ToolTally(tool=tool)
                tally.calls += 1

                kind = call.failure
                if kind is None:
                    continue
                if kind is FailureKind.cascade and not include_cascade:
                    survey.cascade_suppressed += 1
                    continue
                if wanted is not None and kind not in wanted:
                    continue

                assert call.result is not None  # failure implies a result
                text = _excerpt(call.result.text, EXAMPLE_CHARS)
                example = FailureExample(
                    text=text,
                    tool=tool,
                    session=ref.session_id,
                    project=project,
                    agent=agent_id,
                )

                survey.total += 1
                tally.errors += 1
                tally.kinds[kind] += 1

                kt = kind_tallies.get(kind)
                if kt is None:
                    kt = kind_tallies[kind] = KindTally(kind=kind)
                kt.count += 1
                kt.sessions.add(sid)
                if kt.example is None:
                    kt.example = example

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
                    shape = text[:SHAPE_CHARS]
                    sh = shape_tallies.get(shape)
                    if sh is None:
                        sh = shape_tallies[shape] = ShapeTally(shape=shape)
                    sh.count += 1
                    sh.sessions.add(sid)
                    if sh.example is None:
                        sh.example = example

        if touched:
            survey.sessions_scanned += 1

    survey.sessions_affected = len(session_tallies)
    survey.by_kind = sorted(
        kind_tallies.values(), key=lambda k: k.count, reverse=True
    )
    survey.by_tool = sorted(
        (t for t in tool_tallies.values() if t.errors),
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
