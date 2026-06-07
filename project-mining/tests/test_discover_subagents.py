"""Tests for bottom-up subagent discovery and its reconciliation with the
top-down dispatch parse.

extract_subagents only sees agents whose spawn the parent transcript recorded.
discover_subagents adds a filesystem walk over `<session>/subagents/**` so
orphan transcripts — notably workflow-orchestrated agents under
`subagents/workflows/<runId>/` — stop being invisible. These tests pin:

  - collect_agent_files: recursive walk, meta.json sidecar parsing, workflow
    runId extraction, missing-meta tolerance.
  - discover_subagents merge matrix: dispatched+file (dispatched),
    dispatched-no-file (dispatch_only), file-no-dispatch (orphan).
  - scan_output_file_stats recovering an orphan's prompt/result from its own
    transcript, since the parent has no record of it.
"""

import json
from pathlib import Path
from unittest.mock import patch

from cc_explorer.parser import ConversationRef, load_transcript
from cc_explorer.search import load_sessions
from cc_explorer.subagents import (
    AgentFile,
    collect_agent_files,
    discover_subagents,
    resolve_subagents_dir,
    scan_output_file_stats,
)

SID = "5c23db0a-12d4-4042-9119-738da66c60e0"
TS = "2026-03-15T10:30:00.000Z"


def _write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")


def _agent_transcript(agent_id: str, prompt: str, result: str) -> list[dict]:
    """A minimal but parser-valid subagent transcript: one user turn, one assistant turn."""
    return [
        {
            "type": "user",
            "uuid": f"u-{agent_id}",
            "timestamp": TS,
            "sessionId": SID,
            "agentId": agent_id,
            "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
        },
        {
            "type": "assistant",
            "uuid": f"a-{agent_id}",
            "parentUuid": f"u-{agent_id}",
            "timestamp": TS,
            "sessionId": SID,
            "agentId": agent_id,
            "message": {
                "id": "m1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4",
                "content": [{"type": "text", "text": result}],
            },
        },
    ]


def _dispatch_assistant(tool_use_id: str, prompt: str, desc: str) -> dict:
    return {
        "type": "assistant",
        "uuid": f"asst-{tool_use_id}",
        "timestamp": TS,
        "sessionId": SID,
        "message": {
            "id": "mm",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-4",
            "content": [
                {
                    "type": "tool_use",
                    "id": tool_use_id,
                    "name": "Agent",
                    "input": {
                        "description": desc,
                        "subagent_type": "general-purpose",
                        "prompt": prompt,
                    },
                }
            ],
        },
    }


def _dispatch_result(tool_use_id: str, agent_id: str) -> dict:
    return {
        "type": "user",
        "uuid": f"res-{tool_use_id}",
        "timestamp": TS,
        "sessionId": SID,
        "toolUseResult": {
            "status": "completed",
            "agentId": agent_id,
            "totalTokens": 1234,
        },
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "done"}],
                }
            ],
        },
    }


def _meta(path: Path, **fields) -> None:
    path.with_suffix(".meta.json").write_text(json.dumps(fields), encoding="utf-8")


# =============================================================================
# Filesystem walk
# =============================================================================


def test_resolve_subagents_dir_strips_jsonl_suffix(tmp_path):
    session = tmp_path / f"{SID}.jsonl"
    assert resolve_subagents_dir(session) == tmp_path / SID / "subagents"


def test_collect_agent_files_missing_dir_is_empty(tmp_path):
    assert collect_agent_files(tmp_path / "nope") == []


def test_collect_agent_files_walks_nested_and_reads_meta(tmp_path):
    subdir = tmp_path / SID / "subagents"
    top = subdir / "agent-aaa111.jsonl"
    nested = subdir / "workflows" / "wf_xyz" / "agent-bbb222.jsonl"
    _write_jsonl(top, _agent_transcript("aaa111", "p", "r"))
    _write_jsonl(nested, _agent_transcript("bbb222", "p", "r"))
    _meta(top, agentType="Explore", description="top one", toolUseId="toolu_TOP")
    _meta(nested, agentType="workflow-subagent")

    files = {f.agent_id: f for f in collect_agent_files(subdir)}
    assert set(files) == {"aaa111", "bbb222"}

    assert files["aaa111"].workflow_run_id is None
    assert files["aaa111"].agent_type == "Explore"
    assert files["aaa111"].tool_use_id == "toolu_TOP"

    assert files["bbb222"].workflow_run_id == "wf_xyz"
    assert files["bbb222"].agent_type == "workflow-subagent"
    assert files["bbb222"].tool_use_id == ""  # orphan — no parent dispatch recorded


