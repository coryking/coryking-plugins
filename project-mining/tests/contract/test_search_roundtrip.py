"""Contract test: IDs returned by search() are resolvable by get_turn_context().

This is a regression test for a bug where grep_session returned turn IDs that
read_turn couldn't find. The root cause was that get_turn_context used != (which
dispatches to str.__ne__ on PrefixId, doing exact comparison) instead of using
PrefixId's prefix-aware equality.

Test level: integration/contract. Neither search() nor get_turn_context() is
broken in isolation — the bug is in the contract between them. search() returns
turn UUIDs that get serialized to 8-char prefixes at the MCP boundary;
get_turn_context() must accept those prefixes back.

Reference: Fowler, "Contract Test" — verifies two components agree on the
shape and semantics of their shared interface.
"""

from datetime import datetime, timezone
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
        path="fake.jsonl",
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
        with patch("cc_explorer.search.load_transcript", return_value=entries):
            yield

    @pytest.fixture
    def sessions(self):
        return [_make_session()]

    def test_full_uuid_from_search_resolves(self, sessions):
        """Baseline: get_turn_context works with the full UUID from search."""
        result = search(sessions, "hello")
        assert result.matches, "search should find 'hello' in the user message"

        turn_uuid = result.matches[0].turn_uuid
        session, entries = get_turn_context(sessions, turn_uuid)

        assert session is not None, (
            f"get_turn_context could not resolve full turn_uuid={turn_uuid!r}"
        )

    def test_prefix_from_search_resolves(self, sessions):
        """The actual bug: search returns a PrefixId, MCP serializes to 8-char
        prefix, read_turn passes that prefix back to get_turn_context.

        get_turn_context must resolve prefix IDs, not just full UUIDs.
        """
        result = search(sessions, "hello")
        assert result.matches

        # Simulate the MCP serialization boundary: turn_uuid -> 8-char prefix
        full_turn_uuid = result.matches[0].turn_uuid
        prefix = str(full_turn_uuid)[:8]

        session, entries = get_turn_context(sessions, prefix)

        assert session is not None, (
            f"get_turn_context could not resolve prefix={prefix!r} "
            f"(from full={full_turn_uuid!r}). "
            f"This is the grep_session -> read_turn contract: "
            f"IDs returned by search must be resolvable."
        )
        assert len(entries) > 0, "should return the matched turn plus context"
