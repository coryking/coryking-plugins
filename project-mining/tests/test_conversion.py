"""Tests for session<->subagent conversion (convert_session / delete_conversions).

All fixtures are SYNTHESIZED — builder helpers emit fake transcript dicts; no
real conversation content appears here (this is a public repo). The fake
~/.claude/projects root is a tmp_path-based CLAUDE_CONFIG_DIR, with
_get_worktree_paths patched to [] so each project pools as itself (no git).

Coverage:
  - roundtrip fidelity: session -> subagent -> session preserves conversation text
  - tree source relinearizes to a valid linear chain
  - trailing-noise trim
  - prefix ambiguity raises
  - title collision raises
  - refuse to overwrite an existing session file
  - cross-project src resolution
  - x-converter-provenance line is the sole trust surface: present at the top of
    agent jsonl (line 1) and session jsonl (after custom-title); meta.json carries
    only the three standard keys
  - delete_conversions: refuses subagents with no provenance line, refuses grown
    subagents (growth guard), refuses sessions unconditionally (even tagged),
    deletes ungrown tagged subagents; sweep reports growth-skips
  - lineage accumulates across two conversions
  - conversion-tagged agents excluded from search corpus but present in
    list_session_agents (labeled)
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastmcp.exceptions import ToolError

import cc_explorer._claude_paths as paths
import cc_explorer.mcp_server as srv
from cc_explorer.search import SessionInfo, session_sources, triage_multi
from cc_explorer.subagents import collect_agent_files, resolve_subagents_dir
from cc_explorer.utils import PrefixId

TS = "2026-03-15T10:30:00.000Z"
CWD = "/Users/test/projects/demo"
PROJECT = "/Users/test/projects/demo"


# =============================================================================
# Synthesized transcript builders
# =============================================================================


def _user(uuid: str, text: str, *, parent: str | None = None, **extra) -> dict:
    # NOTE: cwd is intentionally NOT set here — write_session stamps it per-project
    # so a session written to a given project pools under that project's dir.
    line = {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": TS,
        "sessionId": "SID",
        "version": "2.1.175",
        "gitBranch": "main",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    line.update(extra)
    return line


def _assistant(uuid: str, text: str, *, parent: str | None = None, model="claude-opus-4-8", **extra) -> dict:
    line = {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": TS,
        "sessionId": "SID",
        "version": "2.1.175",
        "gitBranch": "main",
        "message": {
            "id": "msg_" + uuid,
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": text}],
        },
    }
    line.update(extra)
    return line


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(l) for l in lines) + "\n", encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


# The own-type provenance line is the single trust surface (meta.json is not).
PROVENANCE_TYPE = "x-converter-provenance"


def _provenance_lines(lines: list[dict]) -> list[dict]:
    return [l for l in lines if l.get("type") == PROVENANCE_TYPE]


def _convo_only(lines: list[dict]) -> list[dict]:
    """Body lines, dropping the provenance line and any session header lines."""
    return [l for l in lines if l.get("type") in ("user", "assistant")]


def _fake_provenance_line(
    agent_id: str, *, lines_at_creation: int, kind: str = "session", converted_at: str = TS
) -> dict:
    """A shape-valid x-converter-provenance line for synthesizing tagged artifacts.

    `converted_at` defaults to the fixed TS (well in the past relative to test-run
    `now`, so the reaper sees these as cold); pass a fresh ISO timestamp to
    synthesize a 'young' artifact the reaper must spare.
    """
    return {
        "type": PROVENANCE_TYPE,
        "x_converter": {
            "tool": "convert_session",
            "v": 1,
            "from": {"kind": kind, "id": "x", "project": PROJECT},
            "converted_at": converted_at,
            "lines_at_creation": lines_at_creation,
        },
        "sessionId": SID_PARENT,
        "agentId": agent_id,
    }


# =============================================================================
# Fake ~/.claude/projects environment
# =============================================================================


def _sanitize(p: str) -> str:
    return paths._sanitize_path(p)


@pytest.fixture
def fake_claude(tmp_path, monkeypatch):
    """A tmp CLAUDE_CONFIG_DIR with no git (each cwd pools as its own project).

    Returns a helper object with `.project_dir(path)` to get the encoded dir for
    a project path and `.write_session(...)` to lay down a session transcript.
    """
    config = tmp_path / ".claude"
    (config / "projects").mkdir(parents=True)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    # No git anywhere → _get_worktree_paths returns [] → single-dir pooling.
    monkeypatch.setattr(paths, "_get_worktree_paths", lambda cwd: [])
    # Identity canonicalize so our synthetic absolute cwds stay stable.
    monkeypatch.setattr(paths, "_canonicalize_path", lambda p: p)

    class Env:
        projects_root = config / "projects"

        def project_dir(self, project_path: str) -> Path:
            return self.projects_root / _sanitize(project_path)

        def write_session(self, session_id: str, lines: list[dict], project_path: str = PROJECT) -> Path:
            for l in lines:
                l.setdefault("cwd", project_path)
                l["sessionId"] = session_id
            d = self.project_dir(project_path)
            d.mkdir(parents=True, exist_ok=True)
            path = d / f"{session_id}.jsonl"
            _write_jsonl(path, lines)
            return path

    return Env()


SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_PARENT = "bbbbbbbb-1111-2222-3333-444444444444"


def _simple_session_lines() -> list[dict]:
    return [
        _user("u1111111-0000-0000-0000-000000000001", "ALPHA question"),
        _assistant("a1111111-0000-0000-0000-000000000002", "BETA answer", parent="u1111111-0000-0000-0000-000000000001"),
        _user("u1111111-0000-0000-0000-000000000003", "GAMMA followup", parent="a1111111-0000-0000-0000-000000000002"),
        _assistant("a1111111-0000-0000-0000-000000000004", "DELTA reply", parent="u1111111-0000-0000-0000-000000000003"),
    ]


def _convo_texts(lines: list[dict]) -> list[str]:
    """Ordered conversation text of user/assistant lines."""
    out = []
    for l in lines:
        if l.get("type") not in ("user", "assistant"):
            continue
        content = l["message"]["content"]
        if isinstance(content, str):
            out.append(content)
        else:
            out.append("".join(b.get("text", "") for b in content if b.get("type") == "text"))
    return out


# =============================================================================
# session_to_subagent
# =============================================================================


def test_session_to_subagent_basic(fake_claude):
    fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A,
        src_project=PROJECT,
        dest_parent_session=SID_PARENT,
    )

    assert resp.operation == "copy"
    assert resp.direction == "session_to_subagent"
    assert resp.created_id.startswith("a") and len(resp.created_id) == 17
    assert resp.invocation == f'SendMessage(to: "{resp.created_id}")'
    assert resp.parent_session == SID_PARENT
    assert resp.turns == 4
    assert resp.tail_state == "clean"
    assert resp.suggested_handoff and "[CONVERTED SESSION]" in resp.suggested_handoff

    # The new agent transcript lives under the PARENT session's subagents dir.
    parent_dir = fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents"
    agent_file = parent_dir / f"agent-{resp.created_id}.jsonl"
    meta_file = parent_dir / f"agent-{resp.created_id}.meta.json"
    assert agent_file.exists() and meta_file.exists()

    lines = _read_jsonl(agent_file)

    # The x-converter-provenance line is the FIRST line and the only trust surface.
    assert lines[0]["type"] == PROVENANCE_TYPE
    prov = lines[0]["x_converter"]
    assert prov["tool"] == "convert_session" and prov["v"] == 1
    assert prov["from"]["kind"] == "session" and prov["from"]["id"] == SID_A
    assert lines[0]["sessionId"] == SID_PARENT
    assert lines[0]["agentId"] == resp.created_id
    # lines_at_creation counts the whole file (provenance line included).
    assert prov["lines_at_creation"] == len(lines)

    body = _convo_only(lines)
    assert all(l["isSidechain"] is True for l in body)
    assert all(l["agentId"] == resp.created_id for l in body)
    assert all(l["sessionId"] == SID_PARENT for l in body)
    assert _convo_texts(body) == ["ALPHA question", "BETA answer", "GAMMA followup", "DELTA reply"]

    # meta.json carries ONLY the three standard keys — NO conversion/x_converter
    # key (the harness rewrites meta.json on resume and drops unknown keys).
    meta = json.loads(meta_file.read_text())
    assert set(meta.keys()) == {"agentType", "description", "toolUseId"}
    assert meta["agentType"] == "general-purpose"
    assert meta["description"] == f"converted from session {SID_A[:8]} (demo)"
    assert meta["toolUseId"].startswith("toolu_")


def test_session_to_subagent_source_untouched(fake_claude):
    src = fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    before = src.read_text()

    srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A,
        src_project=PROJECT,
        dest_parent_session=SID_PARENT,
    )
    assert src.read_text() == before  # never modified


def test_session_to_subagent_relinearizes_tree(fake_claude):
    # A TREE: two assistant siblings off the same user turn (a message edit).
    # EDIT-ONE is an abandoned branch; EDIT-TWO is the active branch (the last
    # user and assistant turns continue from it). The active-thread extraction
    # walks parentUuid backward from the tip, so EDIT-ONE is DROPPED.
    lines = [
        _user("u0000000-0000-0000-0000-000000000001", "ROOT"),
        _assistant("a0000000-0000-0000-0000-0000000000a1", "EDIT-ONE", parent="u0000000-0000-0000-0000-000000000001"),
        _assistant("a0000000-0000-0000-0000-0000000000a2", "EDIT-TWO", parent="u0000000-0000-0000-0000-000000000001"),
        _user("u0000000-0000-0000-0000-000000000003", "NEXT", parent="a0000000-0000-0000-0000-0000000000a2"),
        _assistant("a0000000-0000-0000-0000-000000000004", "LAST", parent="u0000000-0000-0000-0000-000000000003"),
    ]
    fake_claude.write_session(SID_A, lines)
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    agent_file = fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents" / f"agent-{resp.created_id}.jsonl"
    all_lines = _read_jsonl(agent_file)
    assert all_lines[0]["type"] == PROVENANCE_TYPE  # provenance leads
    out = _convo_only(all_lines)

    # Valid linear chain: first parent null, each subsequent points at predecessor.
    assert out[0]["parentUuid"] is None
    for prev, cur in zip(out, out[1:]):
        assert cur["parentUuid"] == prev["uuid"]
    # Active thread: ROOT → EDIT-TWO → NEXT → LAST (4 turns).
    # EDIT-ONE is the abandoned sibling branch and is DROPPED.
    assert len(out) == 4
    assert _convo_texts(out) == ["ROOT", "EDIT-TWO", "NEXT", "LAST"]
    assert resp.dropped_branches == 1


def test_session_to_subagent_trims_trailing_noise(fake_claude):
    lines = _simple_session_lines() + [
        _user("u9999999-0000-0000-0000-000000000099", "[Request interrupted by user]"),
        _user("u9999999-0000-0000-0000-00000000009a", ""),
    ]
    fake_claude.write_session(SID_A, lines)
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    assert resp.trimmed_trailing == 2
    assert resp.turns == 4
    assert resp.tail_state == "clean"


def test_session_to_subagent_pending_user_tail(fake_claude):
    lines = _simple_session_lines() + [
        _user("u8888888-0000-0000-0000-000000000088", "a real unanswered question"),
    ]
    fake_claude.write_session(SID_A, lines)
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    assert resp.trimmed_trailing == 0
    assert resp.tail_state == "pending_user_input"


def test_session_to_subagent_default_parent_is_calling_session(fake_claude, monkeypatch):
    fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID_PARENT)

    resp = srv.convert_session(
        direction="session_to_subagent", src_id=SID_A, src_project=PROJECT,
    )
    assert resp.parent_session == SID_PARENT


def test_session_to_subagent_errors_without_parent(fake_claude, monkeypatch):
    fake_claude.write_session(SID_A, _simple_session_lines())
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    with pytest.raises(ToolError) as exc:
        srv.convert_session(direction="session_to_subagent", src_id=SID_A, src_project=PROJECT)
    assert "dest_parent_session is required" in str(exc.value)


def test_session_to_subagent_reports_nested_agents(fake_claude):
    # Source session has one subagent on disk → nested_agents counts it (not copied).
    src = fake_claude.write_session(SID_A, _simple_session_lines())
    sub_dir = fake_claude.project_dir(PROJECT) / SID_A / "subagents"
    nested_agent = "a" + "f" * 16
    _write_jsonl(
        sub_dir / f"agent-{nested_agent}.jsonl",
        [_user("u-nested-0000-0000-0000-00000000000n", "nested work", agentId=nested_agent, isSidechain=True)],
    )
    (sub_dir / f"agent-{nested_agent}.meta.json").write_text(
        json.dumps({"agentType": "Explore", "description": "d", "toolUseId": "toolu_x"})
    )
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent", src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    assert resp.nested_agents == 1


# =============================================================================
# subagent_to_session
# =============================================================================


def _lay_down_subagent(
    fake_claude,
    agent_id: str,
    *,
    is_conversion_artifact=False,
    lines=None,
    extra_lines=0,
    converted_at: str = TS,
) -> Path:
    """Write a subagent transcript (+meta) under SID_PARENT's subagents dir.

    When `is_conversion_artifact` is True the file is stamped with a real x-converter-
    provenance line at the top (the only trust surface) — its lines_at_creation
    matches the file as written so the growth guard sees no growth. Pass
    `extra_lines` to append N junk lines AFTER recording lines_at_creation,
    simulating a resumed/built-upon conversion (growth guard should then refuse).
    meta.json carries ONLY the three standard keys — never a conversion key.
    """
    sub_dir = fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents"
    body = lines if lines is not None else [
        _user("u-sub-0000-0000-0000-0000000000s1", "SUBALPHA", agentId=agent_id, isSidechain=True),
        _assistant("a-sub-0000-0000-0000-0000000000s2", "SUBBETA", parent="u-sub-0000-0000-0000-0000000000s1", agentId=agent_id, isSidechain=True),
    ]

    out_lines: list[dict]
    if is_conversion_artifact:
        # lines_at_creation = provenance line + body (what we write now).
        created = 1 + len(body)
        prov = _fake_provenance_line(agent_id, lines_at_creation=created, converted_at=converted_at)
        out_lines = [prov] + body
    else:
        out_lines = list(body)

    # Simulate resume/build-up: append junk lines NOT counted in lines_at_creation.
    for i in range(extra_lines):
        out_lines.append(
            _user(f"u-grow-0000-0000-0000-00000000g{i:03d}", f"GROWTH{i}", agentId=agent_id, isSidechain=True)
        )

    _write_jsonl(sub_dir / f"agent-{agent_id}.jsonl", out_lines)
    # meta.json: standard three keys ONLY (no conversion key — it's not trusted).
    meta = {"agentType": "general-purpose", "description": "d", "toolUseId": "toolu_abc"}
    (sub_dir / f"agent-{agent_id}.meta.json").write_text(json.dumps(meta))
    return sub_dir / f"agent-{agent_id}.jsonl"


AGENT_ID = "a" + "1" * 16


def test_subagent_to_session_basic(fake_claude):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    _lay_down_subagent(fake_claude, AGENT_ID)

    resp = srv.convert_session(direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT)

    assert resp.operation == "copy"
    assert resp.direction == "subagent_to_session"
    new_session = resp.created_id
    assert resp.title == f"converted-{AGENT_ID[:8]}"
    assert resp.invocation.startswith(f"claude -r {new_session}")
    assert "--resume" in resp.invocation

    out_path = fake_claude.project_dir(PROJECT) / f"{new_session}.jsonl"
    assert out_path.exists()
    out = _read_jsonl(out_path)

    # Header lines first, then the provenance line (after custom-title), then body.
    assert out[0]["type"] == "mode"
    assert out[1]["type"] == "permission-mode"
    assert out[2]["type"] == "custom-title" and out[2]["customTitle"] == resp.title
    assert out[3]["type"] == PROVENANCE_TYPE
    prov = out[3]["x_converter"]
    assert prov["from"]["kind"] == "subagent" and prov["from"]["id"] == AGENT_ID
    assert out[3]["sessionId"] == new_session
    assert out[3]["agentId"] is None  # null for session conversions
    assert prov["lines_at_creation"] == len(out)  # whole file, provenance included
    body = [l for l in out if l.get("type") in ("user", "assistant")]
    assert all(l["isSidechain"] is False for l in body)
    assert all("agentId" not in l for l in body)
    assert all(l["sessionId"] == new_session for l in body)
    # Filename equals internal sessionId.
    assert out_path.stem == new_session
    assert _convo_texts(body) == ["SUBALPHA", "SUBBETA"]


def test_subagent_to_session_explicit_dest_project(fake_claude):
    """dest_project routes the new session into a different project's dir."""
    dest = "/Users/test/projects/dest"
    fake_claude.write_session(SID_PARENT, _simple_session_lines(), project_path=PROJECT)
    # Seed the dest project so it has a main-worktree dir to write into.
    fake_claude.write_session("eeeeeeee-1111-2222-3333-444444444444", _simple_session_lines(), project_path=dest)
    _lay_down_subagent(fake_claude, AGENT_ID)

    resp = srv.convert_session(
        direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT, dest_project=dest,
    )
    assert resp.project == dest
    assert (fake_claude.project_dir(dest) / f"{resp.created_id}.jsonl").exists()
    assert not (fake_claude.project_dir(PROJECT) / f"{resp.created_id}.jsonl").exists()


