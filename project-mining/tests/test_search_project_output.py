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
        "cc_explorer.providers.claude.load_transcript",
        side_effect=lambda path: mapping.get(str(path), []),
    )


@pytest.fixture
def two_project_response():
    sessions = [
        _session(SESSION_A_ID, "a.jsonl", TS_A, project=PROJECT_A),
        _session(SESSION_B_ID, "b.jsonl", TS_B, project=PROJECT_B),
    ]
    with _patch_entries({"a.jsonl": ENTRIES_A, "b.jsonl": ENTRIES_B}):
        results = triage_multi(sessions, ["comment_count"])
        return SearchProjectsResponse.from_triage(results, projects_searched=2)


def test_examples_preserve_session_provenance(two_project_response):
    examples = two_project_response.matches[0].examples
    # Assert the association as well as field presence; swapped metadata is wrong.
    assert {(ex.project, str(ex.session), ex.date, ex.agent) for ex in examples} == {
        (PROJECT_A, "aaaaaaaa", "2026-03-15", None),
        (PROJECT_B, "bbbbbbbb", "2026-03-22", None),
    }
    assert all("comment_count" in ex.excerpt for ex in examples)


def test_counts(two_project_response):
    match = two_project_response.matches[0]
    assert isinstance(match.sessions, int) and match.sessions == 2
    assert isinstance(match.projects, int) and match.projects == 2
    assert two_project_response.total_hits == 2
    assert two_project_response.projects_searched == 2


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
