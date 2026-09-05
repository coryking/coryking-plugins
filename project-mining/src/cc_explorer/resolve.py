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

from .corpus import SessionRef
from .search import SessionInfo
from .identifiers import MIN_ID_LEN, ambiguous_id, matching_ids
from .subagents import collect_agent_files, resolve_subagents_dir


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
    matches = matching_ids(refs, session, lambda r: (r.session_id,))
    distinct = {(r.harness, r.session_id): r for r in matches}
    if len(distinct) > 1:
        # Only inspect the colliding candidates; discovery remains filename-only.
        surviving = [(r, info) for r in distinct.values()
                     if (info := SessionInfo.load(r)) is not None]
        if len(surviving) == 1:
            return surviving[0][0]
        if not surviving:
            return None
        raise ToolError(str(ambiguous_id(session, "Session", (
            f"{r.session_id} [{r.harness.value}] in {r.project_path} "
            f"({info.first_timestamp}; {info.title})"
            for r, info in surviving
        ))))
    return next(iter(distinct.values()), None)


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
    # Session and agent identities share one lookup so exact matches win across
    # kinds too. No body parsing is needed for mutation target selection.
    index: list[tuple[str, str, Path, SessionRef]] = []
    for ref in refs:
        index.append(("session", ref.session_id, ref.path, ref))
        for agent in collect_agent_files(resolve_subagents_dir(ref.path)):
            if agent.agent_id:
                index.append(("subagent", agent.agent_id, agent.path, ref))

    resolved: list[tuple[str, str, str, Optional[Path]]] = []
    for raw_id in raw_ids:
        if len(raw_id) < MIN_ID_LEN:
            raise ToolError(
                f"Id {raw_id!r} is too short ({len(raw_id)} chars) — pass at least "
                f"{MIN_ID_LEN} chars to avoid accidental prefix matches. "
                "Use list_project_sessions or list_session_agents to find full ids."
            )
        matches = matching_ids(index, raw_id, lambda row: (row[1],))
        distinct = {(kind, fid, path): (kind, fid, path, ref)
                    for kind, fid, path, ref in matches}
        if len(distinct) > 1:
            raise ToolError(str(ambiguous_id(raw_id, "Artifact", (
                f"{kind} {fid} in {ref.project_path} (session {ref.session_id})"
                for kind, fid, path, ref in distinct.values()
            ))))
        if distinct:
            kind, fid, path, _ = next(iter(distinct.values()))
            resolved.append((raw_id, kind, fid, path))
        else:
            resolved.append((raw_id, "", "", None))
    return resolved
