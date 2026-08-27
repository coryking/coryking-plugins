"""Codex is a first-class, direct-filesystem cc-explorer corpus provider."""

from __future__ import annotations

import json
import inspect
import os
from pathlib import Path

from cc_explorer.models import AssistantTranscriptEntry, HumanEntry, ToolResultEntry
from cc_explorer.corpus import Corpus, SessionRef
from cc_explorer.corpus import discover_projects
from cc_explorer.providers import Harness
from cc_explorer.providers.codex import CodexProvider
from cc_explorer.search import SessionInfo, get_turn_context, triage_multi
from cc_explorer.responses import SessionSummary
from cc_explorer.utils import PrefixId


def _write_rollout(path: Path, *items: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(item) for item in items) + "\n")


def _line(timestamp: str, ordinal: int, kind: str, payload: dict) -> dict:
    return {"timestamp": timestamp, "ordinal": ordinal, "type": kind, "payload": payload}


def _meta(thread_id: str, cwd: str, **extra: object) -> dict:
    payload = {
        "session_id": thread_id,
        "id": thread_id,
        "timestamp": "2026-08-27T12:00:00Z",
        "cwd": cwd,
        "originator": "codex-tui",
        "cli_version": "0.150.1",
        "source": "cli",
        "model_provider": "openai",
        **extra,
    }
    return _line("2026-08-27T12:00:00Z", 0, "session_meta", payload)


