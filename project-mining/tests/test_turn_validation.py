"""Boundary validation for read_turn turn ID input.

Catches two real bugs from production:
- agents passing turn=""
- agents passing the unix-timestamp field instead of the turn UUID
"""

import pytest

from cc_explorer.mcp_server import _validate_turn_id
from fastmcp.exceptions import ToolError


class TestValidateTurnId:
    @pytest.mark.parametrize("turn", ["a1b2c3d4", "a9529cc1-b576-5fd3-9f1a-1234567890ab"])
    def test_accepts_turn_identifier(self, turn):
        _validate_turn_id(turn)

    @pytest.mark.parametrize(
        "turn,message",
        [
            ("", "non-empty"),
            ("1775406360", "not a valid UUID"),  # timestamp column mistaken for ID
            ("hello", "not a valid UUID"),
            ("a1b2c3d", "not a valid UUID"),  # one short of the 8-character prefix
        ],
    )
    def test_rejects_invalid_turn_identifier(self, turn, message):
        with pytest.raises(ToolError, match=message):
            _validate_turn_id(turn)