def test_collect_agent_files_tolerates_absent_meta(tmp_path):
    subdir = tmp_path / SID / "subagents"
    f = subdir / "agent-nometa.jsonl"
    _write_jsonl(f, _agent_transcript("nometa", "p", "r"))
    # no .meta.json written
    [af] = collect_agent_files(subdir)
    assert af == AgentFile(agent_id="nometa", path=f)


# =============================================================================
# Merge matrix
# =============================================================================


def _setup_session(tmp_path) -> Path:
    """A session with: one dispatch that has a file, one dispatch with no file,
    and one orphan workflow transcript with no dispatch."""
    session = tmp_path / f"{SID}.jsonl"
    _write_jsonl(
        session,
        [
            _dispatch_assistant("toolu_HASFILE", "do the matched work", "matched"),
            _dispatch_result("toolu_HASFILE", "aid_matched"),
            # a dispatch whose transcript never landed on disk
            _dispatch_assistant("toolu_NOFILE", "ran but no transcript", "ghost"),
            _dispatch_result("toolu_NOFILE", "aid_ghost"),
        ],
    )
    subdir = tmp_path / SID / "subagents"
    matched = subdir / "agent-aid_matched.jsonl"
    _write_jsonl(matched, _agent_transcript("aid_matched", "matched prompt", "matched result"))
    _meta(matched, agentType="general-purpose", description="matched", toolUseId="toolu_HASFILE")

    orphan = subdir / "workflows" / "wf_run1" / "agent-aid_orphan.jsonl"
    _write_jsonl(
        orphan,
        _agent_transcript("aid_orphan", "ORPHAN PROMPT TEXT", "ORPHAN RESULT TEXT"),
    )
    _meta(orphan, agentType="workflow-subagent")
    return session


def test_discover_merge_sources(tmp_path):
    by_source = {}
    for a in discover_subagents(_setup_session(tmp_path)):
        by_source.setdefault(a.source, []).append(a)

    assert sorted(by_source) == ["dispatch_only", "dispatched", "orphan"]
    assert len(by_source["dispatched"]) == 1
    assert len(by_source["dispatch_only"]) == 1
    assert len(by_source["orphan"]) == 1


def test_discover_dispatched_gets_transcript_resolved(tmp_path):
    agents = discover_subagents(_setup_session(tmp_path))
    matched = next(a for a in agents if a.source == "dispatched")
    # toolUseId correlation backfilled the agent_id and resolved the on-disk file
    assert matched.agent_id == "aid_matched"
    assert matched.output_file_exists
    assert matched.output_file_resolved.endswith("agent-aid_matched.jsonl")
    # parent-side prompt is preserved (the real spawn prompt, not the transcript copy)
    assert matched.prompt == "do the matched work"


def test_discover_orphan_recovers_prompt_and_result_from_transcript(tmp_path):
    agents = discover_subagents(_setup_session(tmp_path))
    orphan = next(a for a in agents if a.source == "orphan")
    assert orphan.workflow_run_id == "wf_run1"
    assert orphan.subagent_type == "workflow-subagent"
    assert orphan.output_file_exists

    # Parent has no record of an orphan — prompt/result come from its own transcript
    scan_output_file_stats(agents)
    assert orphan.prompt == "ORPHAN PROMPT TEXT"
    assert orphan.result_text == "ORPHAN RESULT TEXT"


def test_discover_dispatch_only_has_no_file(tmp_path):
    agents = discover_subagents(_setup_session(tmp_path))
    ghost = next(a for a in agents if a.source == "dispatch_only")
    assert not ghost.output_file_exists
    assert ghost.tool_use_id == "toolu_NOFILE"


