"""Tests for cross-project session resolution safety (code-review fixes).

When `projects` is omitted, session-keyed tools span the whole corpus. Three
guarantees this exercises:
- ambiguous short prefixes raise instead of silently resolving to one session
- pooling de-dupes by session_id (so an explicit list naming two worktrees of
  one repo doesn't double-count)
- the holding project is located cheaply (load_conversations, no transcript
  parse) instead of parsing every project.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

import cc_explorer.mcp_server as srv
from cc_explorer.mcp_server import (
    _load_all_sessions,
    _projects_for_sessions,
    _resolve_unique_session,
)
from cc_explorer.search import SessionInfo
from cc_explorer.utils import PrefixId

TS = datetime(2026, 6, 1, tzinfo=timezone.utc)
ID_A = "aaaaaaaa-1111-2222-3333-444444444444"
ID_A2 = "aaaaaaaa-9999-8888-7777-666666666666"  # shares 8-char prefix with ID_A
ID_B = "bbbbbbbb-1111-2222-3333-444444444444"


def _session(sid: str, project: str = "/repo") -> SessionInfo:
    return SessionInfo(
        session_id=PrefixId(sid),
        path=Path(f"/tmp/{sid}.jsonl"),
        title="t",
        first_timestamp=TS,
        message_count=1,
        project_path=project,
    )


# --- _resolve_unique_session -------------------------------------------------


def test_resolve_unique_returns_single_match():
    sessions = [_session(ID_A), _session(ID_B)]
    got = _resolve_unique_session(sessions, ID_A)
    assert got.session_id == PrefixId(ID_A)


def test_resolve_unique_raises_on_ambiguous_prefix():
    # Two distinct full ids sharing the same 8-char prefix.
    sessions = [_session(ID_A, "/repoA"), _session(ID_A2, "/repoB")]
    with pytest.raises(ToolError) as exc:
        _resolve_unique_session(sessions, "aaaaaaaa")
    msg = str(exc.value)
    assert "ambiguous" in msg.lower()
    assert "/repoA" in msg and "/repoB" in msg  # names where the collision lives


def test_resolve_unique_raises_on_no_match():
    with pytest.raises(ToolError):
        _resolve_unique_session([_session(ID_B)], ID_A)


# --- _load_all_sessions dedup ------------------------------------------------


def test_load_all_sessions_dedups_by_session_id(monkeypatch):
    # Two project paths (e.g. two worktrees of one repo) each yield the same
    # pooled session — must appear once, not twice.
    monkeypatch.setattr(srv, "resolve_projects", lambda projects=None: ["/repo", "/repo/wt"])
    monkeypatch.setattr(
        srv, "load_sessions", lambda p, with_agents_present=False: [_session(ID_A)]
    )
    sessions, proj_paths = _load_all_sessions(["/repo", "/repo/wt"])
    assert len(sessions) == 1
    assert proj_paths == ["/repo", "/repo/wt"]


# --- _projects_for_sessions (cheap locator) ----------------------------------


def test_projects_for_sessions_explicit_bypasses_locator(monkeypatch):
    monkeypatch.setattr(srv, "resolve_projects", lambda projects=None: list(projects))
    # load_conversations must NOT be consulted when projects is explicit.
    monkeypatch.setattr(
        srv, "load_conversations", lambda p: (_ for _ in ()).throw(AssertionError("parsed!"))
    )
    assert _projects_for_sessions([ID_A], ["/x", "/y"]) == ["/x", "/y"]


def test_projects_for_sessions_locates_holding_project(monkeypatch):
    monkeypatch.setattr(srv, "resolve_projects", lambda projects=None: ["/a", "/b"])
    convs = {
        "/a": {PrefixId(ID_A): object()},
        "/b": {PrefixId(ID_B): object()},
    }
    monkeypatch.setattr(srv, "load_conversations", lambda p: convs[p])
    # Query by 8-char prefix of ID_A → only /a holds it.
    assert _projects_for_sessions(["aaaaaaaa"], None) == ["/a"]
    # A prefix nobody has → empty (caller turns this into a "no session" error).
    assert _projects_for_sessions(["cccccccc"], None) == []
