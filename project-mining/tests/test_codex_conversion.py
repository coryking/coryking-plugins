"""Codex sessions convert into resumable Claude Code subagents.

Synthesized rollouts only (public repo). The vendored codex-resume layer renders
a rollout into text turns; these tests pin the Claude-side shape conversion.py
writes around them, the tool-level routing by source harness, and the live
session identity the default parent comes from.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import cc_explorer.live_session as live_session
import cc_explorer.mcp_server as srv
from cc_explorer.providers.codex import CodexProvider
from tests.test_codex_provider import _line, _meta, _write_rollout
from tests.test_conversion import (  # noqa: F401  (fake_claude is a fixture)
    PROJECT,
    PROVENANCE_TYPE,
    SID_PARENT,
    _convo_only,
    _read_jsonl,
    _simple_session_lines,
    fake_claude,
)

THREAD = "01a04576-fee2-7ee0-a898-58bb50387cc5"


def _rollout_items() -> list[dict]:
    return [
        _meta(THREAD, PROJECT),
        _line("2026-08-27T12:00:00Z", 1, "turn_context", {"cwd": PROJECT, "model": "gpt-test-codex"}),
        _line("2026-08-27T12:00:01Z", 2, "response_item", {
            "type": "message", "role": "user",
            "content": [
                {"type": "input_text", "text": "<environment_context>shell: zsh</environment_context>"},
                {"type": "input_text", "text": "find the blue widget"},
            ],
        }),
        _line("2026-08-27T12:00:02Z", 3, "response_item", {
            "type": "reasoning", "summary": [], "encrypted_content": "opaque",
        }),
        _line("2026-08-27T12:00:03Z", 4, "response_item", {
            "type": "function_call", "call_id": "call_1", "name": "exec_command",
            "arguments": "{\"cmd\":\"rg blue\"}",
        }),
        _line("2026-08-27T12:00:04Z", 5, "response_item", {
            "type": "function_call_output", "call_id": "call_1", "output": "widget.py: blue",
        }),
        _line("2026-08-27T12:00:05Z", 6, "response_item", {
            "type": "message", "role": "assistant",
            "content": [{"type": "output_text", "text": "It is in widget.py."}],
        }),
    ]


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / ".codex"
    monkeypatch.setenv("CODEX_HOME", str(home))
    _write_rollout(home / "sessions/2026/08/27" / f"rollout-2026-08-27T12-00-00-{THREAD}.jsonl", *_rollout_items())
    return home


def test_codex_session_converts_to_resumable_subagent(fake_claude, codex_home):
    parent_lines = _simple_session_lines()
    parent_lines[0]["version"] = "9.9.9"
    fake_claude.write_session(SID_PARENT, parent_lines)

    resp = srv.convert_session(
        direction="session_to_subagent", src_id=THREAD, dest_parent_session=SID_PARENT
    )

    assert resp.source_harness == "codex"
    assert resp.invocation == f'SendMessage(to: "{resp.created_id}")'
    assert resp.models.last == "gpt-test-codex"
    assert resp.environment.harness_version == "0.150.1"
    assert resp.environment.original_cwd == PROJECT
    assert resp.source_context_tokens > 0
    assert "OpenAI Codex session" in resp.suggested_handoff

    agent_file = fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents" / f"agent-{resp.created_id}.jsonl"
    lines = _read_jsonl(agent_file)
    assert lines[0]["type"] == PROVENANCE_TYPE
    assert lines[0]["x_converter"]["from"]["id"] == THREAD

    body = _convo_only(lines)
    assert [l["type"] for l in body] == ["user", "assistant"]
    assert all(l["isSidechain"] and l["agentId"] == resp.created_id for l in body)
    assert all(l["sessionId"] == SID_PARENT and l["version"] == "9.9.9" for l in body)
    assert body[0]["parentUuid"] is None and body[1]["parentUuid"] == body[0]["uuid"]
    assert body[0]["promptId"]
    assert body[1]["message"]["model"] == "<synthetic>"

    user_text = body[0]["message"]["content"]
    assert "took place in OpenAI Codex" in user_text
    assert "find the blue widget" in user_text
    assert "environment_context" not in user_text

    reply = body[1]["message"]["content"][0]["text"]
    assert "[Codex tool: exec_command]\nrg blue\n→ widget.py: blue" in reply
    assert "It is in widget.py." in reply
    assert "opaque" not in json.dumps(lines)


def test_codex_session_without_messages_is_refused(fake_claude, codex_home, tmp_path):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    empty = "01a04576-0000-7ee0-a898-58bb50387cc5"
    _write_rollout(codex_home / "sessions/2026/08/28" / f"rollout-x-{empty}.jsonl", _meta(empty, PROJECT))

    with pytest.raises(Exception, match="no messages"):
        srv.convert_session(direction="session_to_subagent", src_id=empty, dest_parent_session=SID_PARENT)


def test_search_drops_codex_injected_context(tmp_path):
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-x-{THREAD}.jsonl"
    _write_rollout(rollout, *_rollout_items())

    entries = CodexProvider(tmp_path).load_transcript([rollout])

    user = next(e for e in entries if e.type == "user")
    assert user.message.content[0].text == "find the blue widget"


# --- live session identity -----------------------------------------------------


@pytest.fixture
def registry(tmp_path, monkeypatch):
    """An empty registry dir and a clean owner cache for the ancestor walk."""
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(live_session, "_owner_cache", None)
    monkeypatch.setattr(live_session, "_process_name", lambda pid: "python")
    return sessions


def _register(sessions: Path, pid: int, session_id: str, *, recorded_pid: int | None = None) -> None:
    (sessions / f"{pid}.json").write_text(
        json.dumps({"pid": recorded_pid if recorded_pid is not None else pid, "sessionId": session_id})
    )


def test_live_owner_reads_parent_registry_entry(registry):
    _register(registry, os.getppid(), "live-id")
    assert live_session.live_owner() == ("claude", "live-id")


def test_live_owner_walks_up_to_an_ancestor(registry):
    grandparent = live_session._parent_pid(os.getppid())
    assert grandparent
    _register(registry, grandparent, "ancestor-id")
    assert live_session.live_owner() == ("claude", "ancestor-id")


def test_live_owner_ignores_entry_for_a_different_pid(registry):
    _register(registry, os.getppid(), "reused-pid-id", recorded_pid=os.getppid() + 1)
    assert live_session.live_owner() is None


def test_live_owner_nearest_codex_beats_outer_claude(registry, monkeypatch):
    grandparent = live_session._parent_pid(os.getppid())
    _register(registry, grandparent, "outer-claude-id")
    monkeypatch.setattr(
        live_session, "_process_name", lambda pid: "codex" if pid == os.getppid() else "python"
    )
    assert live_session.live_owner() == ("codex", None)


def test_live_owner_miss_is_not_cached(registry):
    assert live_session.live_owner() is None
    _register(registry, os.getppid(), "late-id")
    assert live_session.live_owner() == ("claude", "late-id")


def test_live_registry_id_beats_stale_spawn_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "stale-spawn-id")
    monkeypatch.setattr(srv, "live_owner", lambda: ("claude", "live-id"))
    assert srv._current_claude_session_id() == "live-id"
    assert srv._current_session_id() == "live-id"


def test_codex_owner_ignores_inherited_claude_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "outer-claude-id")
    monkeypatch.setenv("CODEX_THREAD_ID", THREAD)
    monkeypatch.setattr(srv, "live_owner", lambda: ("codex", None))
    assert srv._current_claude_session_id() is None
    assert srv._current_session_id() == THREAD


# --- size budget and Codex-specific result fields ------------------------------


def test_codex_conversion_reports_turns_dropped_for_size(fake_claude, codex_home, monkeypatch):
    import cc_explorer._codex_resume as vendored

    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    many = "01a04576-1111-7ee0-a898-58bb50387cc5"
    items = [_meta(many, PROJECT)]
    for i in range(6):
        role = "user" if i % 2 == 0 else "assistant"
        kind = "input_text" if role == "user" else "output_text"
        items.append(_line(f"2026-08-27T12:00:0{i}Z", i + 1, "response_item", {
            "type": "message", "role": role, "content": [{"type": kind, "text": f"turn-{i} " + "x" * 100}],
        }))
    _write_rollout(codex_home / "sessions/2026/08/29" / f"rollout-x-{many}.jsonl", *items)
    # Budget fits the last two turns only; build_turns reads its default at call time.
    monkeypatch.setattr(vendored.build_turns, "__defaults__", (None, 250))

    resp = srv.convert_session(direction="session_to_subagent", src_id=many, dest_parent_session=SID_PARENT)

    assert resp.source_turns_dropped == 4
    assert resp.dropped_branches is None
    assert resp.turns == 2


def test_codex_subagent_rollout_keeps_its_own_session_meta(tmp_path):
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-x-{THREAD}.jsonl"
    _write_rollout(
        rollout,
        _meta(THREAD, PROJECT, subagent_history_start_ordinal=5),
        _line("2026-08-27T12:00:01Z", 1, "response_item", {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "inherited"}],
        }),
        _line("2026-08-27T12:00:06Z", 6, "response_item", {
            "type": "message", "role": "user", "content": [{"type": "input_text", "text": "child"}],
        }),
    )

    records = CodexProvider(tmp_path).session_records([rollout])

    assert [r["type"] for r in records] == ["session_meta", "response_item"]
    assert records[1]["payload"]["content"][0]["text"] == "child"
