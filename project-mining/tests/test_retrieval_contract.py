"""Discovery misses, role scope and output controls through the MCP boundary."""

import asyncio
import json

import pytest
from fastmcp import Client

import cc_explorer.mcp_server as srv
from cc_explorer.corpus import Corpus, SessionRef
from cc_explorer.responses import AgentDetailResponse
from cc_explorer.search import SessionInfo
from cc_explorer.subagents import SubagentInfo
from cc_explorer.utils import PrefixId
from tests.test_line_search import SID, _claude_line, _claude_session


@pytest.fixture
def source(tmp_path, monkeypatch):
    ref = _claude_session(tmp_path)
    monkeypatch.setattr(Corpus, "discover", classmethod(lambda cls, projects=None, harnesses=None: Corpus([ref])))
    monkeypatch.setattr(srv, "resolve_projects", lambda projects=None: [str(tmp_path)])
    monkeypatch.setattr(srv, "_current_session_id", lambda: None)
    monkeypatch.setattr(srv, "_run_reaper", lambda phase: None)
    return ref


def _call(name, args):
    async def run():
        async with Client(srv.mcp) as client:
            return await client.call_tool(name, args, raise_on_error=False)
    return asyncio.run(run())


def test_default_role_finds_agent_actions_and_user_intent(source):
    default = _call("search_projects", {"patterns": ["needle"]})
    users = _call("search_projects", {"patterns": ["needle"], "role": "user"})
    agents = _call("search_projects", {"patterns": ["needle"], "role": "assistant"})
    assert not default.is_error
    assert default.structured_content["total_hits"] == 3
    assert users.structured_content["total_hits"] == 2
    assert agents.structured_content["total_hits"] == 1


@pytest.mark.parametrize("name,args", [
    ("search_projects", {"patterns": ["absent-phrase"]}),
    ("grep_session", {"session": SID, "patterns": ["absent-phrase"]}),
    ("grep_sessions", {"sessions": [SID, "deadbeef"], "patterns": ["absent-phrase"]}),
    ("list_project_sessions", {"min_agents": 100}),
])
def test_empty_discovery_is_successful_and_retains_diagnostics(source, name, args):
    result = _call(name, args)
    assert not result.is_error
    data = result.structured_content
    if name == "search_projects":
        assert data["total_hits"] == 0
    elif name == "grep_session":
        assert data["patterns"][0]["hits"] == 0
    else:
        assert data["sessions"] == []
    if name == "grep_sessions":
        assert data["not_found"] == ["deadbeef"]


def test_invalid_session_remains_an_error(source):
    result = _call("grep_sessions", {"sessions": ["deadbeef"], "patterns": ["needle"]})
    assert result.is_error
    assert "No sessions matched" in result.content[0].text


def test_zero_context_does_not_add_neighbours(source):
    result = _call("grep_session", {"session": SID, "patterns": ["needle"], "context": 0})
    blocks = result.structured_content["patterns"][0]["matches"]
    assert len(blocks) == 3
    assert all(not b["before"] and not b["after"] for b in blocks)


@pytest.mark.parametrize("context,expected", [(0, 1), (1, 3)])
def test_read_turn_uses_the_same_visible_context_boundary(source, context, expected):
    result = _call("read_turn", {
        "session": SID, "turn": "00000000-0000-0000-0000-000000000002", "context": context,
    })
    assert not result.is_error
    assert len(result.structured_content["chats"]) == expected


@pytest.mark.parametrize("tool", ["grep_session", "grep_sessions"])
def test_tools_apply_one_budget_without_losing_total_hits(tmp_path, monkeypatch, tool):
    ids = [SID, "22222222-2222-2222-2222-222222222222"]
    refs = []
    for sid in ids:
        path = tmp_path / f"{sid}.jsonl"
        record = _claude_line("assistant", "needle target " * 1000, sid, "2026-03-15T10:30:00Z")
        record["sessionId"] = sid
        path.write_text(json.dumps(record) + "\n")
        refs.append(SessionRef(session_id=PrefixId(sid), path=path, project_path=str(tmp_path)))
    monkeypatch.setattr(Corpus, "discover", classmethod(lambda cls, projects=None, harnesses=None: Corpus(refs)))
    monkeypatch.setattr(srv, "_run_reaper", lambda phase: None)
    args = {"patterns": ["needle", "target"], "truncate": 0, "max_chars": 1000}
    args.update({"session": ids[0]} if tool == "grep_session" else {"sessions": ids})

    result = _call(tool, args)

    assert not result.is_error
    data = result.structured_content
    assert data["output_truncated"] is True
    sessions = [data] if tool == "grep_session" else data["sessions"]
    assert len(sessions) == (1 if tool == "grep_session" else 2)
    chars = 0
    for session in sessions:
        for pattern in session["patterns"]:
            assert pattern["hits"] == 1
            assert pattern["showing"] == len(pattern["matches"])
            assert pattern["overflow"]
            for block in pattern["matches"]:
                chars += sum(map(len, block["before"] + [block["match"]] + block["after"]))
    assert 0 < chars <= 1000


@pytest.mark.parametrize("width", [1, 2, 80])
def test_agent_prompt_and_result_honour_excerpt_control(source, width):
    found = SubagentInfo(agent_id=PrefixId("a1234567"), tool_use_id="t", prompt="p" * 500, result_text="r" * 500)
    session = SessionInfo.load(source)
    clipped = AgentDetailResponse.from_subagent(found, session, truncate=width)
    full = AgentDetailResponse.from_subagent(found, session, truncate=0)
    assert len(clipped.prompt) <= width and len(clipped.result) <= width
    if width >= 3:
        assert clipped.prompt.endswith("...") and clipped.result.endswith("...")
    assert full.prompt == found.prompt and full.result == found.result_text
