"""Tests for triage_multi — single-pass multi-pattern triage.

The behavioral contract: triage_multi(sessions, patterns) produces the same
PatternTriageResults as calling triage() once per pattern, but loads each
session's transcript only once regardless of pattern count.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_explorer.models import (
    HumanEntry,
    TextContent,
    TranscriptStats,
    UserMessageModel,
)
from cc_explorer.search import SessionInfo, triage, triage_multi


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_A_ID = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B_ID = "bbbbbbbb-1111-2222-3333-444444444444"
TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)


def _human(text: str, uuid: str = "11111111-aaaa-bbbb-cccc-dddddddddddd") -> HumanEntry:
    return HumanEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_A_ID,
        type="user",
        message=UserMessageModel(role="user", content=[TextContent(type="text", text=text)]),
    )


def _session(session_id: str, path: str = "fake.jsonl") -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        path=Path(path),
        title="test session",
        first_timestamp=TS,
        message_count=5,
        stats=TranscriptStats(),
    )


# Session A: talks about databases and sandwich filters
ENTRIES_A = [
    _human("we need to capture everything in the database"),
    _human("now let's do the sandwich filter", uuid="33333333-aaaa-bbbb-cccc-dddddddddddd"),
]

# Session B: talks about pruning and rendering
ENTRIES_B = [
    _human("the double pruning strategy looks good"),
    _human("let's check the rendering pipeline", uuid="44444444-aaaa-bbbb-cccc-dddddddddddd"),
]


def _load_transcript_side_effect(sessions_entries: dict[str, list]):
    """Create a side_effect function that returns entries based on session path."""
    def _load(path):
        return sessions_entries.get(str(path), [])
    return _load


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestTriageMulti:
    @pytest.fixture(autouse=True)
    def _patch_load(self):
        side_effect = _load_transcript_side_effect({
            "a.jsonl": ENTRIES_A,
            "b.jsonl": ENTRIES_B,
        })
        with patch("cc_explorer.search.load_transcript", side_effect=side_effect):
            yield

    @pytest.fixture
    def two_sessions(self):
        return [
            _session(SESSION_A_ID, "a.jsonl"),
            _session(SESSION_B_ID, "b.jsonl"),
        ]

    def test_single_pattern_matches(self, two_sessions):
        """One pattern finds the right sessions with correct counts."""
        results = triage_multi(two_sessions, ["database"])

        assert len(results) == 1
        pat, hits = results[0]
        assert pat == "database"
        assert len(hits) == 1
        assert hits[0].session.session_id == SESSION_A_ID
        assert hits[0].count == 1  # only user entries by default

    def test_multiple_patterns_correct_counts(self, two_sessions):
        """Multiple patterns each get correct per-session counts."""
        results = triage_multi(two_sessions, ["database", "prun", "sandwich"])

        results_by_pattern = {pat: hits for pat, hits in results}

        # "database" — only in session A (user message)
        assert len(results_by_pattern["database"]) == 1
        assert results_by_pattern["database"][0].session.session_id == SESSION_A_ID

        # "prun" (matches "pruning") — only in session B (user message)
        assert len(results_by_pattern["prun"]) == 1
        assert results_by_pattern["prun"][0].session.session_id == SESSION_B_ID

        # "sandwich" — only in session A
        assert len(results_by_pattern["sandwich"]) == 1
        assert results_by_pattern["sandwich"][0].session.session_id == SESSION_A_ID

    def test_no_matches_returns_empty(self, two_sessions):
        """Patterns that match nothing yield empty result lists."""
        results = triage_multi(two_sessions, ["zzzznotfound", "xyznonexistent"])

        assert len(results) == 2
        for pat, hits in results:
            assert hits == []

    def test_equivalent_to_individual_triage(self, two_sessions):
        """triage_multi produces the same results as calling triage() per pattern."""
        patterns = ["database", "prun", "sandwich", "rendering"]

        multi_results = triage_multi(two_sessions, patterns)

        for pat, multi_hits in multi_results:
            individual_hits = triage(two_sessions, pat)
            assert len(multi_hits) == len(individual_hits), f"Mismatch for pattern {pat!r}"
            for mh, ih in zip(multi_hits, individual_hits):
                assert mh.session.session_id == ih.session.session_id
                assert mh.count == ih.count

    def test_loads_transcript_once_per_session(self, two_sessions):
        """With N patterns and M sessions, load_transcript is called M times, not N*M."""
        with patch("cc_explorer.search.load_transcript") as mock_load:
            mock_load.side_effect = _load_transcript_side_effect({
                "a.jsonl": ENTRIES_A,
                "b.jsonl": ENTRIES_B,
            })
            triage_multi(two_sessions, ["database", "prun", "sandwich"])

            assert mock_load.call_count == 2  # once per session, not 3x per session
