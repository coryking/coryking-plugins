"""Session <-> subagent transcript conversion (always a COPY).

This module reshapes Claude Code transcripts between two on-disk shapes:

  - a top-level *session* transcript at ``<projectDir>/<sessionId>.jsonl``
  - a *subagent* transcript at
    ``<projectDir>/<parentSessionId>/subagents/agent-<agentId>.jsonl`` with a
    sibling ``agent-<agentId>.meta.json``.

The harness resolves a resume target by scanning these files on disk, so a
correctly-shaped file IS a resumable agent (or session). Conversion never
modifies, moves, or deletes the source — it reads the source transcript and
writes a fresh copy in the other shape, stamping provenance keys
(``_converted_from``, ``_lineage``) and a dedicated ``x-converter-provenance``
line at the top of the written file so the artifact is identifiable and
removable later (see delete_conversions).

The ``x-converter-provenance`` line is the ONLY durable trust surface. A live
durability probe showed the harness REWRITES an agent's ``meta.json`` on
SendMessage-resume, preserving only ``agentType``/``description``/``toolUseId``
and silently dropping any unknown keys — so a provenance key written into
``meta.json`` vanishes on first resume. A custom own-type line in the jsonl body
survives untouched. We therefore put the full sentinel (including
``lines_at_creation``) on that line and key every downstream decision
(search-corpus exclusion, agent labeling, deletion eligibility) off it.
``meta.json`` carries only the standard three keys, with origin encoded in the
human-readable ``description`` string.

We operate on RAW JSONL dicts, not the typed `TranscriptEntry` models: the
harness tolerates (and we must preserve) unknown envelope fields, and we add our
own provenance keys that the typed models would drop. The typed layer is for
reading; this is surgery on the wire format.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Conversation line types we copy. Everything else (system, summary, progress,
# file-history-snapshot, queue-operation, mode/permission/custom-title headers)
# is dropped from the body — a subagent/session transcript is a linear chain of
# user/assistant turns; headers are re-synthesized where the target needs them.
_CONVO_TYPES = ("user", "assistant")

# The own-type provenance line stamped at the top of every conversion. This is
# the single durable trust surface (meta.json is rewritten on resume; see module
# docstring). `_PROVENANCE_VERSION` is the sentinel schema version.
_PROVENANCE_TYPE = "x-converter-provenance"
_PROVENANCE_VERSION = 1
_CONVERTER_TOOL = "convert_session"

# Keys stripped when copying a SESSION into a SUBAGENT.
_STRIP_SESSION_TO_SUBAGENT = (
    "forkedFrom",
    "isMeta",
    "attributionAgent",
    "attributionSkill",
    "sourceToolAssistantUUID",
)

# Keys stripped when copying a SUBAGENT into a SESSION.
_STRIP_SUBAGENT_TO_SESSION = (
    "agentId",
    "attributionAgent",
    "attributionSkill",
    "sourceToolAssistantUUID",
)

# A trailing user turn matching any of these is conversational dead weight (an
# unanswered prompt, an interrupt sentinel, or bare command scaffolding) and is
# trimmed so the copy ends on a clean boundary.
_TRAILING_NOISE_MARKERS = ("[Request interrupted", "<local-command", "<command-name>")


def _new_agent_id() -> str:
    """Mint a harness-shaped agent id: 'a' + 16 hex chars."""
    return "a" + secrets.token_hex(8)


def _new_tool_use_id() -> str:
    """Mint a tool-use id shaped like the harness's: 'toolu_' + 24 hex chars."""
    return "toolu_" + secrets.token_hex(12)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _provenance_line(
    *,
    src_kind: str,
    src_id: str,
    src_project: str,
    session_id: Optional[str],
    agent_id: Optional[str],
    lines_at_creation: int,
) -> dict[str, Any]:
    """Build one x-converter-provenance line — the single durable trust surface.

    `lines_at_creation` is the TOTAL physical line count of the file as written,
    THIS provenance line included. `agent_id` is the new agent id for subagent
    conversions, None for session conversions; `session_id` is the parent (subagent
    conv) or the new session id (session conv).
    """
    return {
        "type": _PROVENANCE_TYPE,
        "x_converter": {
            "tool": _CONVERTER_TOOL,
            "v": _PROVENANCE_VERSION,
            "from": {"kind": src_kind, "id": src_id, "project": src_project},
            "converted_at": _now_iso(),
            "lines_at_creation": lines_at_creation,
        },
        "sessionId": session_id,
        "agentId": agent_id,
    }


