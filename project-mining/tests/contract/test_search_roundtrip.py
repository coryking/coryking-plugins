"""Search identifiers round-trip unchanged; saved legacy prefixes still resolve."""

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
from cc_explorer.search import SessionInfo, get_turn_context, search

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SESSION_ID = "bbbbbbbb-1111-2222-3333-444444444444"
TURN_UUID = "a9529cc1-b576-5fd3-9f1a-1234567890ab"
TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)


def _make_session() -> SessionInfo:
    return SessionInfo(
        session_id=SESSION_ID,
        path=Path("fake.jsonl"),
        title="test session",
        first_timestamp=TS,
        message_count=2,
        stats=TranscriptStats(),
    )


def _make_entries():
    """A single user message — enough to exercise search() -> get_turn_context()."""
    return [
        HumanEntry(
            uuid=TURN_UUID,
            timestamp=TS,
            sessionId=SESSION_ID,
            type="user",
            message=UserMessageModel(
                role="user",
                content=[TextContent(type="text", text="hello world")],
            ),
        ),
    ]


# ---------------------------------------------------------------------------
# Contract: search() -> get_turn_context() round-trip
# ---------------------------------------------------------------------------


class TestSearchToReadTurnContract:
    """IDs produced by search() must be consumable by get_turn_context()."""

    @pytest.fixture(autouse=True)
    def _patch_load(self):
        entries = _make_entries()
        with patch("cc_explorer.providers.claude.load_transcript", return_value=entries):
            yield

    @pytest.fixture
    def sessions(self):
        return [_make_session()]

    def test_full_uuid_from_search_resolves(self, sessions):
        """Baseline: get_turn_context works with the full UUID from search."""
        result = search(sessions, "hello")
        assert result.matches, "search should find 'hello' in the user message"

        turn_uuid = result.matches[0].turn_uuid
        resolved = get_turn_context(sessions, turn_uuid)

        assert resolved is not None, (
            f"get_turn_context could not resolve full turn_uuid={turn_uuid!r}"
        )

    def test_prefix_from_search_resolves(self, sessions):
        """A unique prefix saved by an earlier caller still locates the full turn."""
        result = search(sessions, "hello")
        assert result.matches

        # Simulate a saved legacy citation.
        full_turn_uuid = result.matches[0].turn_uuid
        prefix = str(full_turn_uuid)[:8]

        resolved = get_turn_context(sessions, prefix)

        assert resolved is not None, (
            f"get_turn_context could not resolve prefix={prefix!r} "
            f"(from full={full_turn_uuid!r}). "
            f"This is the grep_session -> read_turn contract: "
            f"IDs returned by search must be resolvable."
        )
        assert len(resolved.entries) > 0, "should return the matched turn plus context"
