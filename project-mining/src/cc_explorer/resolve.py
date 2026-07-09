"""Id → artifact resolution for the MCP tool layer.

Session ids, agent ids, and their prefixes resolve against `SessionRef`s —
filename identity only, NO transcript parse. Parsing every transcript just to
resolve one id was the full-corpus-read hazard that timed out MCP calls
mid-mutation (rewind) and fed the unbounded-cache blowup; resolution now stays
sub-second at any corpus size, and callers promote only the ref(s) they
resolved (via `search.SessionInfo.load`).

Raises fastmcp ToolError directly — this is a server-side module, and the
error text (ambiguity candidates, too-short ids) IS the tool's UX.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastmcp.exceptions import ToolError

from .corpus import MIN_ID_LEN, SessionRef
from .search import SessionInfo
from .subagents import collect_agent_files, resolve_subagents_dir
from .utils import PrefixId


def resolve_unique_ref_or_none(
    refs: list[SessionRef], session: str
) -> Optional[SessionRef]:
    """Resolve a session id/prefix to one SessionRef, None on no match.

    Ambiguity is a hard error even here: a colliding prefix must be
    disambiguated by the caller, not silently re-routed to a fallback path.
    Decided over sessions that actually parse non-empty — a prefix colliding
    with an empty/unreadable session file resolves through to the real one
    rather than erroring, matching pre-redesign behavior. Promotion (via
    `SessionInfo.load`) touches only the colliding refs, so cost stays
    bounded regardless of corpus size.
    """
    matches = [r for r in refs if r.session_id == session]
    if not matches:
        return None
    distinct = {r.session_id.full for r in matches}
    if len(distinct) > 1:
        # One ref per distinct full id decides whether the collision is real:
        # a prefix matching an empty/unparseable session alongside a real one
        # isn't ambiguous, it's just noise from the empty file.
        one_per_id = {r.session_id.full: r for r in matches}
        surviving = {
            fid: r for fid, r in one_per_id.items() if SessionInfo.load(r) is not None
        }
        if len(surviving) == 1:
            return next(iter(surviving.values()))
        if not surviving:
            return None
        where = ", ".join(
            sorted({r.project_path or "?" for r in surviving.values()})
        )
        raise ToolError(
            f"Session prefix {session!r} is ambiguous — it matches {len(surviving)} "
            f"distinct sessions (in: {where}). Pass a longer id or scope with `projects`."
        )
    return matches[0]


def resolve_unique_ref(refs: list[SessionRef], session: str) -> SessionRef:
    """Resolve a session id/prefix to exactly one SessionRef, or raise.

    Prefix matching across the whole corpus can be ambiguous (an 8-char prefix
    may match more than one full session id). Rather than silently picking the
    first/newest, surface the collision so the caller can disambiguate with a
    longer id or an explicit `projects` scope.
    """
    ref = resolve_unique_ref_or_none(refs, session)
    if ref is None:
        raise ToolError(f"No session matching: {session}")
    return ref


def resolve_artifacts(
    raw_ids: list[str],
    refs: list[SessionRef],
) -> list[tuple[str, str, str, Optional[Path]]]:
    """Resolve ids to (raw_id, kind, full_id, path) tuples, raising on any problem.

    Works over SessionRefs (caller narrows the corpus first — typically via
    `Corpus.narrow_to_artifact_ids`). For each id:
      - Rejects ids shorter than MIN_ID_LEN with a clear error.
      - Finds ALL matching sessions and agent files for the id.
      - Raises ToolError on ambiguity (listing candidates + their projects).
      - Returns a 4-tuple on unique match, or a no-match placeholder
        (raw_id, "", "", None) so the caller can format its own refused reason.
    """
    # Build agent index once: (agent_id, project_path, transcript path)
    agent_index: list[tuple[str, str, Path]] = []
    for r in refs:
        for af in collect_agent_files(resolve_subagents_dir(r.path)):
            if af.agent_id:
                agent_index.append((af.agent_id, r.project_path or "?", af.path))

    resolved: list[tuple[str, str, str, Optional[Path]]] = []
    for raw_id in raw_ids:
        if len(raw_id) < MIN_ID_LEN:
            raise ToolError(
                f"Id {raw_id!r} is too short ({len(raw_id)} chars) — pass at least "
                f"{MIN_ID_LEN} chars to avoid accidental prefix matches. "
                f"Use list_project_sessions or list_session_agents to find full ids."
            )
        # Session matches
        session_matches = [r for r in refs if r.session_id == raw_id]
        distinct_sess = {r.session_id.full for r in session_matches}
        # Agent matches
        agent_matches = [
            (fid, proj, p) for fid, proj, p in agent_index if PrefixId(fid) == raw_id
        ]
        distinct_agents = {fid for fid, _, _ in agent_matches}

        total_kinds = len(distinct_sess) + len(distinct_agents)
        if total_kinds > 1:
            candidates: list[str] = []
            for r in session_matches:
                candidates.append(
                    f"session {r.session_id.full[:12]} in {r.project_path or '?'}"
                )
            for fid, proj, _ in agent_matches:
                candidates.append(f"agent {fid[:12]} in {proj}")
            raise ToolError(
                f"Id prefix {raw_id!r} is ambiguous — it matches {total_kinds} distinct "
                f"artifacts: {'; '.join(candidates)}. Pass a longer id to disambiguate."
            )
        if len(distinct_sess) == 1:
            r = session_matches[0]
            resolved.append((raw_id, "session", r.session_id.full, r.path))
        elif len(distinct_agents) == 1:
            fid, _, p = agent_matches[0]
            resolved.append((raw_id, "subagent", fid, p))
        else:
            resolved.append((raw_id, "", "", None))
    return resolved