def read_provenance(transcript_path: Path) -> Optional[dict[str, Any]]:
    """Return the validated `x_converter` sentinel for a conversion, else None.

    The provenance line is written at the top, so the fast path reads only the
    head of the file. We scan a tiny window (provenance is line 1 for subagent
    conversions, line 4 — right after the custom-title header — for session
    conversions, with slack for hand-edits) rather than the whole file.

    Shape-validates: the line must be an `x-converter-provenance` dict whose
    `x_converter` is a dict carrying a `from` dict and an int `lines_at_creation`.
    A line that merely names the type but is malformed returns None (it is not a
    trustworthy conversion marker).
    """
    _SCAN_LIMIT = 8  # provenance is line 1 or 4; a tiny window absorbs reordering.
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= _SCAN_LIMIT:
                    break
                line = line.strip()
                if not line:
                    continue
                if '"' + _PROVENANCE_TYPE + '"' not in line:
                    continue
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(data, dict) or data.get("type") != _PROVENANCE_TYPE:
                    continue
                sentinel = data.get("x_converter")
                if not isinstance(sentinel, dict):
                    continue
                if not isinstance(sentinel.get("from"), dict):
                    continue
                if not isinstance(sentinel.get("lines_at_creation"), int):
                    continue
                return sentinel
    except OSError:
        return None
    return None


def current_line_count(transcript_path: Path) -> int:
    """Total non-blank-tolerant line count of a transcript (every physical line).

    Counts physical lines exactly as `_write_with_provenance` did (it wrote one
    line per dict), so this is directly comparable to `lines_at_creation` for the
    growth guard. Blank lines are counted too — a resumed file that appended blank
    lines still grew, and we refuse to delete grown files.
    """
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _line_text(line: dict[str, Any]) -> str:
    """Concatenated text of a user/assistant line's content blocks.

    Handles both bare-string content and the block-list shape. Used only for
    trailing-noise detection, so it is deliberately simple — no XML stripping.
    """
    msg = line.get("message")
    if not isinstance(msg, dict):
        return ""
    content = msg.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        )
    return ""


def _content_has_non_text_block(line: dict[str, Any]) -> bool:
    """True when message.content is a list containing ANY non-text block.

    A user turn whose content includes tool_result, image, or other non-text
    blocks carries semantic meaning even if the text blocks are empty — it must
    never be trimmed as trailing noise.
    """
    msg = line.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") != "text"
        for b in content
    )


def _is_trailing_noise(line: dict[str, Any]) -> bool:
    """True when a trailing user line is empty / interrupt / command scaffolding.

    A user line whose content list contains ANY non-text block (tool_result,
    image, etc.) is NEVER trailing noise — only lines whose content is entirely
    text (or an empty string) qualify. Without this guard a tool_result-only
    user turn reads as empty text and gets trimmed, leaving a dangling assistant
    tool_use that the API rejects on resume.
    """
    if line.get("type") != "user":
        return False
    # If the content has any non-text blocks, preserve the line unconditionally.
    if _content_has_non_text_block(line):
        return False
    text = _line_text(line).strip()
    if not text:
        return True
    return any(marker in text for marker in _TRAILING_NOISE_MARKERS)


def _read_raw_lines(path: Path) -> list[dict[str, Any]]:
    """Read a JSONL transcript into raw dicts, skipping unparseable lines."""
    out: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(data, dict):
                out.append(data)
    return out


