"""Tests for SearchProjectResponse output format.

Covers:
1. Examples include date alongside session_id: 'session_id|YYYY-MM-DD|excerpt'
2. sessions field is an integer count, not a list of formatted strings
3. No literal '\\n' in excerpts (newline escaping should not leak into examples)
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    HumanEntry,
    TextContent,
    ToolUseContent,
    TranscriptStats,
    UserMessageModel,
)
from cc_explorer.responses import SearchProjectResponse
from cc_explorer.search import (
    SessionInfo,
    triage_multi,
)
from cc_explorer.utils import PrefixId


TS_A = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
TS_B = datetime(2026, 3, 22, 14, 0, 0, tzinfo=timezone.utc)
SESSION_A_ID = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B_ID = "bbbbbbbb-1111-2222-3333-444444444444"


def _human(text: str, uuid: str = "11111111-aaaa-bbbb-cccc-dddddddddddd", session_id: str = SESSION_A_ID) -> HumanEntry:
    return HumanEntry(
        uuid=uuid,
        timestamp=TS_A,
        sessionId=session_id,
        type="user",
        message=UserMessageModel(role="user", content=[TextContent(type="text", text=text)]),
    )


def _session(session_id: str, path: str, ts: datetime) -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        path=Path(path),
        title="test",
        first_timestamp=ts,
        message_count=10,
        stats=TranscriptStats(),
    )


ENTRIES_A = [_human("the comment_count field is broken")]
ENTRIES_B = [_human("comment_count shows zero for all posts", uuid="22222222-aaaa-bbbb-cccc-dddddddddddd", session_id=SESSION_B_ID)]


def _patch_entries(mapping):
    return patch(
        "cc_explorer.search.load_transcript",
        side_effect=lambda path: mapping.get(str(path), []),
    )


class TestExampleFormat:
    """Examples should be pipe-delimited: session_id|date|excerpt."""

    @pytest.fixture
    def two_sessions(self):
        return [
            _session(SESSION_A_ID, "a.jsonl", TS_A),
            _session(SESSION_B_ID, "b.jsonl", TS_B),
        ]

    def _get_pattern_match(self, two_sessions, pattern="comment_count"):
        with _patch_entries({"a.jsonl": ENTRIES_A, "b.jsonl": ENTRIES_B}):
            results = triage_multi(two_sessions, [pattern])
            response = SearchProjectResponse.from_triage(results)
        return response.matches[0]

    def test_example_has_three_pipe_fields(self, two_sessions):
        """Each example should have session_id|date|excerpt."""
        match = self._get_pattern_match(two_sessions)
        assert match.examples
        for example in match.examples:
            parts = example.split("|", 2)
            assert len(parts) == 3, f"Expected 3 pipe-separated fields, got {len(parts)}: {example!r}"

    def test_example_second_field_is_date(self, two_sessions):
        """The second pipe field should be a YYYY-MM-DD date."""
        match = self._get_pattern_match(two_sessions)
        for example in match.examples:
            _, date_str, _ = example.split("|", 2)
            # Should parse as a date
            parsed = datetime.strptime(date_str, "%Y-%m-%d")
            assert parsed is not None

    def test_example_first_field_is_session_id(self, two_sessions):
        """The first pipe field should be an 8-char session ID prefix."""
        match = self._get_pattern_match(two_sessions)
        for example in match.examples:
            session_id, _, _ = example.split("|", 2)
            assert len(session_id) == 8

    def test_example_third_field_contains_match(self, two_sessions):
        """The excerpt field should contain the matched term."""
        match = self._get_pattern_match(two_sessions)
        for example in match.examples:
            _, _, excerpt = example.split("|", 2)
            assert "comment_count" in excerpt


class TestSessionsField:
    """sessions should be an integer count, not a list of strings."""

    @pytest.fixture
    def two_sessions(self):
        return [
            _session(SESSION_A_ID, "a.jsonl", TS_A),
            _session(SESSION_B_ID, "b.jsonl", TS_B),
        ]

    def test_sessions_is_int(self, two_sessions):
        """sessions field should be an integer count."""
        with _patch_entries({"a.jsonl": ENTRIES_A, "b.jsonl": ENTRIES_B}):
            results = triage_multi(two_sessions, ["comment_count"])
            response = SearchProjectResponse.from_triage(results)
        match = response.matches[0]
        assert isinstance(match.sessions, int)
        assert match.sessions == 2


class TestNoEscapedNewlines:
    """Excerpts should not contain literal backslash-n sequences."""

    def test_multiline_text_no_backslash_n_in_excerpt(self):
        """Text with real newlines should have them collapsed to spaces, not escaped."""
        entries = [_human("line one\nline two\ncomment_count is here\nline four")]
        session = _session(SESSION_A_ID, "a.jsonl", TS_A)
        with _patch_entries({"a.jsonl": entries}):
            results = triage_multi([session], ["comment_count"])
            response = SearchProjectResponse.from_triage(results)
        match = response.matches[0]
        assert match.examples
        for example in match.examples:
            _, _, excerpt = example.split("|", 2)
            assert "\\n" not in excerpt, f"Literal backslash-n found in excerpt: {excerpt!r}"
