"""Boundary validation for read_turn turn ID input.

Catches two real bugs from production:
- agents passing turn=""
- agents passing the unix-timestamp field instead of the turn UUID
"""

import pytest

from cc_explorer.mcp_server import _validate_turn_id
from fastmcp.exceptions import ToolError


class TestValidateTurnId:
    def test_accepts_8char_hex_prefix(self):
        _validate_turn_id("a1b2c3d4")  # no raise

    def test_accepts_full_uuid(self):
        _validate_turn_id("a9529cc1-b576-5fd3-9f1a-1234567890ab")  # no raise

    def test_rejects_empty(self):
        with pytest.raises(ToolError, match="non-empty"):
            _validate_turn_id("")

    def test_rejects_unix_timestamp(self):
        # 10-digit decimal — what agents pull from the timestamp field
        # in the pipe-delimited format when they grab the wrong column.
        with pytest.raises(ToolError, match="not a valid UUID"):
            _validate_turn_id("1775406360")

    def test_rejects_word(self):
        with pytest.raises(ToolError, match="not a valid UUID"):
            _validate_turn_id("hello")

    def test_rejects_short_prefix(self):
        # 7 chars is one shy of the canonical 8-char prefix.
        with pytest.raises(ToolError, match="not a valid UUID"):
            _validate_turn_id("a1b2c3d")
