"""Tests for the SessionSummary row every session-listing tool returns.

The row is a product surface: it is what a caller reads to decide which session
to open, search, or convert. Signals here must be cheap to scan and must not pad
the payload when they say nothing — a zero is noise on every row, so
zero-valued signals are omitted rather than serialized.

Pinned here: `compactions`, the pre-interview signal that a session's early
history was summarized away (present only when nonzero) — both its presentation
on the row and what TranscriptStats will and won't count as a compaction.
"""

from datetime import datetime, timezone
from pathlib import Path

from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    CompactionEvent,
    TextContent,
    TranscriptStats,
    UsageInfo,
)
from cc_explorer.responses import SessionSummary
from cc_explorer.search import SessionInfo

TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
SESSION_ID = "a9529cc1-b576-5fd3-9f1a-1234567890ab"


def _session(stats: TranscriptStats) -> SessionInfo:
    return SessionInfo(
        session_id=SESSION_ID,
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


# =============================================================================
# What actually counts as a compaction (TranscriptStats.from_entries)
# =============================================================================
#
# The signal is only useful if it's real. A `<synthetic>` assistant turn — the
# placeholder Claude Code writes for an API error or an interrupt — reports an
# all-zero usage block. That is the absence of a measurement, not a context
# drop, and on the live corpus these turns produced the majority of all reported
# "compactions".


def _turn(uuid_hex: str, input_tokens: int, model: str = "claude-opus-4") -> AssistantTranscriptEntry:
    """One assistant turn reporting `input_tokens` of context."""
    return AssistantTranscriptEntry(
        uuid=uuid_hex,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="assistant",
        message=AssistantMessageModel(
            id="m",
            type="message",
            role="assistant",
            model=model,
            content=[TextContent(type="text", text="answer")],
            usage=UsageInfo(
                input_tokens=input_tokens,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
                output_tokens=5,
            ),
        ),
    )


def _synthetic_turn(uuid_hex: str) -> AssistantTranscriptEntry:
    """An API-error / interrupt placeholder: model `<synthetic>`, all-zero usage."""
    return _turn(uuid_hex, 0, model="<synthetic>")


UUIDS = [f"{i:08x}-aaaa-bbbb-cccc-dddddddddddd" for i in range(4)]


def test_zero_usage_turn_is_not_a_compaction():
    """A `<synthetic>` turn between two real turns reports no context, not a drop."""
    stats = TranscriptStats.from_entries([
        _turn(UUIDS[0], 50000),
        _synthetic_turn(UUIDS[1]),
        _turn(UUIDS[2], 52000),
    ])
    assert stats.compaction_events == []
    assert stats.context_tokens == 52000


def test_zero_usage_turn_does_not_mask_the_next_real_compaction():
    """The synthetic turn must not become the baseline the next turn is judged against."""
    stats = TranscriptStats.from_entries([
        _turn(UUIDS[0], 50000),
        _synthetic_turn(UUIDS[1]),
        _turn(UUIDS[2], 12000),
    ])
    assert len(stats.compaction_events) == 1
    event = stats.compaction_events[0]
    assert (event.from_tokens, event.to_tokens) == (50000, 12000)


def test_genuine_context_drop_is_still_reported():
    """The real thing — a >30% drop between two measured turns — still counts."""
    stats = TranscriptStats.from_entries([
        _turn(UUIDS[0], 50000),
        _turn(UUIDS[1], 12000),
    ])
    assert [e.to_tokens for e in stats.compaction_events] == [12000]
    assert stats.compaction_count == 1