def test_subagent_to_session_custom_title(fake_claude):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    _lay_down_subagent(fake_claude, AGENT_ID)

    resp = srv.convert_session(
        direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT, dest_title="my-special-title",
    )
    assert resp.title == "my-special-title"


def test_subagent_to_session_title_collision_raises(fake_claude):
    # A session already carrying the custom title "taken-title".
    titled = _simple_session_lines()
    fake_claude.write_session(
        SID_PARENT,
        [{"type": "custom-title", "customTitle": "taken-title", "sessionId": SID_PARENT}] + titled,
    )
    _lay_down_subagent(fake_claude, AGENT_ID)

    with pytest.raises(ToolError) as exc:
        srv.convert_session(
            direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT, dest_title="taken-title",
        )
    assert "already exists" in str(exc.value).lower()


def test_subagent_to_session_refuses_overwrite(fake_claude, monkeypatch):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    _lay_down_subagent(fake_claude, AGENT_ID)

    # Force convert_subagent_to_session to mint a uuid that already exists on disk.
    existing = "cccccccc-1111-2222-3333-444444444444"
    fake_claude.write_session(existing, _simple_session_lines())
    import cc_explorer.conversion as conv
    monkeypatch.setattr(conv.uuid, "uuid4", lambda: existing)

    with pytest.raises(ToolError) as exc:
        srv.convert_session(direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT)
    assert "overwrite" in str(exc.value).lower()


