"""Tests for the subagent-inclusive search corpus (issue #22).

Search used to read only the main session transcript; a subagent's internal
activity was invisible. session_sources() now expands a session into its main
transcript plus every subagent body under <sessionId>/subagents/ (incl.
workflows/<runId>/), and matches carry the agent_id they came from.
"""

import json
from pathlib import Path

from cc_explorer.search import (
    SessionInfo,
    session_sources,
    triage_multi,
    search_multi,
)
from cc_explorer.models import HumanEntry

SID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "agent123-aaaa-bbbb-cccc-dddddddddddd"
WF_AGENT_ID = "wf000000-aaaa-bbbb-cccc-dddddddddddd"


def _human_line(text: str, uuid: str) -> dict:
    return {
        "type": "user",
        "uuid": uuid,
        "timestamp": "2026-03-15T10:30:00Z",
        "sessionId": SID,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _write(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _build(tmp_path, *, main_text, agent_text=None, wf_text=None) -> SessionInfo:
    session_path = tmp_path / f"{SID}.jsonl"
    _write(session_path, [_human_line(main_text, "00000000-0000-0000-0000-000000000001")])
    subdir = tmp_path / SID / "subagents"
    if agent_text is not None:
        _write(
            subdir / f"agent-{AGENT_ID}.jsonl",
            [_human_line(agent_text, "00000000-0000-0000-0000-0000000000a1")],
        )
    if wf_text is not None:
        _write(
            subdir / "workflows" / "run-xyz" / f"agent-{WF_AGENT_ID}.jsonl",
            [_human_line(wf_text, "00000000-0000-0000-0000-0000000000b1")],
        )
    return SessionInfo(
        session_id=SID,
        path=session_path,
        title="t",
        first_timestamp=None,
        message_count=1,
    )


def test_session_sources_includes_main_and_subagents(tmp_path):
    session = _build(tmp_path, main_text="main", agent_text="sub", wf_text="wf")
    sources = session_sources(session)
    # main (agent_id None) + one direct subagent + one workflow orphan
    agent_ids = {s.agent_id for s in sources}
    assert None in agent_ids
    assert any(s.agent_id == AGENT_ID for s in sources)
    assert any(s.agent_id == WF_AGENT_ID for s in sources)
    assert len(sources) == 3


def test_match_only_in_subagent_is_found_and_attributed(tmp_path):
    session = _build(tmp_path, main_text="nothing here", agent_text="UNIQUETOKEN inside agent")
    results = triage_multi([session], ["UNIQUETOKEN"])
    pattern, hits = results[0]
    assert pattern == "UNIQUETOKEN"
    assert len(hits) == 1
    assert hits[0].count == 1
    assert hits[0].agent_id == AGENT_ID  # attributed to the subagent body


def test_match_in_workflow_orphan_is_found(tmp_path):
    session = _build(tmp_path, main_text="nothing", wf_text="ORPHANTOKEN in workflow agent")
    results = triage_multi([session], ["ORPHANTOKEN"])
    _, hits = results[0]
    assert len(hits) == 1
    assert hits[0].agent_id == WF_AGENT_ID


def test_main_transcript_match_has_no_agent(tmp_path):
    session = _build(tmp_path, main_text="MAINTOKEN here", agent_text="unrelated")
    results = triage_multi([session], ["MAINTOKEN"])
    _, hits = results[0]
    assert len(hits) == 1
    assert hits[0].agent_id is None  # main transcript → no agent provenance


def test_search_multi_carries_agent_on_match_hits(tmp_path):
    session = _build(tmp_path, main_text="nothing", agent_text="GREPTOKEN in agent body")
    out = search_multi([session], ["GREPTOKEN"], context=0)
    pattern_results = out[session.session_id]
    _, matches, total = pattern_results[0]
    assert total == 1
    assert matches[0].agent_id == AGENT_ID
