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

import json
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


def _fake_provenance_line(agent_id: str, *, lines_at_creation: int, kind: str = "session") -> dict:
    """A shape-valid x-converter-provenance line for synthesizing tagged artifacts."""
    return {
        "type": PROVENANCE_TYPE,
        "x_converter": {
            "tool": "convert_session",
            "v": 1,
            "from": {"kind": kind, "id": "x", "project": PROJECT},
            "converted_at": TS,
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
    # All five turns preserved in file order (siblings linearized, not dropped).
    assert len(out) == 5


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
    is_conversion=False,
    lines=None,
    extra_lines=0,
) -> Path:
    """Write a subagent transcript (+meta) under SID_PARENT's subagents dir.

    When `is_conversion` is True the file is stamped with a real x-converter-
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
    if is_conversion:
        # lines_at_creation = provenance line + body (what we write now).
        created = 1 + len(body)
        prov = _fake_provenance_line(agent_id, lines_at_creation=created)
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
    path = _lay_down_subagent(fake_claude, conv_agent, is_conversion=True)
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
    path = _lay_down_subagent(fake_claude, real_agent, is_conversion=False)

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
    _lay_down_subagent(fake_claude, AGENT_ID, is_conversion=True)
    r = srv.convert_session(direction="subagent_to_session", src_id=AGENT_ID, src_project=PROJECT)
    out_path = fake_claude.project_dir(PROJECT) / f"{r.created_id}.jsonl"
    assert out_path.exists()
    # Sanity: it really IS a tagged conversion (the file carries a provenance line).
    from cc_explorer.conversion import is_conversion as _is_conv
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
    path = _lay_down_subagent(fake_claude, grown, is_conversion=True, extra_lines=3)

    resp = srv.delete_conversions(ids=[grown])
    assert not resp.deleted
    assert len(resp.refused) == 1
    assert "resumed or built upon" in resp.refused[0].reason
    assert path.exists()  # untouched — someone may depend on it


def test_delete_conversions_deletes_subagent_when_growth_guard_passes(fake_claude):
    """Provenance line present AND no growth → deleted."""
    fake_claude.write_session(SID_PARENT, _simple_session_lines())
    conv_agent = "a" + "2" * 16
    path = _lay_down_subagent(fake_claude, conv_agent, is_conversion=True, extra_lines=0)
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
    conv_path = _lay_down_subagent(fake_claude, conv_a, is_conversion=True)
    real_path = _lay_down_subagent(fake_claude, real_a, is_conversion=False)
    grown_path = _lay_down_subagent(fake_claude, grown_a, is_conversion=True, extra_lines=2)
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
        fake_claude, conv_agent, is_conversion=True,
        lines=[_user("u-conv-0000-0000-0000-00000000c001", "SECRETTOKEN", agentId=conv_agent, isSidechain=True)],
    )
    # A normal subagent containing a different unique token.
    real_agent = "a" + "7" * 16
    _lay_down_subagent(
        fake_claude, real_agent, is_conversion=False,
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
    _lay_down_subagent(fake_claude, conv_agent, is_conversion=True)

    resp = srv.list_session_agents(session=SID_PARENT, projects=[PROJECT])
    ids = {a.agent_id.full: a for a in resp.agents}
    assert conv_agent in ids
    assert ids[conv_agent].is_conversion is True
