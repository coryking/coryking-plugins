"""Contract tests for PrefixId integration points.

Pins the output shape at the boundaries we'll touch during the refactor:
- format_entry_line pipe-delimited format
- Response model .model_dump() serialization
- Pydantic field with PrefixId type
"""

from pydantic import BaseModel

from cc_explorer.formatting import format_entry_line
from cc_explorer.responses import GrepSessionResponse, ReadTurnResponse
from cc_explorer.search import MatchHit
from cc_explorer.utils import PrefixId

from .conftest import FULL_UUID


# =============================================================================
# format_entry_line output shape
# =============================================================================


class TestFormatEntryLine:
    def test_pipe_format_has_8char_turn_id(self, human_entry):
        """The turn_id field in pipe output must be 8 chars."""
        line = format_entry_line(human_entry)
        parts = line.split("|")
        # format: timestamp|role|turn_id|full_length|display
        assert len(parts) == 5
        turn_id = parts[2]
        assert turn_id == "a9529cc1"
        assert len(turn_id) == 8

    def test_pipe_format_structure(self, human_entry):
        """Verify the overall pipe-delimited structure."""
        line = format_entry_line(human_entry)
        parts = line.split("|")
        assert parts[0].isdigit()  # timestamp
        assert parts[1] == "U"  # role
        assert len(parts[2]) == 8  # turn_id
        assert parts[3].isdigit()  # full_length


# =============================================================================
# Response model serialization
# =============================================================================


class TestGrepSessionResponseSerialization:
    def test_session_id_is_8_chars(self, human_entry):
        match = MatchHit(
            session_id=FULL_UUID,
            turn_uuid=FULL_UUID,
            entry=human_entry,
            context_before=[],
            context_after=[],
        )
        resp = GrepSessionResponse.from_matches(
            session_id=FULL_UUID,
            matches=[match],
            total=1,
            limit=30,
        )
        dumped = resp.model_dump()
        assert len(dumped["session_id"]) == 8
        assert dumped["session_id"] == "a9529cc1"


class TestReadTurnResponseSerialization:
    def test_ids_are_8_chars(self, human_entry, session_info):
        resp = ReadTurnResponse.from_entries(session_info, FULL_UUID, [human_entry])
        dumped = resp.model_dump()
        assert len(dumped["session_id"]) == 8
        assert len(dumped["turn_id"]) == 8


# =============================================================================
# Pydantic model with PrefixId field
# =============================================================================


class TestPydanticFieldSerialization:
    def test_prefix_id_field_serializes_as_short_string(self):
        """When a Pydantic model has a PrefixId field, model_dump() gives the short form."""

        class MyModel(BaseModel):
            session_id: PrefixId

        m = MyModel(session_id=PrefixId(FULL_UUID))
        dumped = m.model_dump()
        assert dumped["session_id"] == "a9529cc1"
        assert isinstance(dumped["session_id"], str)

    def test_prefix_id_field_coerces_from_raw_string(self):
        """Pydantic should accept a plain str and coerce to PrefixId."""

        class MyModel(BaseModel):
            session_id: PrefixId

        m = MyModel(session_id=FULL_UUID)
        assert isinstance(m.session_id, PrefixId)
        assert m.session_id.full == FULL_UUID
