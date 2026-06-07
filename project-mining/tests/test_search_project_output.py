"""Tests for SearchProjectsResponse output format.

Covers the structured example shape (project/session/date/agent/excerpt):
1. Each example names its project and session and carries a date.
2. sessions / projects fields are integer counts, not lists of strings.
3. No literal '\\n' in excerpts (newline escaping should not leak into examples).
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
from cc_explorer.responses import SearchProjectsResponse
from cc_explorer.search import (
    SessionInfo,
    triage_multi,
)


TS_A = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
TS_B = datetime(2026, 3, 22, 14, 0, 0, tzinfo=timezone.utc)
SESSION_A_ID = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B_ID = "bbbbbbbb-1111-2222-3333-444444444444"
PROJECT_A = "/home/me/projects/alpha"
PROJECT_B = "/home/me/projects/beta"


def _human(text: str, uuid: str = "11111111-aaaa-bbbb-cccc-dddddddddddd", session_id: str = SESSION_A_ID) -> HumanEntry:
    return HumanEntry(
        uuid=uuid,
        timestamp=TS_A,
        sessionId=session_id,
        type="user",
        message=UserMessageModel(role="user", content=[TextContent(type="text", text=text)]),
    )


def _session(session_id: str, path: str, ts: datetime, project: str = PROJECT_A) -> SessionInfo:
    return SessionInfo(
        session_id=session_id,
        path=Path(path),
        title="test",
        first_timestamp=ts,
        message_count=10,
        stats=TranscriptStats(),
        project_path=project,
    )


ENTRIES_A = [_human("the comment_count field is broken")]
ENTRIES_B = [_human("comment_count shows zero for all posts", uuid="22222222-aaaa-bbbb-cccc-dddddddddddd", session_id=SESSION_B_ID)]


def _patch_entries(mapping):
    return patch(
        "cc_explorer.search.load_transcript",
        side_effect=lambda path: mapping.get(str(path), []),
    )


class TestExampleFormat:
    """Examples should be structured SearchHitExample objects."""

    @pytest.fixture
    def two_sessions(self):
        return [
            _session(SESSION_A_ID, "a.jsonl", TS_A, project=PROJECT_A),
            _session(SESSION_B_ID, "b.jsonl", TS_B, project=PROJECT_B),
        ]

    def _get_pattern_match(self, two_sessions, pattern="comment_count"):
        with _patch_entries({"a.jsonl": ENTRIES_A, "b.jsonl": ENTRIES_B}):
            results = triage_multi(two_sessions, [pattern])
            response = SearchProjectsResponse.from_triage(results, projects_searched=2)
        return response.matches[0]

    def test_example_names_project(self, two_sessions):
        match = self._get_pattern_match(two_sessions)
        assert match.examples
        projects = {ex.project for ex in match.examples}
        assert projects == {PROJECT_A, PROJECT_B}

    def test_example_session_is_short_prefix(self, two_sessions):
        match = self._get_pattern_match(two_sessions)
        for ex in match.examples:
            assert len(str(ex.session)) == 8

    def test_example_has_date(self, two_sessions):
        match = self._get_pattern_match(two_sessions)
        for ex in match.examples:
            parsed = datetime.strptime(ex.date, "%Y-%m-%d")
            assert parsed is not None

    def test_example_excerpt_contains_match(self, two_sessions):
        match = self._get_pattern_match(two_sessions)
        for ex in match.examples:
            assert "comment_count" in ex.excerpt

    def test_example_agent_absent_for_main_transcript(self, two_sessions):
        # Hits in the main transcript have no agent provenance.
        match = self._get_pattern_match(two_sessions)
        for ex in match.examples:
            assert ex.agent is None


class TestCountFields:
    """sessions / projects / total_hits should be integer counts."""

    @pytest.fixture
    def two_sessions(self):
        return [
            _session(SESSION_A_ID, "a.jsonl", TS_A, project=PROJECT_A),
            _session(SESSION_B_ID, "b.jsonl", TS_B, project=PROJECT_B),
        ]

    def test_counts(self, two_sessions):
        with _patch_entries({"a.jsonl": ENTRIES_A, "b.jsonl": ENTRIES_B}):
            results = triage_multi(two_sessions, ["comment_count"])
            response = SearchProjectsResponse.from_triage(results, projects_searched=2)
        match = response.matches[0]
        assert isinstance(match.sessions, int) and match.sessions == 2
        assert isinstance(match.projects, int) and match.projects == 2
        assert response.total_hits == 2
        assert response.projects_searched == 2


class TestNoEscapedNewlines:
    """Excerpts should not contain literal backslash-n sequences."""

    def test_multiline_text_no_backslash_n_in_excerpt(self):
        entries = [_human("line one\nline two\ncomment_count is here\nline four")]
        session = _session(SESSION_A_ID, "a.jsonl", TS_A)
        with _patch_entries({"a.jsonl": entries}):
            results = triage_multi([session], ["comment_count"])
            response = SearchProjectsResponse.from_triage(results, projects_searched=1)
        match = response.matches[0]
        assert match.examples
        for ex in match.examples:
            assert "\\n" not in ex.excerpt, f"Literal backslash-n in excerpt: {ex.excerpt!r}"
