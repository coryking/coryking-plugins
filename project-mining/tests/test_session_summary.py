"""Tests for the SessionSummary row every session-listing tool returns.

The row is a product surface: it is what a caller reads to decide which session
to open, search, or convert. Signals here must be cheap to scan and must not pad
the payload when they say nothing — a zero is noise on every row, so
zero-valued signals are omitted rather than serialized.

Pinned here: `compactions`, the pre-interview signal that a session's early
history was summarized away (present only when nonzero).
"""

from datetime import datetime, timezone
from pathlib import Path

from cc_explorer.models import CompactionEvent, TranscriptStats
from cc_explorer.responses import SessionSummary
from cc_explorer.search import SessionInfo
from cc_explorer.utils import PrefixId

TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
SESSION_ID = "a9529cc1-b576-5fd3-9f1a-1234567890ab"


def _session(stats: TranscriptStats) -> SessionInfo:
    return SessionInfo(
        session_id=PrefixId(SESSION_ID),
        path=Path("/tmp/fake.jsonl"),
        title="test session",
        first_timestamp=TS,
        message_count=10,
        stats=stats,
    )


def _compaction(turn: int) -> CompactionEvent:
    return CompactionEvent(turn=turn, from_tokens=50000, to_tokens=12000, drop_pct=76.0)


def test_compactions_omitted_when_none():
    """A session that never compacted spends no payload saying so."""
    row = SessionSummary.from_session_info(_session(TranscriptStats(context_tokens=9000)))
    assert row.compactions is None
    assert "compactions" not in row.model_dump()


def test_compactions_reports_event_count():
    """Nonzero compactions are surfaced as a count — how lossy the early history is."""
    stats = TranscriptStats(
        context_tokens=12000,
        compaction_events=[_compaction(12), _compaction(40)],
    )
    row = SessionSummary.from_session_info(_session(stats))
    assert row.compactions == 2
    assert row.model_dump()["compactions"] == 2
