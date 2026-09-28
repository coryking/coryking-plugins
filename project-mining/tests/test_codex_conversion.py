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


def test_registry_session_id_reads_owning_process_entry(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    (sessions / f"{os.getppid()}.json").write_text(json.dumps({"sessionId": "live-id"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    live_session._owning_registry_file.cache_clear()
    try:
        assert live_session.registry_session_id() == "live-id"
    finally:
        live_session._owning_registry_file.cache_clear()


def test_registry_session_id_walks_up_to_an_ancestor(tmp_path, monkeypatch):
    sessions = tmp_path / "sessions"
    sessions.mkdir()
    grandparent = live_session._parent_pid(os.getppid())
    assert grandparent
    (sessions / f"{grandparent}.json").write_text(json.dumps({"sessionId": "ancestor-id"}))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    live_session._owning_registry_file.cache_clear()
    try:
        assert live_session.registry_session_id() == "ancestor-id"
    finally:
        live_session._owning_registry_file.cache_clear()


def test_registry_session_id_none_without_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
    live_session._owning_registry_file.cache_clear()
    try:
        assert live_session.registry_session_id() is None
    finally:
        live_session._owning_registry_file.cache_clear()


def test_live_registry_id_beats_stale_spawn_env(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "stale-spawn-id")
    monkeypatch.setattr(srv, "registry_session_id", lambda: "live-id")
    assert srv._current_claude_session_id() == "live-id"
    assert srv._current_session_id() == "live-id"
