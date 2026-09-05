"""Identity and sparse output at the real MCP serialization boundary."""

import asyncio
import json
from datetime import datetime, timezone

import pytest
from fastmcp.exceptions import ToolError

from cc_explorer.corpus import Corpus, SessionRef
from cc_explorer.mcp_server import mcp
from cc_explorer.models import HumanEntry


SESSION_A = "01992000-1111-7111-8111-111111111111"
SESSION_B = "01992000-2222-7222-8222-222222222222"
TURN_A = "abcdef12-1111-4111-8111-111111111111"
TURN_B = "abcdef12-2222-4222-8222-222222222222"


@pytest.fixture
def identity_corpus(tmp_path, monkeypatch):
    refs = []
    for sid, turns in [(SESSION_A, [TURN_A, TURN_B]), (SESSION_B, [TURN_A])]:
        path = tmp_path / f"{sid}.jsonl"
        rows = [
            dict(
                type="user",
                uuid=turn,
                sessionId=sid,
                timestamp="2026-08-20T10:00:00Z",
                message={"role": "user", "content": f"target turn {index}"},
            )
            for index, turn in enumerate(turns)
        ]
        path.write_text("\n".join(json.dumps(row) for row in rows))
        refs.append(
            SessionRef(
                session_id=HumanEntry.model_validate(rows[0]).sessionId,
                path=path,
                project_path=str(tmp_path),
            )
        )
    monkeypatch.setattr(
        Corpus,
        "discover",
        classmethod(lambda cls, projects=None, harnesses=None: Corpus(refs)),
    )
    return refs


def call(name, **args):
    result = asyncio.run(mcp.call_tool(name, args))
    # FastMCP wraps union output schemas in a result envelope.
    # Both channels must carry the same sparse, full-identity response.
    payload = result.structured_content
    if name == "get_agent_detail":
        payload = payload["result"]
    assert json.loads(result.content[0].text) == payload
    return payload


def test_stored_identity_is_exact():
    e = HumanEntry(
        type="user",
        uuid=TURN_A,
        sessionId=SESSION_A,
        timestamp=datetime.now(timezone.utc),
        message={"role": "user", "content": "hello"},
    )
    assert e.uuid != TURN_A[:8]
    assert e.uuid != ""
    assert hash(e.uuid) == hash(TURN_A)
    assert str(e.uuid) == TURN_A
    assert e.model_dump()["uuid"] == TURN_A


def test_listing_preserves_colliding_sessions(identity_corpus):
    payload = call("list_project_sessions", min_messages=1)
    assert {row["session"] for row in payload["sessions"]} == {SESSION_A, SESSION_B}


def test_mcp_omits_nulls_recursively(identity_corpus):
    payload = call("list_project_sessions", min_messages=1)

    def check(value):
        if isinstance(value, dict):
            assert all(v is not None for v in value.values())
            for v in value.values():
                check(v)
        elif isinstance(value, list):
            for v in value:
                check(v)

    check(payload)
    assert payload["sessions"][0]["agents"] == 0


def test_legacy_session_prefix_lists_complete_candidates(identity_corpus):
    with pytest.raises(ToolError) as exc:
        call("browse_session", session=SESSION_A[:8])
    assert SESSION_A in str(exc.value) and SESSION_B in str(exc.value)


def test_read_returns_resolved_turn_and_zero_context(identity_corpus):
    payload = call("read_turn", session=SESSION_A[:13], turn=TURN_A[:13], context=0)
    assert payload["session"] == SESSION_A
    assert payload["turn"] == TURN_A
    assert len(payload["chats"]) == 1
    assert payload["chats"][0].split("|", 1)[0] == TURN_A


@pytest.mark.parametrize("tool", ["read_turn", "browse_session"])
def test_ambiguous_legacy_turn_never_selects_first(identity_corpus, tool):
    with pytest.raises(ToolError) as exc:
        call(tool, session=SESSION_A, turn=TURN_A[:8])
    assert TURN_A in str(exc.value) and TURN_B in str(exc.value)


