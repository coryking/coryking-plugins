"""Complete identifiers in response models and pipe-delimited entry lines.

Pins the output shape at the boundaries we'll touch during the refactor:
- format_entry_line pipe-delimited format
- Response model .model_dump() serialization
"""

from cc_explorer.formatting import format_entry_line
from cc_explorer.responses import GrepSessionResponse, ReadTurnResponse
from cc_explorer.search import MatchHit

from .conftest import FULL_UUID


# =============================================================================
# format_entry_line output shape
# =============================================================================


class TestFormatEntryLine:
    def test_pipe_format_has_full_turn_id(self, human_entry):
        """The turn_id field in pipe output must be complete and lead the line."""
        line = format_entry_line(human_entry, truncate=500)
        parts = line.split("|")
        # format: turn_id|timestamp|role|full_length|display
        assert len(parts) == 5
        turn_id = parts[0]
        assert turn_id == FULL_UUID
        assert len(turn_id) == 36

    def test_pipe_format_structure(self, human_entry):
        """Verify the overall pipe-delimited structure."""
        line = format_entry_line(human_entry, truncate=500)
        parts = line.split("|")
        assert len(parts[0]) == 36  # turn_id
        assert parts[1].isdigit()  # timestamp
        assert parts[2] == "U"  # role
        assert parts[3].isdigit()  # full_length


# =============================================================================
# Response model serialization
# =============================================================================


class TestGrepSessionResponseSerialization:
    def test_session_is_complete(self, human_entry):
        match = MatchHit(
            session_id=FULL_UUID,
            turn_uuid=FULL_UUID,
            entry=human_entry,
            context_before=[],
            context_after=[],
        )
        resp = GrepSessionResponse.from_pattern_results(
            session_id=FULL_UUID,
            results=[("hello", [match], 1)],
            truncate=500,
        )
        dumped = resp.model_dump()
        assert len(dumped["session"]) == 36
        assert dumped["session"] == FULL_UUID


class TestReadTurnResponseSerialization:
    def test_ids_are_complete(self, human_entry, session_info):
        resp = ReadTurnResponse.from_entries(
            session_info, FULL_UUID, [human_entry], truncate=0
        )
        dumped = resp.model_dump()
        assert len(dumped["session"]) == 36
        assert len(dumped["turn"]) == 36