# =============================================================================
# Roundtrip fidelity
# =============================================================================


def test_roundtrip_preserves_conversation_text(fake_claude, monkeypatch):
    fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    # session -> subagent
    r1 = srv.convert_session(
        direction="session_to_subagent", src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    agent_file = fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents" / f"agent-{r1.created_id}.jsonl"
    mid_texts = _convo_texts(_read_jsonl(agent_file))

    # subagent -> session
    r2 = srv.convert_session(direction="subagent_to_session", src_id=r1.created_id, src_project=PROJECT)
    out_path = fake_claude.project_dir(PROJECT) / f"{r2.created_id}.jsonl"
    final_body = [l for l in _read_jsonl(out_path) if l.get("type") in ("user", "assistant")]
    final_texts = _convo_texts(final_body)

    assert mid_texts == ["ALPHA question", "BETA answer", "GAMMA followup", "DELTA reply"]
    assert final_texts == mid_texts  # text survives both hops


def test_lineage_accumulates_across_two_conversions(fake_claude):
    fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    r1 = srv.convert_session(
        direction="session_to_subagent", src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    assert r1.lineage == [
        {"as": "session", "id": SID_A},
        {"as": "subagent", "id": r1.created_id},
    ]

    r2 = srv.convert_session(direction="subagent_to_session", src_id=r1.created_id, src_project=PROJECT)
    # Prior chain preserved + the new subagent->session hop appended.
    assert r2.lineage == [
        {"as": "session", "id": SID_A},
        {"as": "subagent", "id": r1.created_id},
        {"as": "subagent", "id": r1.created_id},
        {"as": "session", "id": r2.created_id},
    ]


def test_handoff_reflects_subagent_born_source(fake_claude):
    """A session that was itself converted from a subagent gets the subagent-origin handoff."""
    fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    r1 = srv.convert_session(
        direction="session_to_subagent", src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    r2 = srv.convert_session(direction="subagent_to_session", src_id=r1.created_id, src_project=PROJECT)
    # Now convert that session BACK into a subagent — its source lineage ends on a
    # subagent hop, so the handoff phrases its origin as a subagent run.
    r3 = srv.convert_session(
        direction="session_to_subagent", src_id=r2.created_id, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    assert r3.suggested_handoff is not None
    assert "was a subagent run inside Claude Code session" in r3.suggested_handoff


# =============================================================================
# Resolution / ambiguity
# =============================================================================


def test_prefix_ambiguity_raises(fake_claude):
    # Two sessions sharing an 8-char prefix.
    s1 = "dddddddd-1111-2222-3333-444444444444"
    s2 = "dddddddd-9999-8888-7777-666666666666"
    fake_claude.write_session(s1, _simple_session_lines())
    fake_claude.write_session(s2, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    with pytest.raises(ToolError) as exc:
        srv.convert_session(
            direction="session_to_subagent", src_id="dddddddd", src_project=PROJECT, dest_parent_session=SID_PARENT,
        )
    assert "ambiguous" in str(exc.value).lower()


def test_cross_project_src_resolution(fake_claude):
    """src_project omitted → source located across all projects."""
    other = "/Users/test/projects/other"
    fake_claude.write_session(SID_A, _simple_session_lines(), project_path=other)
    fake_claude.write_session(SID_PARENT, _simple_session_lines(), project_path=PROJECT)

    resp = srv.convert_session(
        direction="session_to_subagent", src_id=SID_A, dest_parent_session=SID_PARENT,
    )
    # The source was found in `other` even though the parent is in PROJECT.
    parent_dir = fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents"
    assert (parent_dir / f"agent-{resp.created_id}.jsonl").exists()


# =============================================================================
# delete_conversions
# =============================================================================


def test_delete_conversions_removes_tagged_subagent(fake_claude):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_agent = "a" + "2" * 16
    path = _lay_down_subagent(fake_claude, conv_agent, is_conversion_artifact=True)
    assert path.exists()

    resp = srv.delete_conversions(ids=[conv_agent])
    assert len(resp.deleted) == 1
    assert resp.deleted[0].kind == "subagent"
    assert not resp.refused
    assert not path.exists()
    assert not path.with_suffix(".meta.json").exists()


def test_delete_conversions_refuses_subagent_without_provenance_line(fake_claude):
    """No provenance line (meta is no longer a signal) → refused as non-conversion."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    real_agent = "a" + "3" * 16
    path = _lay_down_subagent(fake_claude, real_agent, is_conversion_artifact=False)

    resp = srv.delete_conversions(ids=[real_agent])
    assert not resp.deleted
    assert len(resp.refused) == 1
    assert "not a conversion artifact" in resp.refused[0].reason
    assert "x-converter-provenance" in resp.refused[0].reason
    assert path.exists()  # untouched


def test_delete_conversions_refuses_real_session(fake_claude):
    """A real (untagged) session resolves as a session → refused with the session message."""
    fake_claude.write_session(SID_A, _simple_session_lines())
    resp = srv.delete_conversions(ids=[SID_A])
    assert not resp.deleted
    assert len(resp.refused) == 1
    assert "converted sessions are for humans to manage" in resp.refused[0].reason
    assert (fake_claude.project_dir(PROJECT) / f"{SID_A}.jsonl").exists()


def test_delete_conversions_refuses_converted_session_even_when_tagged(fake_claude):
    """Sessions are NEVER deleted by this tool — even a genuine, provenance-tagged one."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    _lay_down_subagent(fake_claude, AGENT_ID, is_conversion_artifact=True)
    r = srv.convert_session(direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT)
    out_path = fake_claude.project_dir(PROJECT) / f"{r.created_id}.jsonl"
    assert out_path.exists()
    # Sanity: it really IS a tagged conversion (the file carries a provenance line).
    from cc_explorer.conversion import is_conversion_artifact as _is_conv
    assert _is_conv(out_path)

    resp = srv.delete_conversions(ids=[r.created_id])
    assert not resp.deleted
    assert len(resp.refused) == 1
    assert "converted sessions are for humans to manage" in resp.refused[0].reason
    assert out_path.exists()  # untouched


def test_delete_conversions_refuses_grown_subagent(fake_claude):
    """Growth guard: a tagged subagent that grew past lines_at_creation is refused."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    grown = "a" + "9" * 16
    path = _lay_down_subagent(fake_claude, grown, is_conversion_artifact=True, extra_lines=3)

    resp = srv.delete_conversions(ids=[grown])
    assert not resp.deleted
    assert len(resp.refused) == 1
    assert "resumed or built upon" in resp.refused[0].reason
    assert path.exists()  # untouched — someone may depend on it


def test_delete_conversions_deletes_subagent_when_growth_guard_passes(fake_claude):
    """Provenance line present AND no growth → deleted."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_agent = "a" + "2" * 16
    path = _lay_down_subagent(fake_claude, conv_agent, is_conversion_artifact=True, extra_lines=0)
    assert path.exists()

    resp = srv.delete_conversions(ids=[conv_agent])
    assert len(resp.deleted) == 1
    assert resp.deleted[0].kind == "subagent"
    assert not resp.refused
    assert not path.exists()
    assert not path.with_suffix(".meta.json").exists()


def test_delete_conversions_unknown_id_refused(fake_claude):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    resp = srv.delete_conversions(ids=["nonexistent-id-xyz"])
    assert not resp.deleted
    assert len(resp.refused) == 1
    assert "no session or subagent" in resp.refused[0].reason


def test_delete_conversions_sweep_calling_session(fake_claude, monkeypatch):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_a = "a" + "4" * 16
    real_a = "a" + "5" * 16
    grown_a = "a" + "6" * 16
    conv_path = _lay_down_subagent(fake_claude, conv_a, is_conversion_artifact=True)
    real_path = _lay_down_subagent(fake_claude, real_a, is_conversion_artifact=False)
    grown_path = _lay_down_subagent(fake_claude, grown_a, is_conversion_artifact=True, extra_lines=2)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", SID_PARENT)

    resp = srv.delete_conversions()  # ids omitted → sweep
    deleted_ids = {d.id for d in resp.deleted}
    refused_ids = {r.id for r in resp.refused}
    assert conv_a in deleted_ids
    assert real_a not in deleted_ids  # untagged: never touched, not even reported
    assert grown_a in refused_ids  # tagged but grown → reported as skipped
    assert not conv_path.exists()
    assert real_path.exists()  # non-conversion agent untouched
    assert grown_path.exists()  # grown conversion untouched
    # The growth-guard skip carries the resume/build-up reason.
    grown_reason = next(r.reason for r in resp.refused if r.id == grown_a)
    assert "resumed or built upon" in grown_reason


# =============================================================================
# Search corpus exclusion + list_session_agents labeling
# =============================================================================


def test_conversion_agent_excluded_from_search_corpus(fake_claude):
    session_path = fake_claude.write_session(SID_PARENT, [_user("u-main-0000-0000-0000-00000000m001", "MAINONLY")])
    # A conversion subagent containing a unique token.
    conv_agent = "a" + "6" * 16
    _lay_down_subagent(
        fake_claude, conv_agent, is_conversion_artifact=True,
        lines=[_user("u-conv-0000-0000-0000-00000000c001", "SECRETTOKEN", agentId=conv_agent, isSidechain=True)],
    )
    # A normal subagent containing a different unique token.
    real_agent = "a" + "7" * 16
    _lay_down_subagent(
        fake_claude, real_agent, is_conversion_artifact=False,
        lines=[_user("u-real-0000-0000-0000-00000000r001", "VISIBLETOKEN", agentId=real_agent, isSidechain=True)],
    )

    session = SessionInfo(
        session_id=PrefixId(SID_PARENT), path=session_path, title="t",
        first_timestamp=None, message_count=1, project_path=PROJECT,
    )

    # session_sources excludes the conversion agent's file.
    src_paths = {s.path for s in session_sources(session)}
    assert _lay_down_subagent.__name__  # sanity
    assert (fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents" / f"agent-{real_agent}.jsonl") in src_paths
    assert (fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents" / f"agent-{conv_agent}.jsonl") not in src_paths

    # Triage: the conversion token is NOT found, the normal one IS.
    secret = triage_multi([session], ["SECRETTOKEN"])
    assert secret[0][1] == []  # no hits
    visible = triage_multi([session], ["VISIBLETOKEN"])
    assert len(visible[0][1]) == 1


def test_list_session_agents_shows_conversion_labeled(fake_claude):
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_agent = "a" + "8" * 16
    _lay_down_subagent(fake_claude, conv_agent, is_conversion_artifact=True)

    resp = srv.list_session_agents(session=SID_PARENT, projects=[PROJECT])
    ids = {a.agent_id.full: a for a in resp.agents}
    assert conv_agent in ids
    assert ids[conv_agent].is_conversion_artifact is True


# =============================================================================
# Fix 1: Trim safety — tool_result user turn never trimmed as noise
# =============================================================================


def _tool_use_assistant(uuid: str, tool_use_id: str, *, parent: str | None = None) -> dict:
    """An assistant turn ending with a tool_use block (e.g. a pending Bash call)."""
    line = {
        "type": "assistant",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": TS,
        "sessionId": "SID",
        "version": "2.1.175",
        "gitBranch": "main",
        "message": {
            "id": "msg_" + uuid,
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-4-8",
            "content": [
                {"type": "text", "text": "running tool"},
                {"type": "tool_use", "id": tool_use_id, "name": "Bash", "input": {"command": "ls"}},
            ],
        },
    }
    return line


def _tool_result_user(uuid: str, tool_use_id: str, *, parent: str | None = None) -> dict:
    """A user turn carrying ONLY a tool_result block (no text blocks)."""
    return {
        "type": "user",
        "uuid": uuid,
        "parentUuid": parent,
        "timestamp": TS,
        "sessionId": "SID",
        "version": "2.1.175",
        "gitBranch": "main",
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_use_id,
                    "content": [{"type": "text", "text": "file.txt"}],
                }
            ],
        },
    }


def test_trim_safety_tool_result_user_not_trimmed(fake_claude):
    """A session ending assistant(tool_use)+user(tool_result) must keep BOTH lines.

    Fix 1: a user turn whose content list contains a tool_result block must never
    be treated as trailing noise, even though its text content is empty. Trimming
    it would leave a dangling assistant tool_use that the API rejects on resume.
    """
    tool_id = "toolu_test0000000000000000"
    lines = _simple_session_lines() + [
        _tool_use_assistant(
            "a-tool-0000-0000-0000-000000000aa1",
            tool_id,
            parent="u1111111-0000-0000-0000-000000000003",
        ),
        _tool_result_user(
            "u-tool-0000-0000-0000-000000000ur1",
            tool_id,
            parent="a-tool-0000-0000-0000-000000000aa1",
        ),
    ]
    fake_claude.write_session(SID_A, lines)
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    # Neither the tool_use assistant turn nor the tool_result user turn should be trimmed.
    assert resp.trimmed_trailing == 0
    # The copied transcript includes the tool_use and tool_result turns.
    agent_file = (
        fake_claude.project_dir(PROJECT) / SID_PARENT / "subagents" / f"agent-{resp.created_id}.jsonl"
    )
    body = _convo_only(_read_jsonl(agent_file))
    types = [l["type"] for l in body]
    # Ends with user (tool_result turn) — not trimmed.
    assert types[-1] == "user"
    # That user turn's content has a tool_result block.
    last_content = body[-1]["message"]["content"]
    assert any(b.get("type") == "tool_result" for b in last_content)


# =============================================================================
# Fix 2: Broken-links fallback (parentUuid walk can't complete)
# =============================================================================


def test_active_thread_broken_links_fallback(fake_claude):
    """When parentUuid links are broken (missing parent), fall back to file order.

    A session where the last line's parent uuid doesn't exist in the corpus
    causes the walk to fail and fall back to including all lines (file order).
    """
    # Build lines where the last line's parentUuid points to a non-existent uuid.
    lines = [
        _user("u0000000-1111-0000-0000-000000000001", "FIRST"),
        _assistant("a0000000-1111-0000-0000-000000000002", "SECOND", parent="u0000000-1111-0000-0000-000000000001"),
        _user("u0000000-1111-0000-0000-000000000003", "THIRD", parent="a0000000-1111-0000-0000-000000000002"),
        # This assistant points to a parent that doesn't exist → broken link.
        _assistant("a0000000-1111-0000-0000-000000000004", "FOURTH",
                   parent="nonexistent-0000-0000-0000-000000000000"),
    ]
    fake_claude.write_session(SID_A, lines)
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    # Fallback: all 4 lines kept (file order), none dropped.
    assert resp.turns == 4
    assert resp.dropped_branches == 0


# =============================================================================
# Fix 3: Source-project provenance stamped correctly for subagent_to_session
# =============================================================================


def test_subagent_to_session_provenance_from_project_is_holding_project(fake_claude):
    """Provenance from.project == holding project; response.project == dest project.

    Fix 3: before this fix, src_project_path was incorrectly set to dest_proj_path,
    so provenance/from.project pointed at the destination instead of the source.
    """
    dest = "/Users/test/projects/dest"
    # Seed the dest project so it has a dir to write into.
    fake_claude.write_session("eeeeeeee-1111-2222-3333-444444444444", _simple_session_lines(), project_path=dest)
    fake_claude.write_session(SID_PARENT, _simple_session_lines(), project_path=PROJECT)
    _lay_down_subagent(fake_claude, AGENT_ID)

    resp = srv.convert_session(
        direction="subagent_to_session",
        src_id=AGENT_ID,
        src_project=PROJECT,
        dest_project=dest,
    )
    # response.project is the DESTINATION.
    assert resp.project == dest

    # The written file's provenance/from.project is the HOLDING (source) project.
    out_path = fake_claude.project_dir(dest) / f"{resp.created_id}.jsonl"
    assert out_path.exists()
    lines = _read_jsonl(out_path)
    prov_lines = _provenance_lines(lines)
    assert len(prov_lines) == 1
    from_project = prov_lines[0]["x_converter"]["from"]["project"]
    assert from_project == PROJECT  # holding project, not dest
    assert from_project != dest


# =============================================================================
# Fix 4: Session conversion artifacts excluded from search but visible in listing
# =============================================================================


def test_session_conversion_artifact_excluded_from_search(fake_claude):
    """A subagent_to_session artifact is excluded from triage/search results.

    Fix 4: session conversion artifacts (session files carrying x-converter-provenance)
    should be skipped in search/triage, just like agent-shaped conversion artifacts.
    """
    # A normal session with a unique token.
    normal_sid = "cccccccc-aaaa-0000-0000-000000000001"
    fake_claude.write_session(
        normal_sid,
        [_user("u-norm-0000-0000-0000-0000000n0001", "NORMALTOKEN")],
    )
    # A subagent_to_session conversion artifact: has x-converter-provenance header.
    conv_sid = "cccccccc-bbbb-0000-0000-000000000002"
    conv_lines = [
        {"type": "mode", "mode": "normal", "sessionId": conv_sid},
        {"type": "permission-mode", "permissionMode": "default", "sessionId": conv_sid},
        {"type": "custom-title", "customTitle": "conv-session", "sessionId": conv_sid},
        {
            "type": "x-converter-provenance",
            "x_converter": {
                "tool": "convert_session",
                "v": 1,
                "from": {"kind": "subagent", "id": "x", "project": PROJECT},
                "converted_at": TS,
                "lines_at_creation": 6,
            },
            "sessionId": conv_sid,
            "agentId": None,
        },
        _user("u-conv-0000-0000-0000-0000000c0001", "SECRETTOKEN"),
        _assistant("a-conv-0000-0000-0000-0000000c0002", "reply"),
    ]
    for l in conv_lines:
        l.setdefault("cwd", PROJECT)
    d = fake_claude.project_dir(PROJECT)
    d.mkdir(parents=True, exist_ok=True)
    _write_jsonl(d / f"{conv_sid}.jsonl", conv_lines)

    sessions, _ = srv._load_all_sessions([PROJECT])

    # Triage: conversion artifact session not in search results.
    from cc_explorer.search import triage_multi
    secret = triage_multi(sessions, ["SECRETTOKEN"])
    assert secret[0][1] == []  # no hits from conversion artifact session
    normal = triage_multi(sessions, ["NORMALTOKEN"])
    assert len(normal[0][1]) == 1  # normal session found


def test_session_conversion_artifact_labeled_in_listing(fake_claude):
    """A subagent_to_session artifact appears in list_project_sessions with is_conversion_artifact=true."""
    # Normal session (different prefix from conversion session).
    normal_sid = "11111111-aaaa-0000-0000-000000000001"
    fake_claude.write_session(normal_sid, _simple_session_lines())

    # A subagent_to_session conversion artifact.
    conv_sid = "22222222-bbbb-0000-0000-000000000002"
    conv_lines = [
        {"type": "mode", "mode": "normal", "sessionId": conv_sid},
        {"type": "permission-mode", "permissionMode": "default", "sessionId": conv_sid},
        {"type": "custom-title", "customTitle": "labeled-conv", "sessionId": conv_sid},
        {
            "type": "x-converter-provenance",
            "x_converter": {
                "tool": "convert_session",
                "v": 1,
                "from": {"kind": "subagent", "id": "x", "project": PROJECT},
                "converted_at": TS,
                "lines_at_creation": 6,
            },
            "sessionId": conv_sid,
            "agentId": None,
        },
        _user("u-lbl-0000-0000-0000-0000000l0001", "hello"),
        _assistant("a-lbl-0000-0000-0000-0000000l0002", "world"),
    ]
    for l in conv_lines:
        l.setdefault("cwd", PROJECT)
    d = fake_claude.project_dir(PROJECT)
    d.mkdir(parents=True, exist_ok=True)
    _write_jsonl(d / f"{conv_sid}.jsonl", conv_lines)

    resp = srv.list_project_sessions(projects=[PROJECT], min_messages=1)
    by_id = {str(s.session)[:8]: s for s in resp.sessions}
    # Both sessions are listed.
    assert conv_sid[:8] in by_id
    assert normal_sid[:8] in by_id
    # The artifact is labeled.
    assert by_id[conv_sid[:8]].is_conversion_artifact is True
    assert by_id[normal_sid[:8]].is_conversion_artifact is None


# =============================================================================
# Fix 5: Delete resolution hardening
# =============================================================================


def test_delete_conversions_short_id_rejected(fake_claude):
    """An id shorter than 6 chars is rejected with a clear error before any lookup."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    with pytest.raises(ToolError) as exc:
        srv.delete_conversions(ids=["ab"])
    assert "too short" in str(exc.value).lower()


def test_delete_conversions_ambiguous_prefix_raises(fake_claude):
    """An ambiguous prefix (matches multiple distinct artifacts) raises ToolError naming candidates."""
    # Two sessions whose ids share a prefix.
    s1 = "eeeeeeee-1111-2222-3333-444444444441"
    s2 = "eeeeeeee-1111-2222-3333-444444444442"
    fake_claude.write_session(s1, _simple_session_lines())
    fake_claude.write_session(s2, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    with pytest.raises(ToolError) as exc:
        srv.delete_conversions(ids=["eeeeeeee"])
    assert "ambiguous" in str(exc.value).lower()


# =============================================================================
# Fix 6: Sweep error swallow
# =============================================================================


def test_delete_conversions_sweep_raises_on_unresolvable_session(fake_claude, monkeypatch):
    """Sweep with an unresolvable calling session raises ToolError (not empty success).

    Fix 6: the old behavior silently returned empty deleted/refused on resolution
    failure, making it indistinguishable from 'nothing to clean'.
    """
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    # Point the calling session to something that doesn't exist.
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "nonexistent-0000-0000-0000-000000000000")
    with pytest.raises(ToolError) as exc:
        srv.delete_conversions()
    assert "sweep failed" in str(exc.value).lower() or "could not resolve" in str(exc.value).lower()


# =============================================================================
# Fix 8: Population semantics — conversion artifacts not counted as dispatched runs
# =============================================================================


def test_agents_present_excludes_conversion_artifacts(fake_claude):
    """A session whose ONLY agent file is a conversion artifact has agents_present==0."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_agent = "a" + "c" * 16
    _lay_down_subagent(fake_claude, conv_agent, is_conversion_artifact=True)

    sessions, _ = srv._load_all_sessions([PROJECT], with_agents_present=True)
    parent_session = next(s for s in sessions if str(s.session_id) == SID_PARENT[:8] or s.session_id.full == SID_PARENT)
    assert parent_session.agents_present == 0


def test_min_agents_filter_excludes_conversion_only_sessions(fake_claude):
    """A session with only a conversion artifact is absent from min_agents=1 listings.

    Since agents_present==0 for that session, min_agents=1 filters it out.
    With no other sessions matching, list_project_sessions raises (nothing to return).
    """
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_agent = "a" + "d" * 16
    _lay_down_subagent(fake_claude, conv_agent, is_conversion_artifact=True)

    # With min_agents=1, the session (agents_present==0) doesn't match — nothing returned.
    with pytest.raises(ToolError):
        srv.list_project_sessions(projects=[PROJECT], min_agents=1, min_messages=1)

    # With min_agents=0, the session is present (no filtering on agents).
    resp = srv.list_project_sessions(projects=[PROJECT], min_agents=0, min_messages=1)
    session_ids = {s.session.full for s in resp.sessions}
    assert SID_PARENT in session_ids


def test_nested_agents_excludes_conversion_artifacts(fake_claude):
    """convert_session's nested_agents count excludes conversion artifacts on the source."""
    fake_claude.write_session(SID_A, _simple_session_lines())
    fake_claude.write_session(SID_PARENT, _simple_session_lines())

    # Put a conversion artifact under SID_A (not a real dispatched run).
    conv_nested = "a" + "e" * 16
    sub_dir = fake_claude.project_dir(PROJECT) / SID_A / "subagents"
    body = [
        _user("u-n-0000-0000-0000-0000000n0001", "n", agentId=conv_nested, isSidechain=True),
    ]
    prov = _fake_provenance_line(conv_nested, lines_at_creation=2, kind="session")
    _write_jsonl(sub_dir / f"agent-{conv_nested}.jsonl", [prov] + body)
    (sub_dir / f"agent-{conv_nested}.meta.json").write_text(
        json.dumps({"agentType": "general-purpose", "description": "d", "toolUseId": "toolu_n"})
    )

    resp = srv.convert_session(
        direction="session_to_subagent",
        src_id=SID_A, src_project=PROJECT, dest_parent_session=SID_PARENT,
    )
    # Conversion artifact is not counted as a nested agent.
    assert resp.nested_agents is None or resp.nested_agents == 0


# =============================================================================
# rewind_transcript
# =============================================================================
#
# Rewind truncates a conversion artifact IN PLACE at a chosen turn. Turn ids
# passed to the tool go through _validate_turn_id, so rewind fixtures use
# all-hex uuids (the synthesized session builders above use 'u'/'a'-prefixed
# ids that are fine on the wire but not valid turn args).

# Hex body uuids (valid as `turn` args).
RW_T1 = "aaaa0001-0000-0000-0000-000000000001"  # user
RW_A1 = "aaaa0001-0000-0000-0000-000000000002"  # assistant
RW_T2 = "aaaa0001-0000-0000-0000-000000000003"  # user
RW_A2 = "aaaa0001-0000-0000-0000-000000000004"  # assistant


def _rw_body(agent_id: str) -> list[dict]:
    """A 4-turn subagent body (user/assistant x2) with hex uuids."""
    return [
        _user(RW_T1, "ONE", agentId=agent_id, isSidechain=True),
        _assistant(RW_A1, "TWO", parent=RW_T1, agentId=agent_id, isSidechain=True),
        _user(RW_T2, "THREE", parent=RW_A1, agentId=agent_id, isSidechain=True),
        _assistant(RW_A2, "FOUR", parent=RW_T2, agentId=agent_id, isSidechain=True),
    ]


def _assistant_tool_use(uuid: str, tool: str, *, parent=None, agent_id=None) -> dict:
    line = _assistant(uuid, "", parent=parent)
    line["message"]["content"] = [{"type": "tool_use", "id": "toolu_x", "name": tool, "input": {}}]
    if agent_id:
        line["agentId"] = agent_id
        line["isSidechain"] = True
    return line


def _user_tool_result(uuid: str, *, parent=None, agent_id=None) -> dict:
    line = _user(uuid, "", parent=parent)
    line["message"]["content"] = [{"type": "tool_result", "tool_use_id": "toolu_x", "content": "ok"}]
    if agent_id:
        line["agentId"] = agent_id
        line["isSidechain"] = True
    return line


RW_AGENT = "a" + "7" * 16


def _lay_rw_subagent(fake_claude, agent_id=RW_AGENT, *, body=None, conv=True) -> Path:
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    return _lay_down_subagent(
        fake_claude, agent_id, is_conversion_artifact=conv,
        lines=body if body is not None else _rw_body(agent_id),
    )


def test_rewind_subagent_cut_after(fake_claude):
    path = _lay_rw_subagent(fake_claude)

    resp = srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")

    assert resp.operation == "rewind"
    assert resp.kind == "subagent"
    assert resp.artifact_id == RW_AGENT
    assert resp.invocation == f'SendMessage(to: "{RW_AGENT}")'
    assert resp.cut == "after"
    assert resp.target_turn == RW_A1
    assert resp.turns_before == 4
    assert resp.turns_after == 2  # ONE, TWO
    assert resp.removed_after_cut == 2
    assert resp.tail_state == "clean"  # ends on assistant TWO

    body = _convo_only(_read_jsonl(path))
    assert _convo_texts(body) == ["ONE", "TWO"]


def test_rewind_subagent_cut_before(fake_claude):
    """cut='before' a user prompt → the prompt and everything after is gone."""
    path = _lay_rw_subagent(fake_claude)

    resp = srv.rewind_transcript(src_id=RW_AGENT, turn=RW_T2, cut="before")

    assert resp.cut == "before"
    assert resp.turns_after == 2  # ONE, TWO — the THREE prompt is dropped
    assert resp.tail_state == "clean"
    body = _convo_only(_read_jsonl(path))
    assert _convo_texts(body) == ["ONE", "TWO"]


def test_rewind_restamps_lines_at_creation_and_marks_rewound(fake_claude):
    path = _lay_rw_subagent(fake_claude)
    srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")

    lines = _read_jsonl(path)
    prov = _provenance_lines(lines)[0]["x_converter"]
    # File is provenance line + 2 kept body lines == 3.
    assert prov["lines_at_creation"] == len(lines) == 3
    assert prov["rewound_to"] == RW_A1
    assert "rewound_at" in prov


def test_rewind_keeps_artifact_deletable(fake_claude):
    """After a rewind the re-stamp keeps the growth guard satisfied → still deletable."""
    from cc_explorer.conversion import growth_exceeded, is_conversion_artifact as _is_conv

    path = _lay_rw_subagent(fake_claude)
    srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")
    assert _is_conv(path)
    assert not growth_exceeded(path)

    resp = srv.delete_conversions(ids=[RW_AGENT])
    assert len(resp.deleted) == 1 and not resp.refused
    assert not path.exists()


def test_rewind_trims_dangling_tool_use(fake_claude):
    """Cutting at an assistant tool_use turn trims it off so resume stays valid."""
    body = [
        _user(RW_T1, "ONE", agentId=RW_AGENT, isSidechain=True),
        _assistant_tool_use(RW_A1, "Bash", parent=RW_T1, agent_id=RW_AGENT),
        _user_tool_result(RW_T2, parent=RW_A1, agent_id=RW_AGENT),
        _assistant(RW_A2, "DONE", parent=RW_T2, agentId=RW_AGENT, isSidechain=True),
    ]
    path = _lay_rw_subagent(fake_claude, body=body)

    resp = srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")
    # The cut keeps ONE + the tool_use assistant; the dangling assistant is trimmed.
    assert resp.trimmed_dangling_tool_use == 1
    assert resp.turns_after == 1  # just ONE
    assert resp.tail_state == "pending_user_input"
    body_out = _convo_only(_read_jsonl(path))
    assert _convo_texts(body_out) == ["ONE"]


def test_rewind_trims_trailing_noise(fake_claude):
    body = _rw_body(RW_AGENT) + [
        _user("aaaa0001-0000-0000-0000-000000000099", "[Request interrupted by user]",
              parent=RW_A2, agentId=RW_AGENT, isSidechain=True),
    ]
    path = _lay_rw_subagent(fake_claude, body=body)
    # Cut after the interrupt line → it should be trimmed as trailing noise.
    resp = srv.rewind_transcript(src_id=RW_AGENT, turn="aaaa0001-0000-0000-0000-000000000099", cut="after")
    assert resp.trimmed_trailing == 1
    assert resp.turns_after == 4
    assert resp.tail_state == "clean"


def test_rewind_refuses_non_conversion_subagent(fake_claude):
    """An untagged subagent (no provenance line) is refused untouched."""
    path = _lay_rw_subagent(fake_claude, conv=False)
    before = path.read_text()

    with pytest.raises(ToolError) as exc:
        srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")
    assert "not a conversion artifact" in str(exc.value)
    assert path.read_text() == before  # untouched


def test_rewind_refuses_real_session(fake_claude):
    """A real, untagged session is refused — we never truncate what we didn't write."""
    fake_claude.write_session(SID_A, _simple_session_lines())
    sess_path = fake_claude.project_dir(PROJECT) / f"{SID_A}.jsonl"
    before = sess_path.read_text()

    with pytest.raises(ToolError) as exc:
        srv.rewind_transcript(src_id=SID_A, turn="a1111111-0000-0000-0000-000000000002", cut="after")
    assert "not a conversion artifact" in str(exc.value)
    assert sess_path.read_text() == before


def test_rewind_allows_converted_session(fake_claude):
    """Unlike delete_conversions, a CONVERTED session is eligible for rewind."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    _lay_down_subagent(fake_claude, AGENT_ID, is_conversion_artifact=True, lines=_rw_body(AGENT_ID))
    r = srv.convert_session(direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT)
    out_path = fake_claude.project_dir(PROJECT) / f"{r.created_id}.jsonl"
    from cc_explorer.conversion import is_conversion_artifact as _is_conv
    assert _is_conv(out_path)

    resp = srv.rewind_transcript(src_id=r.created_id, turn=RW_A1, cut="after")
    assert resp.kind == "session"
    assert resp.invocation == f"claude -r {r.created_id}"
    assert resp.turns_after == 2
    # The session header (mode/permission-mode/custom-title) survives the rewrite.
    lines = _read_jsonl(out_path)
    assert lines[0]["type"] == "mode"
    assert any(l.get("type") == "custom-title" for l in lines[:4])
    assert _provenance_lines(lines)  # provenance preserved


def test_rewind_turn_not_found(fake_claude):
    _lay_rw_subagent(fake_claude)
    with pytest.raises(ToolError) as exc:
        srv.rewind_transcript(src_id=RW_AGENT, turn="deadbeef-0000-0000-0000-000000000000", cut="after")
    assert "not found" in str(exc.value)


def test_rewind_cut_before_first_turn_refused(fake_claude):
    """cut='before' the first turn would empty the transcript → refused."""
    path = _lay_rw_subagent(fake_claude)
    before = path.read_text()
    with pytest.raises(ToolError) as exc:
        srv.rewind_transcript(src_id=RW_AGENT, turn=RW_T1, cut="before")
    assert "discard the entire conversation" in str(exc.value)
    assert path.read_text() == before  # untouched on refusal


def test_rewind_short_id_rejected(fake_claude):
    _lay_rw_subagent(fake_claude)
    with pytest.raises(ToolError) as exc:
        srv.rewind_transcript(src_id="ab", turn=RW_A1, cut="after")
    assert "too short" in str(exc.value)


def test_rewind_relinearizes_kept_body(fake_claude):
    path = _lay_rw_subagent(fake_claude)
    srv.rewind_transcript(src_id=RW_AGENT, turn=RW_T2, cut="after")
    body = _convo_only(_read_jsonl(path))
    assert body[0]["parentUuid"] is None
    for prev, cur in zip(body, body[1:]):
        assert cur["parentUuid"] == prev["uuid"]


def test_rewind_follows_active_thread_not_file_order(fake_claude):
    """A resumed artifact with an abandoned edit-branch sibling: rewind keeps the
    target's ancestor lineage, never file-order siblings."""
    # ONE -> TWO(assistant). Then the user 'edited' TWO's follow-up: an abandoned
    # branch THREE-OLD and the live branch THREE-NEW, both children of TWO. We
    # rewind to FOUR, which descends from THREE-NEW. THREE-OLD must NOT appear.
    THREE_OLD = "aaaa0001-0000-0000-0000-0000000000a1"
    THREE_NEW = "aaaa0001-0000-0000-0000-0000000000a2"
    FOUR = "aaaa0001-0000-0000-0000-0000000000a3"
    body = [
        _user(RW_T1, "ONE", agentId=RW_AGENT, isSidechain=True),
        _assistant(RW_A1, "TWO", parent=RW_T1, agentId=RW_AGENT, isSidechain=True),
        _user(THREE_OLD, "THREE-OLD", parent=RW_A1, agentId=RW_AGENT, isSidechain=True),
        _user(THREE_NEW, "THREE-NEW", parent=RW_A1, agentId=RW_AGENT, isSidechain=True),
        _assistant(FOUR, "FOUR", parent=THREE_NEW, agentId=RW_AGENT, isSidechain=True),
    ]
    path = _lay_rw_subagent(fake_claude, body=body)

    resp = srv.rewind_transcript(src_id=RW_AGENT, turn=FOUR, cut="after")
    texts = _convo_texts(_convo_only(_read_jsonl(path)))
    assert texts == ["ONE", "TWO", "THREE-NEW", "FOUR"]  # THREE-OLD dropped
    assert "THREE-OLD" not in texts
    assert resp.turns_after == 4
    # The 5 body turns minus the 4 kept = 1 removed (the abandoned sibling).
    assert resp.removed_after_cut == 1


def test_rewind_duplicate_target_uuid_refused(fake_claude):
    """If the target uuid repeats in the transcript, rewind refuses rather than
    silently truncating at the first occurrence."""
    dup = RW_A1
    body = [
        _user(RW_T1, "ONE", agentId=RW_AGENT, isSidechain=True),
        _assistant(dup, "TWO", parent=RW_T1, agentId=RW_AGENT, isSidechain=True),
        _user(RW_T2, "THREE", parent=dup, agentId=RW_AGENT, isSidechain=True),
        _assistant(dup, "FOUR", parent=RW_T2, agentId=RW_AGENT, isSidechain=True),  # same uuid
    ]
    path = _lay_rw_subagent(fake_claude, body=body)
    before = path.read_text()
    with pytest.raises(ToolError) as exc:
        srv.rewind_transcript(src_id=RW_AGENT, turn=dup, cut="after")
    assert "more than once" in str(exc.value)
    assert path.read_text() == before  # untouched


def test_rewind_atomic_no_tempfile_left_behind(fake_claude):
    """A successful rewind leaves no .rewind-tmp sidecar."""
    path = _lay_rw_subagent(fake_claude)
    srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")
    tmp = path.with_name(path.name + ".rewind-tmp")
    assert not tmp.exists()
    assert path.exists()


def test_rewind_atomic_write_preserves_original_on_failure(fake_claude, monkeypatch):
    """If the write fails mid-way, the original transcript is left intact (the
    atomic temp+replace guarantees no half-written file)."""
    import cc_explorer.conversion as conv

    path = _lay_rw_subagent(fake_claude)
    before = path.read_text()

    real_replace = conv.os.replace

    def boom(src, dst):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(conv.os, "replace", boom)
    with pytest.raises(OSError):
        srv.rewind_transcript(src_id=RW_AGENT, turn=RW_A1, cut="after")

    # Original untouched, temp cleaned up.
    assert path.read_text() == before
    assert not path.with_name(path.name + ".rewind-tmp").exists()
    monkeypatch.setattr(conv.os, "replace", real_replace)


# =============================================================================
# Conversion reaper — lifespan-driven GC of pristine, cold artifacts
# =============================================================================
#
# The reaper deletes ONLY pristine (never-resumed) conversion subagents older
# than the age threshold. Two invariants protect everything else: the growth
# guard (resumed forks carry unique history) and the age gate (young forks may
# still be resumed). Converted SESSIONS are structurally out of the glob.


def _fresh_iso() -> str:
    """An ISO timestamp at ~now — well within the reaper's age threshold."""
    return datetime.now(timezone.utc).isoformat()


def test_reaper_deletes_pristine_cold(fake_claude):
    """A pristine conversion artifact older than the threshold is reaped (+meta)."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    cold = "a" + "1" * 16
    path = _lay_down_subagent(fake_claude, cold, is_conversion_artifact=True)  # converted_at=TS, months old
    assert path.exists()

    reaped = srv._reap_stale_conversions(srv._reap_age_seconds())

    assert path in reaped
    assert not path.exists()
    assert not path.with_suffix(".meta.json").exists()


def test_reaper_spares_grown(fake_claude):
    """A grown (resumed) conversion artifact is never reaped, however old."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    grown = "a" + "2" * 16
    path = _lay_down_subagent(fake_claude, grown, is_conversion_artifact=True, extra_lines=3)
    assert path.exists()

    reaped = srv._reap_stale_conversions(srv._reap_age_seconds())

    assert path not in reaped
    assert path.exists()  # unique resumed history — protected by the growth guard


def test_reaper_spares_young_pristine(fake_claude):
    """A pristine artifact younger than the threshold may still be resumed — spared."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    young = "a" + "3" * 16
    path = _lay_down_subagent(
        fake_claude, young, is_conversion_artifact=True, converted_at=_fresh_iso()
    )
    assert path.exists()

    reaped = srv._reap_stale_conversions(srv._reap_age_seconds())

    assert path not in reaped
    assert path.exists()


def test_reaper_spares_non_conversion_subagent(fake_claude):
    """A real dispatched subagent (no provenance line) is never touched."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    real = "a" + "4" * 16
    path = _lay_down_subagent(fake_claude, real, is_conversion_artifact=False)
    assert path.exists()

    srv._reap_stale_conversions(srv._reap_age_seconds())

    assert path.exists()


def test_reaper_spares_converted_session(fake_claude):
    """Session-shaped conversion artifacts are structurally outside the reaper's glob."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    _lay_down_subagent(fake_claude, AGENT_ID, is_conversion_artifact=True)
    r = srv.convert_session(direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT)
    session_path = fake_claude.project_dir(PROJECT) / f"{r.created_id}.jsonl"
    assert session_path.exists()

    srv._reap_stale_conversions(srv._reap_age_seconds())

    assert session_path.exists()  # converted sessions are for humans to manage


def test_reaper_disabled_by_env(fake_claude, monkeypatch):
    """CC_EXPLORER_REAP=0 disables all reaping."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    cold = "a" + "5" * 16
    path = _lay_down_subagent(fake_claude, cold, is_conversion_artifact=True)
    monkeypatch.setenv("CC_EXPLORER_REAP", "0")

    srv._run_reaper("startup")  # honors the env switch

    assert path.exists()


def test_reaper_age_threshold_env_override(fake_claude, monkeypatch):
    """A huge CC_EXPLORER_REAP_AGE_HOURS spares an otherwise-cold artifact."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    cold = "a" + "6" * 16
    path = _lay_down_subagent(fake_claude, cold, is_conversion_artifact=True)
    monkeypatch.setenv("CC_EXPLORER_REAP_AGE_HOURS", "1000000")

    srv._run_reaper("startup")

    assert path.exists()  # threshold not met


def test_reaper_lifespan_runs_on_startup(fake_claude):
    """The wired FastMCP lifespan actually reaps on entry (the startup backstop)."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    cold = "a" + "7" * 16
    path = _lay_down_subagent(fake_claude, cold, is_conversion_artifact=True)
    assert path.exists()

    async def _drive():
        async with srv._conversion_reaper_lifespan(srv.mcp):
            # Startup swept it before the server began serving.
            assert not path.exists()

    asyncio.run(_drive())
    assert not path.exists()


def test_reaper_age_zero_or_negative_falls_back_to_default(fake_claude, monkeypatch):
    """CC_EXPLORER_REAP_AGE_HOURS<=0 must NOT arm an immediate sweep of fresh forks."""
    monkeypatch.setenv("CC_EXPLORER_REAP_AGE_HOURS", "0")
    assert srv._reap_age_seconds() == srv._REAP_DEFAULT_AGE_HOURS * 3600.0
    monkeypatch.setenv("CC_EXPLORER_REAP_AGE_HOURS", "-5")
    assert srv._reap_age_seconds() == srv._REAP_DEFAULT_AGE_HOURS * 3600.0

    # Behaviorally: with the threshold pinned to the default, a freshly-written
    # pristine fork is spared (the old `hours >= 0` bug would have reaped it).
    monkeypatch.setenv("CC_EXPLORER_REAP_AGE_HOURS", "0")
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    young = "a" + "8" * 16
    path = _lay_down_subagent(
        fake_claude, young, is_conversion_artifact=True, converted_at=_fresh_iso()
    )
    srv._run_reaper("startup")
    assert path.exists()