def test_browse_returns_resolved_anchor(identity_corpus):
    payload = call("browse_session", session=SESSION_A[:13], turn=TURN_B[:13])
    assert payload["anchor"] == TURN_B


def test_grep_zero_context_emits_no_neighbors(identity_corpus):
    payload = call("grep_session", session=SESSION_A, patterns=["turn 0"], context=0)
    match = payload["patterns"][0]["matches"][0]
    assert match["before"] == [] and match["after"] == []


def test_unscoped_duplicate_turn_reports_session_candidates(identity_corpus):
    with pytest.raises(ToolError) as exc:
        call("read_turn", turn=TURN_A)
    assert SESSION_A in str(exc.value) and SESSION_B in str(exc.value)


@pytest.mark.parametrize("length", range(8, 37))
def test_every_canonical_turn_prefix_length_is_valid(length):
    from cc_explorer.mcp_server import _validate_turn_id

    _validate_turn_id(TURN_A[:length])


def test_agent_prefix_ambiguity_and_exact_aliases(identity_corpus, monkeypatch):
    import cc_explorer.mcp_server as server
    from cc_explorer.search import SessionInfo
    from cc_explorer.subagents import SubagentInfo

    agent_a = SubagentInfo(agent_id="agent123-first", tool_use_id="toolu_01-first")
    agent_b = SubagentInfo(agent_id="agent123-second", tool_use_id="toolu_01-second")
    sessions = [SessionInfo.load(ref) for ref in identity_corpus]
    monkeypatch.setattr(
        server,
        "discover_subagents",
        lambda path: [agent_a] if path == identity_corpus[0].path else [agent_b],
    )
    for prefix in ("agent123", "toolu_01"):
        with pytest.raises(ToolError) as exc:
            server._find_agent(sessions, prefix)
        assert agent_a.agent_id in str(exc.value)
        assert agent_b.agent_id in str(exc.value)
        assert SESSION_A in str(exc.value) and SESSION_B in str(exc.value)
    assert server._find_agent(sessions, "toolu_01-first") == (agent_a, sessions[0])
    assert server._find_agent(sessions, "") == (None, None)
    agent_b.agent_id = "agent123"
    assert server._find_agent(sessions, "agent123") == (agent_b, sessions[1])


def test_sparse_serializer_preserves_meaningful_values():
    from pydantic import Field, TypeAdapter
    from cc_explorer.responses import SparseModel

    class Response(SparseModel):
        missing: str | None = None
        count: int = 0
        enabled: bool = False
        text: str = ""
        items: list[str] = Field(default_factory=list)
        raw: dict = Field(default_factory=lambda: {"value": None})

    response = Response()
    expected = {
        "count": 0,
        "enabled": False,
        "text": "",
        "items": [],
        "raw": {"value": None},
    }
    assert response.model_dump() == expected
    assert json.loads(TypeAdapter(Response).dump_json(response)) == expected


def test_dispatch_ids_roundtrip_without_session_scope(identity_corpus):
    for index, ref in enumerate(identity_corpus):
        row = dict(
            type="assistant",
            uuid=f"12345678-{index:04d}-4000-8000-111111111111",
            sessionId=ref.session_id,
            timestamp="2026-08-20T10:01:00Z",
            message={
                "role": "assistant",
                "id": "synthetic-message",
                "type": "message",
                "model": "synthetic",
                "content": [
                    dict(
                        type="tool_use",
                        id=f"toolu_01-synthetic-{index}",
                        name="Agent",
                        input={"description": "synthetic dispatch", "prompt": "hello"},
                    )
                ],
            },
        )
        with ref.path.open("a") as f:
            f.write("\n" + json.dumps(row))
    listing = call("list_session_agents", session=SESSION_A)
    agent = listing["agents"][0]
    assert agent["tool_use_id"] == "toolu_01-synthetic-0"
    detail = call("get_agent_detail", agent_ids=[agent["tool_use_id"]])
    assert detail["session"] == SESSION_A
    with pytest.raises(ToolError) as exc:
        call("get_agent_detail", agent_ids=["toolu_01"])
    assert "toolu_01-synthetic-0" in str(exc.value)
    assert "toolu_01-synthetic-1" in str(exc.value)