def test_discover_from_preloaded_entries_matches_path_form(tmp_path):
    """Passing entries (the orientation hot path) yields the identical population
    as letting discover_subagents re-read the transcript itself."""
    session = _setup_session(tmp_path)
    from_path = discover_subagents(session)
    from_entries = discover_subagents(session, entries=load_transcript(session))

    assert len(from_path) == len(from_entries)
    assert [a.source for a in from_path] == [a.source for a in from_entries]
    assert [a.agent_id for a in from_path] == [a.agent_id for a in from_entries]


# =============================================================================
# Orientation count: load_sessions surfaces the present population
# =============================================================================


def _parent_chat() -> list[dict]:
    """A parent transcript that dispatches NO agents directly — one human prompt,
    one assistant reply. Stands in for a session that orchestrates only via a
    Workflow (whose children land on disk but never as Task/Agent blocks)."""
    return [
        {
            "type": "user",
            "uuid": "u-root",
            "timestamp": TS,
            "sessionId": SID,
            "message": {
                "role": "user",
                "content": [{"type": "text", "text": "kick off the workflow"}],
            },
        },
        {
            "type": "assistant",
            "uuid": "a-root",
            "timestamp": TS,
            "sessionId": SID,
            "message": {
                "id": "m0",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4",
                "content": [{"type": "text", "text": "running it"}],
            },
        },
    ]


def _setup_workflow_only_session(tmp_path, n: int = 3) -> Path:
    session = tmp_path / f"{SID}.jsonl"
    _write_jsonl(session, _parent_chat())
    subdir = tmp_path / SID / "subagents"
    for i in range(1, n + 1):
        f = subdir / "workflows" / "wf_run1" / f"agent-aid_wf{i}.jsonl"
        _write_jsonl(f, _agent_transcript(f"aid_wf{i}", f"prompt {i}", f"result {i}"))
        _meta(f, agentType="workflow-subagent")
    return session


def test_load_sessions_counts_workflow_orphans_as_present(tmp_path):
    """The orphan-blind-spot fix: a workflow-only session has agent_count==0
    top-down, but agents_present reflects the on-disk population so min_agents
    no longer gates it out. user_turns counts the single human prompt."""
    session = _setup_workflow_only_session(tmp_path, n=3)
    conversations = {SID: ConversationRef(path=session, worktree=None)}
    with patch("cc_explorer.search.load_conversations", return_value=conversations):
        sessions = load_sessions(str(tmp_path), with_agents_present=True)

    assert len(sessions) == 1
    s = sessions[0]
    assert s.stats.agent_count == 0  # nothing dispatched top-down
    assert s.agents_present == 3  # all three workflow orphans discovered
    assert s.user_turns == 1  # one human prompt, despite the fan-out


def test_load_sessions_present_matches_discover(tmp_path):
    """agents_present is exactly len(discover_subagents) so list_project_sessions
    and list_session_agents can never disagree on the count."""
    session = _setup_session(tmp_path)  # 1 dispatched + 1 dispatch_only + 1 orphan
    conversations = {SID: ConversationRef(path=session, worktree=None)}
    with patch("cc_explorer.search.load_conversations", return_value=conversations):
        sessions = load_sessions(str(tmp_path), with_agents_present=True)

    assert sessions[0].agents_present == len(discover_subagents(session))
    assert sessions[0].agents_present == 3


def test_load_sessions_skips_subagent_walk_by_default(tmp_path):
    """agents_present is opt-in: tools that only need transcripts (read_turn,
    grep_session, search_projects, ...) must not pay the per-session subagents
    walk. Default leaves the count at 0 without touching the filesystem tree."""
    session = _setup_workflow_only_session(tmp_path, n=3)
    conversations = {SID: ConversationRef(path=session, worktree=None)}
    with patch("cc_explorer.search.load_conversations", return_value=conversations):
        with patch("cc_explorer.search.discover_subagents") as walk:
            sessions = load_sessions(str(tmp_path))

    walk.assert_not_called()
    assert sessions[0].agents_present == 0
