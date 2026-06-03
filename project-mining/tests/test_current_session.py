"""Tests for current-session detection, exclusion, and marking.

Two behaviors, deliberately different per tool family:
- search_project EXCLUDES the calling session (it would match itself) and
  surfaces excluded_current_session.
- list_* tools KEEP the calling session but flag it is_current=True, because
  dropping a row from an inventory is misleading.

Identity comes from CLAUDE_CODE_SESSION_ID, which Claude Code injects into the
per-session MCP server process.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from cc_explorer.mcp_server import _current_session_id, _exclude_current_session
from cc_explorer.responses import SearchProjectResponse, SessionListResponse
from cc_explorer.search import SessionInfo, TriageResult
from cc_explorer.utils import PrefixId


TS = datetime(2026, 6, 3, 9, 0, 0, tzinfo=timezone.utc)
CALLER_ID = "afcc2acb-948c-462f-80eb-56b1b5d7009f"
OTHER_ID = "bbbbbbbb-1111-2222-3333-444444444444"


def _session(session_id: str) -> SessionInfo:
    return SessionInfo(
        session_id=PrefixId(session_id),
        path=Path(f"/tmp/{session_id}.jsonl"),
        title="test",
        first_timestamp=TS,
        message_count=10,
    )


# --- _current_session_id -----------------------------------------------------


def test_current_session_id_reads_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CALLER_ID)
    assert _current_session_id() == CALLER_ID


def test_current_session_id_absent_is_none(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    assert _current_session_id() is None


def test_current_session_id_empty_is_none(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "")
    assert _current_session_id() is None


# --- _exclude_current_session (search_project path) --------------------------


def test_exclude_drops_caller_and_reports_it(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CALLER_ID)
    sessions = [_session(CALLER_ID), _session(OTHER_ID)]
    kept, excluded = _exclude_current_session(sessions, include_current=False)
    assert [s.session_id for s in kept] == [PrefixId(OTHER_ID)]
    assert excluded == PrefixId(CALLER_ID)


def test_exclude_opt_out_keeps_everything(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CALLER_ID)
    sessions = [_session(CALLER_ID), _session(OTHER_ID)]
    kept, excluded = _exclude_current_session(sessions, include_current=True)
    assert len(kept) == 2
    assert excluded is None


def test_exclude_noop_when_caller_not_in_list(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CALLER_ID)
    sessions = [_session(OTHER_ID)]
    kept, excluded = _exclude_current_session(sessions, include_current=False)
    assert len(kept) == 1
    assert excluded is None


def test_exclude_noop_when_env_absent(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    sessions = [_session(CALLER_ID), _session(OTHER_ID)]
    kept, excluded = _exclude_current_session(sessions, include_current=False)
    assert len(kept) == 2
    assert excluded is None


# --- list tools mark rather than drop ----------------------------------------


def test_session_list_marks_only_current_row():
    sessions = [_session(CALLER_ID), _session(OTHER_ID)]
    resp = SessionListResponse.from_sessions(sessions, current_session=CALLER_ID)
    assert resp.total == 2  # nothing dropped
    by_id = {s.session: s for s in resp.sessions}
    assert by_id[PrefixId(CALLER_ID)].is_current is True
    assert by_id[PrefixId(OTHER_ID)].is_current is None


def test_session_list_marks_nothing_without_current():
    sessions = [_session(CALLER_ID), _session(OTHER_ID)]
    resp = SessionListResponse.from_sessions(sessions, current_session=None)
    assert all(s.is_current is None for s in resp.sessions)


def test_is_current_omitted_from_serialization_when_false():
    # SparseModel drops None — the marker only costs tokens on the one row.
    sessions = [_session(OTHER_ID)]
    resp = SessionListResponse.from_sessions(sessions, current_session=CALLER_ID)
    dumped = resp.model_dump()
    assert "is_current" not in dumped["sessions"][0]


# --- search response surfaces the exclusion ----------------------------------


def test_search_response_surfaces_excluded():
    other = _session(OTHER_ID)
    triage = [("foo", [TriageResult(session=other, count=3, first_match_example="foo bar")])]
    resp = SearchProjectResponse.from_triage(
        triage, excluded_current_session=PrefixId(CALLER_ID)
    )
    assert resp.excluded_current_session == PrefixId(CALLER_ID)
    # serializes to the short form
    assert resp.model_dump()["excluded_current_session"] == "afcc2acb"


def test_search_response_omits_marker_when_nothing_excluded():
    other = _session(OTHER_ID)
    triage = [("foo", [TriageResult(session=other, count=1, first_match_example="foo")])]
    resp = SearchProjectResponse.from_triage(triage)
    assert "excluded_current_session" not in resp.model_dump()


# --- search_project: calling session is the only one in scope ----------------


def test_search_project_only_self_points_at_exclusion(monkeypatch):
    """When the calling session is the only session in scope, the error names
    the exclusion (and the override) rather than blaming the patterns. The
    exclusion branch fires before any transcript is loaded."""
    import cc_explorer.mcp_server as srv
    from fastmcp.exceptions import ToolError

    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", CALLER_ID)
    monkeypatch.setattr(srv, "resolve_project", lambda project=None: "/tmp/fake")
    monkeypatch.setattr(srv, "load_sessions", lambda proj: [_session(CALLER_ID)])

    with pytest.raises(ToolError) as exc:
        srv.search_project(patterns=["anything"])
    msg = str(exc.value)
    assert "include_current_session" in msg
    assert CALLER_ID[:8] in msg  # names the excluded session by short id