def _keep_convo_lines(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only user/assistant lines that carry a dict message."""
    return [
        d
        for d in raw
        if d.get("type") in _CONVO_TYPES and isinstance(d.get("message"), dict)
    ]


def _prior_lineage(raw: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The first `_lineage` breadcrumb found in the source, else []."""
    for d in raw:
        lin = d.get("_lineage")
        if isinstance(lin, list):
            return lin
    return []


def _prior_converted_from(raw: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """The source's own `_converted_from` stamp, if it was itself a conversion.

    Present when the source transcript was produced by an earlier conversion. Its
    `kind` ('session' | 'subagent') says what the source was BEFORE that
    conversion — used to phrase the handoff's origin sentence correctly.
    """
    for d in raw:
        cf = d.get("_converted_from")
        if isinstance(cf, dict):
            return cf
    return None


def _extract_active_thread(
    lines: list[dict[str, Any]],
    *,
    drop_sidechain: bool = False,
) -> tuple[list[dict[str, Any]], int]:
    """Return (active_chain, dropped_count) for the active conversation thread.

    Algorithm: take the LAST user/assistant line in file order as the tip (after
    optionally dropping isSidechain lines for session_to_subagent), walk
    parentUuid links backward to the root, keep exactly that chain in
    root→tip order. Lines not on the chain (abandoned edit-branches, embedded
    sidechain turns) are dropped.

    `drop_sidechain=True` (session_to_subagent only): drop lines with truthy
    `isSidechain` BEFORE picking the tip — they are embedded subagent turns from
    the old format, not main-thread lines. Do NOT set this for subagent sources
    where every line is isSidechain=True.

    Fallback: if the parentUuid walk breaks (missing parent, cycle, or all lines
    lack parentUuid — e.g. synthetic transcripts), return lines as-is with 0
    dropped so file-order behavior is preserved.
    """
    candidates = list(lines)
    if drop_sidechain:
        candidates = [d for d in candidates if not d.get("isSidechain")]

    if not candidates:
        return lines, 0

    # Index by uuid for O(1) parent lookup.
    by_uuid: dict[str, dict[str, Any]] = {}
    for d in candidates:
        u = d.get("uuid")
        if u:
            by_uuid[u] = d

    # If no line has a uuid at all → synthetic transcript; fall back.
    if not by_uuid:
        return lines, 0

    # The canonical root is the FIRST candidate in file order; only parentUuid=null
    # on that node is a real root — null on any later node means the link is absent
    # (e.g. synthetic test fixtures that didn't wire parentUuid forward), which is
    # the "missing parent link" fallback case.
    first_uuid = candidates[0].get("uuid") if candidates else None

    # Tip: last candidate in file order.
    tip = candidates[-1]

    # Walk parentUuid links to build the chain.
    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    current: Optional[dict[str, Any]] = tip
    while current is not None:
        uid = current.get("uuid")
        if uid:
            if uid in visited:
                # Cycle — fall back to file order.
                return lines, 0
            visited.add(uid)
        chain.append(current)
        parent_uuid = current.get("parentUuid")
        if not parent_uuid:
            # Null parentUuid: real root only if this is the first file-order node.
            if uid and uid != first_uuid:
                # Non-first node with null parent → missing link; fall back.
                return lines, 0
            break  # genuine root
        parent = by_uuid.get(parent_uuid)
        if parent is None:
            # Missing parent link — fall back.
            return lines, 0
        current = parent

    chain.reverse()  # root → tip order

    kept_set = {id(d) for d in chain}
    dropped = sum(1 for d in lines if id(d) not in kept_set)
    return chain, dropped


def _relinearize(lines: list[dict[str, Any]]) -> None:
    """Rewrite parentUuid into a linear chain in file order, in place.

    First line gets parentUuid=null; each subsequent line points at the previous
    line's uuid. Source transcripts are TREES (message edits create siblings); a
    subagent/session transcript must be a linear chain, so we flatten. Every line
    is guaranteed a uuid (minted if missing).
    """
    parent: Optional[str] = None
    for d in lines:
        u = d.get("uuid") or str(uuid.uuid4())
        d["uuid"] = u
        d["parentUuid"] = parent
        parent = u


def _trim_trailing_noise(lines: list[dict[str, Any]]) -> int:
    """Drop trailing noise user turns in place. Returns the number trimmed."""
    trimmed = 0
    while lines and _is_trailing_noise(lines[-1]):
        lines.pop()
        trimmed += 1
    return trimmed


def _tail_state(lines: list[dict[str, Any]]) -> str:
    """'clean' when the last kept line is assistant, else 'pending_user_input'."""
    if lines and lines[-1].get("type") == "assistant":
        return "clean"
    return "pending_user_input"


def _model_stats(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """{first, last, counts} over assistant-line message.model values."""
    models: list[str] = []
    for d in lines:
        if d.get("type") != "assistant":
            continue
        msg = d.get("message")
        if isinstance(msg, dict):
            m = msg.get("model")
            if isinstance(m, str) and m:
                models.append(m)
    counts = dict(Counter(models))
    return {
        "first": models[0] if models else None,
        "last": models[-1] if models else None,
        "counts": counts,
    }


def _environment(lines: list[dict[str, Any]]) -> dict[str, Any]:
    """Original cwd / branch / version / last timestamp, with existence checks.

    `branch_exists` is None when the cwd is gone or isn't a git repo (we can't
    tell), True/False otherwise. `age_days` is whole days from the last
    timestamp to now.
    """
    cwd = ""
    branch = ""
    version = ""
    last_ts = ""
    for d in lines:
        if not cwd and isinstance(d.get("cwd"), str):
            cwd = d["cwd"]
        if not branch and isinstance(d.get("gitBranch"), str):
            branch = d["gitBranch"]
        if not version and isinstance(d.get("version"), str):
            version = d["version"]
        if isinstance(d.get("timestamp"), str):
            last_ts = d["timestamp"]

    cwd_exists = bool(cwd) and Path(cwd).is_dir()
    branch_exists = _branch_exists(cwd, branch) if cwd_exists else None

    age_days: Optional[int] = None
    if last_ts:
        try:
            ts = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = (datetime.now(timezone.utc) - ts).days
        except ValueError:
            age_days = None

    return {
        "original_cwd": cwd or None,
        "cwd_exists": cwd_exists,
        "original_branch": branch or None,
        "branch_exists": branch_exists,
        "cc_version": version or None,
        "last_timestamp": last_ts or None,
        "age_days": age_days,
    }


def _branch_exists(cwd: str, branch: str) -> Optional[bool]:
    """Whether `branch` is a valid ref in the repo at `cwd`. None if not a repo.

    Guarded git shell-out — `rev-parse --verify` against the branch ref. Returns
    None (not False) when cwd isn't a git repo, so the caller can distinguish
    "branch gone" from "can't tell".
    """
    if not branch:
        return None
    import subprocess

    try:
        inside = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return None

    try:
        verify = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return verify.returncode == 0


# =============================================================================
# Result objects
# =============================================================================


@dataclass
class ConversionResult:
    """The outcome of one conversion, carrying everything the tool surfaces."""

    direction: str
    created_id: str
    turns: int
    trimmed_trailing: int
    tail_state: str
    models: dict[str, Any]
    environment: dict[str, Any]
    lineage: list[dict[str, str]]
    nested_agents: int = 0
    dropped_branches: int = 0
    # session_to_subagent
    parent_session: Optional[str] = None
    suggested_handoff: Optional[str] = None
    # subagent_to_session
    title: Optional[str] = None
    project: Optional[str] = None
    # written paths (for tests / deletion)
    written_path: Optional[Path] = None
    meta_path: Optional[Path] = None


# =============================================================================
# session_to_subagent
# =============================================================================


def convert_session_to_subagent(
    *,
    src_session_id: str,
    src_path: Path,
    src_project_path: str,
    dest_parent_session_id: str,
    dest_parent_session_dir: Path,
    nested_agents: int = 0,
) -> ConversionResult:
    """Copy a session transcript into a new subagent under a parent session.

    `dest_parent_session_dir` is ``<projectDir>/<parentSessionId>`` — the new
    ``subagents/agent-<id>.jsonl`` and its meta sidecar are written under it.
    `nested_agents` is the count of subagents the SOURCE session itself ran; they
    are not copied (their results already appear inline), but the count is
    reported so the caller knows context was folded in.
    """
    raw = _read_raw_lines(src_path)
    all_lines = _keep_convo_lines(raw)
    prior = _prior_lineage(raw)
    src_converted_from = _prior_converted_from(raw)

    # For session_to_subagent: drop isSidechain lines (embedded subagent turns
    # from the old format) before picking the active thread tip.
    lines, dropped_branches = _extract_active_thread(all_lines, drop_sidechain=True)

    new_agent = _new_agent_id()

    for d in lines:
        for k in _STRIP_SESSION_TO_SUBAGENT:
            d.pop(k, None)
        d["isSidechain"] = True
        d["agentId"] = new_agent
        d["sessionId"] = dest_parent_session_id

    _relinearize(lines)

    # The first line of a resumable subagent needs a promptId.
    if lines and not lines[0].get("promptId"):
        lines[0]["promptId"] = str(uuid.uuid4())

    trimmed = _trim_trailing_noise(lines)

    lineage = list(prior) + [
        {"as": "session", "id": src_session_id},
        {"as": "subagent", "id": new_agent},
    ]
    converted_from = {
        "kind": "session",
        "id": src_session_id,
        "project": src_project_path,
    }
    # Stamp provenance keys in the existing mutation loop (no second pass).
    for d in lines:
        d["_converted_from"] = converted_from
        d["_lineage"] = lineage

    subagents_dir = dest_parent_session_dir / "subagents"
    subagents_dir.mkdir(parents=True, exist_ok=True)
    out_path = subagents_dir / f"agent-{new_agent}.jsonl"
    meta_path = subagents_dir / f"agent-{new_agent}.meta.json"

    # The x-converter-provenance line (line 1) is the only durable trust surface.
    # lines_at_creation counts the whole file (provenance line + body).
    provenance_line = _provenance_line(
        src_kind="session",
        src_id=src_session_id,
        src_project=src_project_path,
        session_id=dest_parent_session_id,
        agent_id=new_agent,
        lines_at_creation=len(lines) + 1,
    )
    # Write provenance line then body sequentially (no list concat).
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(provenance_line) + "\n")
        for d in lines:
            f.write(json.dumps(d) + "\n")

    src8 = src_session_id[:8]
    project_basename = Path(src_project_path).name if src_project_path else "?"
    # meta.json gets ONLY the standard three keys — any extra key is silently
    # dropped by the harness on first resume, so origin lives in `description`.
    meta = {
        "agentType": "general-purpose",
        "description": f"converted from session {src8} ({project_basename})",
        "toolUseId": _new_tool_use_id(),
    }
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f)

    handoff = _suggested_handoff(src8, project_basename, prior, src_converted_from)

    return ConversionResult(
        direction="session_to_subagent",
        created_id=new_agent,
        turns=len(lines),
        trimmed_trailing=trimmed,
        dropped_branches=dropped_branches,
        tail_state=_tail_state(lines),
        models=_model_stats(lines),
        environment=_environment(lines),
        lineage=lineage,
        nested_agents=nested_agents,
        parent_session=dest_parent_session_id,
        suggested_handoff=handoff,
        written_path=out_path,
        meta_path=meta_path,
    )


def _suggested_handoff(
    src8: str,
    project_basename: str,
    prior_lineage: list[dict[str, str]],
    src_converted_from: Optional[dict[str, Any]] = None,
) -> str:
    """The first-message handoff template, with the lineage-aware first sentence.

    When the SOURCE was itself a subagent-born transcript converted earlier
    (its own `_converted_from.kind == "subagent"`), the conversation was
    originally a subagent run inside some parent session, so we phrase its origin
    that way and name that parent's first 8 chars (recovered from the lineage's
    last "session" hop — the session that subagent ran under).
    """
    born_as_subagent = (
        src_converted_from is not None and src_converted_from.get("kind") == "subagent"
    )
    if born_as_subagent:
        # The session the original subagent ran inside is the most-recent "session"
        # hop in the inherited lineage.
        parent8 = "?"
        for hop in reversed(prior_lineage):
            if hop.get("as") == "session":
                parent8 = str(hop.get("id", ""))[:8]
                break
        first = (
            f"This conversation was a subagent run inside Claude Code session "
            f"`{parent8}`."
        )
    else:
        first = (
            f"This conversation was an interactive Claude Code session "
            f"(`{src8}`, project `{project_basename}`) between Claude and the user."
        )
    return (
        "[CONVERTED SESSION]\n"
        f"{first} It has been copied and is now continuing as a subagent — the "
        "original session is untouched. Messages from here on come from the agent "
        "that resumed you, not from the user above."
    )


# =============================================================================
# subagent_to_session
# =============================================================================


def convert_subagent_to_session(
    *,
    src_agent_id: str,
    src_path: Path,
    src_project_path: str,
    dest_project_dir: Path,
    dest_title: str,
) -> ConversionResult:
    """Copy a subagent transcript out to a new top-level session.

    Mints a fresh session uuid; the written file's name MUST equal the internal
    sessionId. Refuses to overwrite an existing session file.
    """
    raw = _read_raw_lines(src_path)
    all_lines = _keep_convo_lines(raw)
    prior = _prior_lineage(raw)

    # For subagent_to_session: do NOT drop sidechain lines — every line in a
    # subagent transcript is isSidechain=True; dropping them would empty the body.
    lines, dropped_branches = _extract_active_thread(all_lines, drop_sidechain=False)

    new_session = str(uuid.uuid4())

    lineage = list(prior) + [
        {"as": "subagent", "id": src_agent_id},
        {"as": "session", "id": new_session},
    ]
    converted_from = {
        "kind": "subagent",
        "id": src_agent_id,
        "project": src_project_path,
    }
    # Stamp provenance keys and strip/set fields in one loop (no second pass).
    for d in lines:
        for k in _STRIP_SUBAGENT_TO_SESSION:
            d.pop(k, None)
        d["isSidechain"] = False
        d["sessionId"] = new_session
        d["_converted_from"] = converted_from
        d["_lineage"] = lineage

    _relinearize(lines)
    trimmed = _trim_trailing_noise(lines)

    header = [
        {"type": "mode", "mode": "normal", "sessionId": new_session},
        {"type": "permission-mode", "permissionMode": "default", "sessionId": new_session},
        {"type": "custom-title", "customTitle": dest_title, "sessionId": new_session},
    ]

    # The provenance line sits right AFTER the custom-title header line: the
    # mode/permission-mode/custom-title header must lead for the harness to
    # resolve the file as a session, and provenance is well within read_provenance's
    # head-scan window. lines_at_creation counts the whole file (header + provenance
    # + body). agentId is null for session conversions.
    provenance_line = _provenance_line(
        src_kind="subagent",
        src_id=src_agent_id,
        src_project=src_project_path,
        session_id=new_session,
        agent_id=None,
        lines_at_creation=len(header) + 1 + len(lines),
    )

    out_path = dest_project_dir / f"{new_session}.jsonl"
    if out_path.exists():
        raise FileExistsError(
            f"Refusing to overwrite existing session file: {out_path}"
        )
    dest_project_dir.mkdir(parents=True, exist_ok=True)
    # Write header, provenance, then body sequentially (no list concat).
    with open(out_path, "w", encoding="utf-8") as f:
        for d in header:
            f.write(json.dumps(d) + "\n")
        f.write(json.dumps(provenance_line) + "\n")
        for d in lines:
            f.write(json.dumps(d) + "\n")

    return ConversionResult(
        direction="subagent_to_session",
        created_id=new_session,
        turns=len(lines),
        trimmed_trailing=trimmed,
        dropped_branches=dropped_branches,
        tail_state=_tail_state(lines),
        models=_model_stats(lines),
        environment=_environment(lines),
        lineage=lineage,
        title=dest_title,
        project=src_project_path,
        written_path=out_path,
    )


# =============================================================================
# Rewind — truncate a conversion artifact in place to an earlier turn
# =============================================================================
#
# Rewind mutates a conversion artifact IN PLACE: it cuts the transcript at a
# chosen turn, discards everything after the cut, and rewrites the file so the
# artifact resumes from the earlier point. This is destructive (the discarded
# tail is gone), which is exactly why it is gated on the x-converter-provenance
# line: we only ever mutate files WE wrote. A real session or subagent is never
# touched — the caller refuses anything without provenance before calling here.
#
# The structural prefix (session header lines + the provenance line) is preserved
# verbatim except for the provenance line's `lines_at_creation`, which is
# re-stamped to the new physical line count so the growth guard and deletion stay
# coherent (current == created right after a rewind → still deletable, not "grown").


def _assistant_has_tool_use(line: dict[str, Any]) -> bool:
    """True when an assistant line's content carries any tool_use block.

    When such a line is the tail of a truncated transcript, the tool_use has no
    following tool_result — a dangling call the Anthropic API rejects on resume.
    Rewind trims these off the tail so the truncated artifact stays resumable.
    """
    if line.get("type") != "assistant":
        return False
    msg = line.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if not isinstance(content, list):
        return False
    return any(
        isinstance(b, dict) and b.get("type") == "tool_use" for b in content
    )


def _trim_resumable_tail(lines: list[dict[str, Any]]) -> tuple[int, int]:
    """Trim the tail in place until it is a resumable boundary.

    Pops, in a single pass to fixpoint: (a) trailing-noise user turns
    (empty/interrupt/command scaffolding) and (b) a trailing assistant turn whose
    content has an unanswered tool_use (dangling after truncation). Returns
    (trimmed_trailing, trimmed_dangling_tool_use).

    A user tail (pending_user_input) and an assistant text tail (clean) are both
    valid resume boundaries, so neither is trimmed.
    """
    trimmed_trailing = 0
    trimmed_dangling = 0
    while lines:
        if _is_trailing_noise(lines[-1]):
            lines.pop()
            trimmed_trailing += 1
            continue
        if _assistant_has_tool_use(lines[-1]):
            lines.pop()
            trimmed_dangling += 1
            continue
        break
    return trimmed_trailing, trimmed_dangling


def _ancestor_chain(
    lines: list[dict[str, Any]], target_uuid: str
) -> list[dict[str, Any]]:
    """The root→target lineage: the target and every ancestor, oldest first.

    Walks `parentUuid` links backward from the target to the root, so the kept
    conversation is exactly the chain leading to the chosen turn — NOT a file-order
    slice. A conversion artifact resumed interactively can grow into a TREE (a
    message edit forks sibling branches); file-order slicing would splice abandoned
    branches into the result, but the ancestor walk keeps only the real lineage.

    A cycle (malformed transcript) or a missing parent link terminates the walk at
    that point, treating the last reachable node as the root.
    """
    by_uuid: dict[str, dict[str, Any]] = {}
    for d in lines:
        u = d.get("uuid")
        if u is not None:
            by_uuid[u] = d  # last write wins; the target is guarded as unique upstream

    chain: list[dict[str, Any]] = []
    visited: set[str] = set()
    current: Optional[dict[str, Any]] = by_uuid.get(target_uuid)
    while current is not None:
        uid = current.get("uuid")
        if uid in visited:
            break  # cycle guard
        if uid is not None:
            visited.add(uid)
        chain.append(current)
        parent_uuid = current.get("parentUuid")
        current = by_uuid.get(parent_uuid) if parent_uuid else None
    chain.reverse()
    return chain


@dataclass
class RewindResult:
    """The outcome of one in-place rewind."""

    kind: str  # 'subagent' | 'session'
    artifact_id: str
    target_turn: str
    cut: str  # 'after' | 'before'
    turns_before: int
    turns_after: int
    removed_after_cut: int
    trimmed_trailing: int
    trimmed_dangling_tool_use: int
    tail_state: str
    models: dict[str, Any]
    environment: dict[str, Any]
    lineage: list[dict[str, str]]
    written_path: Path


def rewind_transcript(
    *,
    transcript_path: Path,
    artifact_id: str,
    kind: str,
    turn: str,
    cut: str = "after",
) -> RewindResult:
    """Truncate a conversion artifact in place at `turn`, discarding the rest.

    `kind` is 'subagent' or 'session' (caller-supplied, for the result). `turn`
    is a uuid or prefix of a body (user/assistant) line in this transcript. `cut`
    is 'after' (keep through the named turn — it becomes the new tail) or 'before'
    (discard the named turn and everything after — the turn is the first line
    dropped). After the cut the tail is trimmed to a resumable boundary.

    The caller MUST have verified the file carries a provenance line before
    calling — this function re-reads it only to re-stamp `lines_at_creation`.

    Raises ValueError on: a turn that matches no body line, an ambiguous prefix
    (more than one distinct uuid), a target uuid that repeats in the transcript, a
    missing provenance line, or a cut/trim that would leave the transcript empty.
    """
    if cut not in ("after", "before"):
        raise ValueError(f"cut must be 'after' or 'before', got {cut!r}")

    raw = _read_raw_lines(transcript_path)

    # Structural prefix = everything before the first body line (session header
    # lines + the provenance line). Preserved verbatim except for the re-stamp.
    first_body_idx = next(
        (i for i, d in enumerate(raw) if d.get("type") in _CONVO_TYPES), None
    )
    if first_body_idx is None:
        raise ValueError("transcript has no user/assistant turns to rewind")
    prefix = raw[:first_body_idx]

    prov_idx = next(
        (i for i, d in enumerate(prefix) if d.get("type") == _PROVENANCE_TYPE), None
    )
    if prov_idx is None:
        raise ValueError(
            "no x-converter-provenance line found — refusing to rewind a "
            "non-conversion transcript"
        )

    body = _keep_convo_lines(raw)
    turns_before = len(body)

    # Resolve the target turn within the body by uuid or prefix.
    matched = [
        d for d in body if (u := d.get("uuid")) and (u == turn or u.startswith(turn))
    ]
    if not matched:
        raise ValueError(f"turn {turn!r} not found in this transcript")
    distinct = {d["uuid"] for d in matched}
    if len(distinct) > 1:
        raise ValueError(
            f"turn prefix {turn!r} is ambiguous — it matches {len(distinct)} "
            f"distinct turns in this transcript. Pass a longer id."
        )
    target_full = next(iter(distinct))
    if sum(1 for d in body if d.get("uuid") == target_full) > 1:
        raise ValueError(
            f"turn {target_full[:8]} appears more than once in this transcript "
            "(a resumed transcript can repeat a uuid) — cannot unambiguously rewind "
            "to it. Pick a turn with a unique id."
        )

    # Keep the lineage leading to the target — its ancestor chain — not a file-order
    # slice: a resumed artifact may contain abandoned edit-branch siblings, and only
    # the chain from root to the target is the conversation we are rewinding to.
    # cut='after' keeps through the target; cut='before' drops the target itself.
    chain = _ancestor_chain(body, target_full)
    kept = chain if cut == "after" else chain[:-1]
    removed_after_cut = turns_before - len(kept)

    trimmed_trailing, trimmed_dangling = _trim_resumable_tail(kept)

    if not kept:
        raise ValueError(
            "rewind would discard the entire conversation — nothing remains after "
            f"the cut={cut} at turn {target_full[:8]} and the resumable-tail trim. "
            "Pick a later turn or cut='after'."
        )

    # The kept chain is already linear; relinearize normalizes parentUuid (first
    # null, each pointing at its predecessor) and mints any missing uuid.
    _relinearize(kept)

    lineage = _prior_lineage(raw)

    # Re-stamp lines_at_creation to the new physical line count so the growth
    # guard stays coherent, and record the rewind for forensics.
    new_total = len(prefix) + len(kept)
    sentinel = prefix[prov_idx].get("x_converter")
    if isinstance(sentinel, dict):
        sentinel["lines_at_creation"] = new_total
        sentinel["rewound_to"] = target_full
        sentinel["rewound_at"] = _now_iso()

    # Write atomically: a destructive in-place truncation must never leave the
    # transcript half-written. Build the new file beside the original, then
    # os.replace() it in one atomic swap; on ANY failure the original is untouched.
    tmp_path = transcript_path.with_name(transcript_path.name + ".rewind-tmp")
    try:
        with open(tmp_path, "w", encoding="utf-8") as f:
            for d in prefix:
                f.write(json.dumps(d) + "\n")
            for d in kept:
                f.write(json.dumps(d) + "\n")
        os.replace(tmp_path, transcript_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    return RewindResult(
        kind=kind,
        artifact_id=artifact_id,
        target_turn=target_full,
        cut=cut,
        turns_before=turns_before,
        turns_after=len(kept),
        removed_after_cut=removed_after_cut,
        trimmed_trailing=trimmed_trailing,
        trimmed_dangling_tool_use=trimmed_dangling,
        tail_state=_tail_state(kept),
        models=_model_stats(kept),
        environment=_environment(kept),
        lineage=lineage,
        written_path=transcript_path,
    )


# =============================================================================
# Deletion — only artifacts carrying conversion provenance
# =============================================================================


def is_conversion_artifact(transcript_path: Path) -> bool:
    """True when a transcript carries a shape-valid x-converter-provenance line.

    The single trust signal for both directions: meta.json is no longer a trust
    surface (it's rewritten on resume), so a conversion is identified ONLY by the
    provenance line at the top of its jsonl. Works for agent and session
    transcripts alike — read_provenance scans the head and validates the sentinel.
    """
    return read_provenance(transcript_path) is not None


def growth_exceeded(
    transcript_path: Path, sentinel: Optional[dict[str, Any]] = None
) -> bool:
    """True when a tagged transcript has grown past its lines_at_creation.

    A resumed-or-built-upon conversion has more lines than it was written with;
    someone may now depend on it. Callers refuse to delete such files. Returns
    False (no growth) when there is no valid provenance line — that case is a
    separate refusal (`not a conversion artifact`), handled by the caller.

    Pass `sentinel` to reuse an already-read provenance dict (parallel to
    `conversion_age_seconds`) — a caller that just read it avoids a second
    head-scan of the same file.
    """
    if sentinel is None:
        sentinel = read_provenance(transcript_path)
    if sentinel is None:
        return False
    created = sentinel.get("lines_at_creation")
    if not isinstance(created, int):
        return False
    return current_line_count(transcript_path) > created


def conversion_age_seconds(
    transcript_path: Path, sentinel: Optional[dict[str, Any]] = None
) -> Optional[float]:
    """Seconds since this conversion artifact was written, per provenance `converted_at`.

    `converted_at` is stamped into the provenance sentinel at creation and never
    rewritten (the body is append-only on resume), so it is a truthful birth time
    — preferred over file mtime, which fs operations can perturb. Falls back to
    mtime only when `converted_at` is absent or unparseable. Returns None when the
    path carries no valid conversion provenance (not a conversion artifact).

    Pass `sentinel` to reuse an already-read provenance dict and avoid a second
    head-scan. This is age-since-CREATION, not age-since-last-activity: pristine
    artifacts never grow, so for them the two coincide; grown ones are protected
    by the growth guard and are never reaped on age alone (see the reaper).
    """
    if sentinel is None:
        sentinel = read_provenance(transcript_path)
    if sentinel is None:
        return None
    ts: Optional[datetime] = None
    converted_at = sentinel.get("converted_at")
    if isinstance(converted_at, str):
        try:
            ts = datetime.fromisoformat(converted_at.replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
        except ValueError:
            ts = None
    if ts is None:
        try:
            ts = datetime.fromtimestamp(
                transcript_path.stat().st_mtime, tz=timezone.utc
            )
        except OSError:
            return None
    return (datetime.now(timezone.utc) - ts).total_seconds()


def delete_agent_conversion(transcript_path: Path) -> None:
    """Remove an agent conversion artifact: its transcript and meta sidecar.

    Only subagent conversions are ever deleted by the tool — converted SESSIONS
    are for humans to manage and are refused unconditionally, so there is no
    session-deletion counterpart.
    """
    meta_path = transcript_path.with_suffix(".meta.json")
    transcript_path.unlink(missing_ok=True)
    meta_path.unlink(missing_ok=True)


def existing_custom_titles(project_dirs: list[Path]) -> set[str]:
    """Every custom-title value across all *.jsonl in the given project dirs.

    `claude --resume "<title>"` resolves by scanning custom-title lines, so a
    duplicate title breaks resolution. We collect existing titles to refuse a
    colliding dest_title before writing.
    """
    titles: set[str] = set()
    for d in project_dirs:
        if not d.is_dir():
            continue
        for jsonl in d.glob("*.jsonl"):
            try:
                with open(jsonl, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or '"custom-title"' not in line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if data.get("type") == "custom-title":
                            t = data.get("customTitle")
                            if isinstance(t, str):
                                titles.add(t)
            except OSError:
                continue
    return titles