def test_discovers_codex_rollouts_by_metadata_cwd(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(rollout, _meta(thread, "/repo/example"))

    refs = CodexProvider(tmp_path).discover_sessions(["/repo/example"])

    assert len(refs) == 1
    assert refs[0].harness is Harness.codex
    assert refs[0].session_id.full == thread
    assert refs[0].project_path == "/repo/example"
    assert refs[0].transcript_files() == [rollout]


def test_parses_messages_reasoning_tools_and_outputs(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(
        rollout,
        _meta(thread, "/repo/example"),
        _line("2026-08-27T12:00:01Z", 1, "response_item", {
            "type": "message", "id": "msg_user", "role": "user",
            "content": [{"type": "input_text", "text": "find the blue widget"}],
        }),
        _line("2026-08-27T12:00:02Z", 2, "response_item", {
            "type": "reasoning", "id": "reason_1",
            "summary": [{"type": "summary_text", "text": "Inspect likely files"}],
            "encrypted_content": "opaque",
        }),
        _line("2026-08-27T12:00:03Z", 3, "response_item", {
            "type": "function_call", "id": "call_item", "call_id": "call_1",
            "name": "exec_command", "namespace": "functions",
            "arguments": "{\"cmd\":\"rg blue\"}",
        }),
        _line("2026-08-27T12:00:04Z", 4, "response_item", {
            "type": "function_call_output", "id": "out_item", "call_id": "call_1",
            "output": "project/widget.py: blue",
        }),
        _line("2026-08-27T12:00:05Z", 5, "response_item", {
            "type": "message", "id": "msg_assistant", "role": "assistant",
            "content": [{"type": "output_text", "text": "Found it."}],
        }),
    )

    entries = CodexProvider(tmp_path).load_transcript((rollout,))

    assert [type(e) for e in entries] == [
        HumanEntry,
        AssistantTranscriptEntry,
        AssistantTranscriptEntry,
        ToolResultEntry,
        AssistantTranscriptEntry,
    ]
    assert "find the blue widget" in entries[0].display(0)
    assert "Inspect likely files" in entries[1].display(0)
    assert "exec_command" in entries[2].display(0)
    assert "rg blue" in entries[2].display(0)
    assert "project/widget.py: blue" in entries[3].display(0)
    assert entries[4].display(0) == "Found it."
    assert len({e.uuid.full for e in entries}) == 5


def test_history_base_is_one_logical_session(tmp_path: Path) -> None:
    base_id = "01a04500-0000-7000-8000-000000000001"
    child_id = "01a04500-0000-7000-8000-000000000002"
    base = tmp_path / "sessions/2026/08/26" / f"rollout-date-{base_id}.jsonl"
    child = tmp_path / "sessions/2026/08/27" / f"rollout-date-{child_id}.jsonl"
    base_lines = [
        _meta(base_id, "/repo/example"),
        _line("2026-08-26T12:00:01Z", 1, "response_item", {
            "type": "message", "id": "base_user", "role": "user",
            "content": [{"type": "input_text", "text": "inherited prompt"}],
        }),
        _line("2026-08-26T12:00:02Z", 2, "response_item", {
            "type": "message", "id": "excluded_tail", "role": "assistant",
            "content": [{"type": "output_text", "text": "must be cut"}],
        }),
    ]
    _write_rollout(base, *base_lines)
    end_offset = len((json.dumps(base_lines[0]) + "\n" + json.dumps(base_lines[1]) + "\n").encode())
    _write_rollout(
        child,
        _meta(child_id, "/repo/example", history_mode="paginated", history_base={
            "thread_id": base_id, "end_ordinal_exclusive": 2, "end_byte_offset": end_offset,
        }),
        _line("2026-08-27T12:00:01Z", 1, "response_item", {
            "type": "message", "id": "child_assistant", "role": "assistant",
            "content": [{"type": "output_text", "text": "continued answer"}],
        }),
    )

    provider = CodexProvider(tmp_path)
    refs = provider.discover_sessions(["/repo/example"])
    child_ref = next(ref for ref in refs if ref.session_id.full == child_id)
    entries = provider.load_transcript(child_ref.paths)

    rendered = [entry.display(0) for entry in entries]
    assert rendered == ["inherited prompt", "continued answer"]
    assert child_ref.transcript_files() == [base, child]


def test_unknown_rollout_items_are_ignored(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(
        rollout,
        _meta(thread, "/repo/example"),
        _line("2026-08-27T12:00:01Z", 1, "future_record", {"new": "shape"}),
        _line("2026-08-27T12:00:02Z", 2, "response_item", {
            "type": "message", "id": "known", "role": "user",
            "content": [{"type": "input_text", "text": "still readable"}],
        }),
    )

    entries = CodexProvider(tmp_path).load_transcript((rollout,))

    assert [entry.display(0) for entry in entries] == ["still readable"]


def test_corpus_combines_harnesses_and_can_filter(monkeypatch, tmp_path: Path) -> None:
    from cc_explorer.parser import ConversationRef

    claude_path = tmp_path / "claude.jsonl"
    codex_path = tmp_path / "codex.jsonl"
    claude_path.touch()
    codex_path.touch()
    codex_ref = type("CodexRef", (), {
        "session_id": PrefixId("22222222-2222-4222-8222-222222222222"),
        "path": codex_path,
        "paths": (codex_path,),
        "project_path": "/repo/example",
        "worktree": None,
        "harness": Harness.codex,
    })()

    monkeypatch.setattr(
        "cc_explorer.providers.claude.load_conversations",
        lambda project: {
            PrefixId("11111111-1111-4111-8111-111111111111"):
                ConversationRef(path=claude_path, worktree=None)
        },
    )
    monkeypatch.setattr(
        "cc_explorer.corpus.CodexProvider.discover_sessions",
        lambda self, projects: [codex_ref],
    )

    combined = Corpus.discover(["/repo/example"])
    codex_only = Corpus.discover(["/repo/example"], harnesses=["codex"])

    assert [(ref.harness.value, ref.session_id.full) for ref in combined.refs] == [
        ("claude", "11111111-1111-4111-8111-111111111111"),
        ("codex", "22222222-2222-4222-8222-222222222222"),
    ]
    assert [ref.harness for ref in codex_only.refs] == [Harness.codex]


def test_codex_ref_runs_through_existing_search_and_read(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(
        rollout,
        _meta(thread, "/repo/example"),
        _line("2026-08-27T12:00:01Z", 1, "response_item", {
            "type": "message", "id": "msg_user", "role": "user",
            "content": [{"type": "input_text", "text": "needle in Codex"}],
        }),
        _line("2026-08-27T12:00:02Z", 2, "response_item", {
            "type": "message", "id": "msg_assistant", "role": "assistant",
            "content": [{"type": "output_text", "text": "answer from Codex"}],
        }),
    )
    ref = SessionRef(
        session_id=PrefixId(thread), path=rollout, paths=(rollout,),
        project_path="/repo/example", harness=Harness.codex,
    )

    session = SessionInfo.load(ref)

    assert session is not None
    assert session.harness is Harness.codex
    result = triage_multi([session], ["needle"])
    assert result[0][1][0].count == 1
    entries = CodexProvider(tmp_path).load_transcript((rollout,))
    found, context, _ = get_turn_context(
        [session], entries[0].uuid.full, context=1, session_id=thread
    )
    assert found is session
    assert [entry.display(0) for entry in context] == [
        "needle in Codex", "answer from Codex"
    ]


def test_project_discovery_includes_codex_only_projects(monkeypatch, tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(rollout, _meta(thread, "/repo/codex-only"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    monkeypatch.setattr("cc_explorer._claude_paths._get_projects_dir", lambda: tmp_path / "none")

    projects = discover_projects()

    assert [(p.path, p.session_count) for p in projects] == [("/repo/codex-only", 1)]


def test_session_summary_exposes_harness(session_info) -> None:
    session_info.harness = Harness.codex

    summary = SessionSummary.from_session_info(session_info)

    assert summary.harness is Harness.codex


def test_read_tools_expose_one_shared_harness_filter() -> None:
    from cc_explorer import mcp_server

    for tool in (
        mcp_server.list_projects,
        mcp_server.list_project_sessions,
        mcp_server.search_projects,
        mcp_server.grep_session,
        mcp_server.grep_sessions,
        mcp_server.read_turn,
        mcp_server.browse_session,
    ):
        assert "harnesses" in inspect.signature(tool).parameters


def test_codex_calling_thread_is_detected(monkeypatch) -> None:
    from cc_explorer.mcp_server import _current_session_id

    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "01a04576-fee2-7ee0-a898-58bb50387cc5")

    assert _current_session_id() == "01a04576-fee2-7ee0-a898-58bb50387cc5"


def test_read_turn_finds_synthetic_codex_turn_without_session(monkeypatch, tmp_path: Path) -> None:
    from cc_explorer.mcp_server import read_turn

    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(
        rollout,
        _meta(thread, "/repo/example"),
        _line("2026-08-27T12:00:01Z", 1, "response_item", {
            "type": "message", "id": "msg_user", "role": "user",
            "content": [{"type": "input_text", "text": "find me by generated turn id"}],
        }),
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    turn = CodexProvider(tmp_path).load_transcript((rollout,))[0].uuid.full

    response = read_turn(
        turn=turn,
        projects=["/repo/example"],
        harnesses=["codex"],
        context=0,
    )

    assert response.harness is Harness.codex
    assert "generated turn id" in response.chats[0]


def test_reverted_thread_uses_newest_rollout_for_stable_thread_id(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    old_rollout_id = "01a04500-0000-7000-8000-000000000001"
    new_rollout_id = "01a04500-0000-7000-8000-000000000002"
    old = tmp_path / "sessions/2026/08/26" / f"rollout-old-{old_rollout_id}.jsonl"
    new = tmp_path / "sessions/2026/08/27" / f"rollout-new-{new_rollout_id}.jsonl"
    _write_rollout(old, _meta(thread, "/repo/example"))
    _write_rollout(new, _meta(thread, "/repo/example"))
    os.utime(old, (1, 1))
    os.utime(new, (2, 2))

    refs = CodexProvider(tmp_path).discover_sessions(["/repo/example"])

    assert len(refs) == 1
    assert refs[0].path == new


def test_bootstrap_user_context_before_first_turn_is_not_human_speech(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(
        rollout,
        _meta(thread, "/repo/example"),
        _line("2026-08-27T12:00:00Z", 1, "response_item", {
            "type": "message", "id": "bootstrap", "role": "user",
            "content": [{"type": "input_text", "text": "# AGENTS.md injected context"}],
        }),
        _line("2026-08-27T12:00:01Z", 2, "turn_context", {"turn_id": "turn_1"}),
        _line("2026-08-27T12:00:02Z", 3, "response_item", {
            "type": "message", "id": "human", "role": "user",
            "content": [{"type": "input_text", "text": "actual human prompt"}],
        }),
    )

    entries = CodexProvider(tmp_path).load_transcript((rollout,))

    assert [entry.display(0) for entry in entries] == ["actual human prompt"]


def test_subagent_projection_excludes_inherited_parent_history(tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(
        rollout,
        _meta(thread, "/repo/example", subagent_history_start_ordinal=3),
        _line("2026-08-27T12:00:00Z", 1, "response_item", {
            "type": "message", "id": "parent", "role": "user",
            "content": [{"type": "input_text", "text": "parent prompt"}],
        }),
        _line("2026-08-27T12:00:01Z", 3, "response_item", {
            "type": "message", "id": "dispatch", "role": "user",
            "content": [{"type": "input_text", "text": "subagent assignment"}],
        }),
    )

    entries = CodexProvider(tmp_path).load_transcript((rollout,))

    assert [entry.display(0) for entry in entries] == ["subagent assignment"]


def test_codex_worktree_sessions_pool_into_main_project(monkeypatch, tmp_path: Path) -> None:
    thread = "01a04576-fee2-7ee0-a898-58bb50387cc5"
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{thread}.jsonl"
    _write_rollout(rollout, _meta(thread, "/repo/worktree"))
    monkeypatch.setattr(
        "cc_explorer._claude_paths._get_worktree_paths",
        lambda cwd: ["/repo/main", "/repo/worktree"],
    )

    refs = CodexProvider(tmp_path).discover_sessions(["/repo/main"])

    assert len(refs) == 1
    assert refs[0].project_path == "/repo/main"
    assert refs[0].worktree == "worktree"
