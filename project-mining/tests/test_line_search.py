"""Line-level search must answer exactly what a full parse answers.

search_projects parses only the JSONL lines rg matched (one line is one entry)
and reads session dates from the head of each transcript. The contract: same
entries, same counts, same examples, same dates as the full-parse path.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cc_explorer.corpus import Corpus, SessionRef
from cc_explorer.providers import Harness, provider_for
from cc_explorer.search import (
    ENTRY_TYPE_MAP,
    SessionHead,
    SessionInfo,
    triage_lines,
    triage_multi,
)
from cc_explorer.utils import PrefixId

SID = "11111111-1111-1111-1111-111111111111"
AGENT_ID = "agent123-aaaa-bbbb-cccc-dddddddddddd"
THREAD = "01a04576-fee2-7ee0-a898-58bb50387cc5"

needs_rg = pytest.mark.skipif(shutil.which("rg") is None, reason="rg not on PATH")


def _write(path: Path, lines: list) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines) + "\n"
    )


def _offsets(path: Path) -> list[int]:
    out, pos = [], 0
    for line in path.read_bytes().splitlines(keepends=True):
        out.append(pos)
        pos += len(line)
    return out


def _claude_line(kind: str, text: str, uuid: str, ts: str) -> dict:
    content = [{"type": "text", "text": text}]
    message = (
        {"role": "user", "content": content}
        if kind == "user"
        else {"id": f"msg_{uuid}", "type": "message", "role": "assistant",
              "model": "m", "content": content}
    )
    return {"type": kind, "uuid": uuid, "timestamp": ts, "sessionId": SID, "message": message}


def _claude_session(tmp_path: Path) -> SessionRef:
    main = tmp_path / f"{SID}.jsonl"
    _write(main, [
        {"type": "custom-title", "customTitle": "needle title"},  # structural
        "{broken json needle",
        {"type": "agent-setting", "marker": "needle"},  # unsupported
        _claude_line("user", "first needle prompt", "00000000-0000-0000-0000-000000000001",
                     "2026-03-15T10:30:00Z"),
        _claude_line("assistant", "needle answer, twice: needle", "00000000-0000-0000-0000-000000000002",
                     "2026-03-15T10:31:00Z"),
        _claude_line("user", "unrelated haystack", "00000000-0000-0000-0000-000000000003",
                     "2026-03-15T10:32:00Z"),
    ])
    _write(tmp_path / SID / "subagents" / f"agent-{AGENT_ID}.jsonl", [
        _claude_line("user", "subagent NEEDLE work", "00000000-0000-0000-0000-0000000000a1",
                     "2026-03-15T10:33:00Z"),
    ])
    return SessionRef(session_id=PrefixId(SID), path=main, project_path=str(tmp_path))


def _codex_line(ts: str, ordinal: int, kind: str, payload: dict) -> dict:
    return {"timestamp": ts, "ordinal": ordinal, "type": kind, "payload": payload}


def _codex_msg(ts: str, ordinal: int, role: str, text: str, mid: str) -> dict:
    kind = "input_text" if role == "user" else "output_text"
    return _codex_line(ts, ordinal, "response_item", {
        "type": "message", "id": mid, "role": role,
        "content": [{"type": kind, "text": text}],
    })


def _codex_meta(thread: str, **extra) -> dict:
    return _codex_line("2026-08-27T12:00:00Z", 0, "session_meta", {
        "session_id": thread, "id": thread, "timestamp": "2026-08-27T12:00:00Z",
        "cwd": "/repo/example", "source": "cli", **extra,
    })


def _codex_session(tmp_path: Path) -> SessionRef:
    rollout = tmp_path / "sessions/2026/08/27" / f"rollout-date-{THREAD}.jsonl"
    _write(rollout, [
        _codex_meta(THREAD),
        _codex_msg("2026-08-27T12:00:01Z", 1, "user", "bootstrap needle context", "boot"),
        _codex_line("2026-08-27T12:00:02Z", 2, "turn_context", {"turn_id": "needle"}),
        _codex_msg("2026-08-27T12:00:03Z", 3, "user", "human needle prompt", "human"),
        _codex_line("2026-08-27T12:00:04Z", 4, "response_item", {
            "type": "function_call", "call_id": "c1", "name": "shell",
            "arguments": json.dumps({"cmd": "grep needle"}),
        }),
        _codex_line("2026-08-27T12:00:05Z", 5, "response_item", {
            "type": "function_call_output", "call_id": "c1", "output": "needle found",
        }),
    ])
    return SessionRef(
        session_id=PrefixId(THREAD), path=rollout, paths=(rollout,),
        project_path="/repo/example", harness=Harness.codex,
    )


def _codex_chain(tmp_path: Path) -> SessionRef:
    """A reverted thread: the base rollout is read only up to the fork."""
    base_id = "01a04500-0000-7000-8000-000000000001"
    child_id = "01a04500-0000-7000-8000-000000000002"
    base = tmp_path / "sessions/2026/08/26" / f"rollout-date-{base_id}.jsonl"
    child = tmp_path / "sessions/2026/08/27" / f"rollout-date-{child_id}.jsonl"
    base_lines = [
        _codex_meta(base_id),
        _codex_msg("2026-08-26T12:00:01Z", 1, "user", "inherited needle", "b1"),
        _codex_msg("2026-08-26T12:00:02Z", 2, "assistant", "needle past the fork", "b2"),
    ]
    _write(base, base_lines)
    end = len((json.dumps(base_lines[0]) + "\n" + json.dumps(base_lines[1]) + "\n").encode())
    _write(child, [
        _codex_meta(child_id, history_mode="paginated", history_base={
            "thread_id": base_id, "end_ordinal_exclusive": 2, "end_byte_offset": end,
        }),
        _codex_msg("2026-08-27T12:00:01Z", 1, "assistant", "continued needle", "c1"),
    ])
    return SessionRef(
        session_id=PrefixId(child_id), path=child, paths=(base, child),
        project_path="/repo/example", harness=Harness.codex,
    )


def _codex_subagent(tmp_path: Path) -> SessionRef:
    rollout = tmp_path / "sessions/2026/08/28" / f"rollout-date-{THREAD}.jsonl"
    _write(rollout, [
        _codex_meta(THREAD, subagent_history_start_ordinal=3),
        _codex_msg("2026-08-28T12:00:00Z", 1, "user", "parent needle", "p"),
        _codex_msg("2026-08-28T12:00:01Z", 3, "user", "subagent needle", "s"),
    ])
    return SessionRef(
        session_id=PrefixId(THREAD), path=rollout, paths=(rollout,),
        project_path="/repo/example", harness=Harness.codex,
    )


BUILDERS = [_claude_session, _codex_session, _codex_chain, _codex_subagent]


def _render(entries) -> list[tuple[str, str]]:
    return [(e.uuid.full, e.display(0)) for e in entries]


@pytest.mark.parametrize("build", BUILDERS)
def test_every_line_reproduces_the_full_parse(tmp_path, build):
    ref = build(tmp_path)
    provider = provider_for(ref.harness)
    paths = ref.paths or (ref.path,)
    offsets = {p: _offsets(p) for p in paths}
    assert _render(provider.load_entries_at(paths, offsets)) == _render(
        provider.load_transcript(paths)
    )


@pytest.mark.parametrize("build", BUILDERS)
def test_head_timestamp_matches_full_parse(tmp_path, build):
    ref = build(tmp_path)
    info = SessionInfo.load(ref)
    assert info is not None
    assert SessionHead.load(ref).first_timestamp == info.first_timestamp


@needs_rg
@pytest.mark.parametrize("role", ["user", "assistant", "all"])
def test_triage_lines_matches_triage_multi(tmp_path, role):
    refs = [
        _claude_session(tmp_path / "claude"),
        _codex_session(tmp_path / "codex"),
        _codex_chain(tmp_path / "chain"),
    ]
    patterns = ["needle", "haystack", "absent-term"]
    base_types = ENTRY_TYPE_MAP[role]

    line_hits = Corpus(refs).matching_lines(patterns)
    assert line_hits is not None
    heads = [SessionHead.load(ref) for ref, _ in line_hits]
    offsets = {f: o for _, files in line_hits for f, o in files.items()}
    got = triage_lines(heads, offsets, patterns, base_types=base_types)

    sessions = [s for ref in refs if (s := SessionInfo.load(ref)) is not None]
    want = triage_multi(sessions, patterns, base_types=base_types)

    def flat(results):
        return [
            (pat, sorted((r.session.session_id.full, r.count, r.first_match_example,
                          r.agent_id.full if r.agent_id else None) for r in rs))
            for pat, rs in results
        ]

    assert flat(got) == flat(want)
    assert any(rs for _, rs in got)


def test_rg_unsafe_pattern_falls_back(tmp_path):
    ref = _claude_session(tmp_path)
    assert Corpus([ref]).matching_lines(["^needle"]) is None
