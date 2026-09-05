"""MCP server wrapping the cc_explorer library.

Exposes Claude Code and Codex transcript exploration as MCP tools via FastMCP.
Most tools are read-only and return typed Pydantic response models; the
conversion tools (convert_session, rewind_transcript, delete_conversions)
write/mutate/delete transcript copies. FastMCP auto-generates output schemas from
return type annotations.
"""

import asyncio
import os
import re
import sys
import traceback
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal, Optional

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.server.middleware import CallNext, Middleware, MiddlewareContext
from fastmcp.tools.base import ToolResult
from mcp.types import CallToolRequestParams
from pydantic import Field
from pydantic import ValidationError as PydanticValidationError

from ._claude_paths import _get_projects_dir
from .activity import build_activity_timeline
from .conversion import (
    conversion_age_seconds,
    convert_session_to_subagent,
    convert_subagent_to_session,
    delete_agent_conversion,
    existing_custom_titles,
    growth_exceeded,
    is_conversion_artifact,
    read_provenance,
    rewind_transcript as rewind_transcript_file,
)
from .corpus import (
    MIN_ID_LEN,
    Corpus,
    SessionRef,
    discover_projects,
    resolve_project,
    resolve_projects,
)
from .failures import narrow_to_error_sessions, survey_failures as run_failure_survey
from .formatting import matches_id
from .models import FailureKind, TranscriptStats, parse_hide, parse_kinds
from .param_repair import argument_error_message, repair_arguments
from .parser import collect_parser_diagnostics, load_conversations, load_transcript
from .providers import Harness
from .resolve import (
    resolve_artifacts,
    resolve_unique_ref,
    resolve_unique_ref_or_none,
)
from .utils import PrefixId
from .responses import (
    ActivityTimelineResponse,
    AgentDetailResponse,
    AgentListResponse,
    AgentToolAudit,
    AgentToolCall,
    BrowseSessionResponse,
    ConvertSessionResponse,
    DeleteConversionsResponse,
    DeletedConversion,
    GrepSessionResponse,
    GrepSessionsResponse,
    ProjectListResponse,
    ReadTurnResponse,
    RefusedDeletion,
    RewindTranscriptResponse,
    SearchProjectsResponse,
    SessionAgentsResponse,
    SessionListResponse,
    SessionToolAuditResponse,
    SurveyFailuresResponse,
)
from .search import (
    ENTRY_TYPE_MAP,
    ConversationRole,
    SessionInfo,
    browse_session_turns,
    conversation_types_for,
    get_turn_context,
    promote_refs,
    search_multi,
    sort_sessions_newest_first,
    triage_multi,
)
from .subagents import (
    collect_agent_files,
    discover_subagents,
    extract_agent_tool_audit,
    resolve_output_files,
    resolve_subagents_dir,
    scan_output_file_stats,
)

_INSTRUCTIONS = """\
cc-explorer explores Claude Code and Codex transcript history stored as local
JSONL files. Search/list/read tools span both harnesses by default; pass
`harnesses=["claude"]` or `harnesses=["codex"]` to narrow them. Projects are
selected with `projects` (paths or bare names; git
worktrees are flattened into one project). Omit `projects` to search across ALL
projects — the recall path when you remember a conversation but not where it
happened. Every result names its harness. Claude search includes nested subagent
bodies; Codex subagents are independent rollout sessions linked by metadata.

1. Conversations — find and read what was discussed.
   When you don't know the project, start with search_projects (omit `projects`)
   to find which project/session a phrase lives in, or list_projects to see what
   exists. Within a project, orient with list_project_sessions (like `ls`), then
   zoom in: grep_session / grep_sessions for matches-in-context, read_turn /
   browse_session to read at full fidelity. Every result carries its `project`,
   so pass that back to scope follow-ups. In agent-team sessions, a user turn
   that is a teammate DM (orchestration, not the human) renders labeled as
   `[teammate: sender -> recipient] ...` rather than raw <teammate-message> XML.

2. Claude agent forensics — see what a session's subagents actually did.
   Start from list_project_sessions(min_agents=1) to find sessions that spawned
   agents, then list_session_agents to see every agent the session ran (workflow
   ones included), get_agent_detail for one agent's prompt / result / tool-trace,
   and audit_session_tools to check whether the agents used their tools correctly
   (per-tool counts, error rates, retries).

3. Failures — what broke, without guessing what the errors said.
   survey_failures is the landing page: every failed tool call in a window,
   classified by kind and category, counted per tool and per session. Then
   drill in with grep_session / grep_sessions / search_projects using
   errors_only=true, which restricts hits to failed tool calls so the pattern
   narrows the TOPIC ("ssh", "psql") rather than the error vocabulary.

4. Attention reconstruction — what a window of agent-driving looked like.
   get_activity_timeline rolls every project's transcripts over a time window
   into a turn-count grid plus pre-computed attention rollups (sessions running
   at once, peaks, hands-on vs autonomous time). Session ids and projects it
   returns pass straight back to the tools above.

5. Interview — ask a past session what it meant, when grep can't answer.
   convert_session copies the session into a resumable subagent; SendMessage
   resumes it (no agent-teams needed — if SendMessage is not in your toolset it
   is deferred, so load it with ToolSearch query "select:SendMessage"); send ONE
   batched message with every question (splitting re-bills the replayed
   context); then delete_conversions(ids=[created_id], force=true).
   For a question-shaped investigation rather than a single lookup, dispatch the
   session-researcher agent and let it drive this loop in its own context.
"""

# =============================================================================
# Conversion-artifact reaper (lifespan-driven garbage collection)
# =============================================================================
#
# convert_session writes resumable transcript COPIES (see conversion.py). The LLM
# can't be trusted to clean them up — a session never knows it's ending, so it
# never reaches "now I'll delete that fork." The reliable lifecycle signal lives
# one layer down: Claude Code spawns a dedicated stdio MCP server PER SESSION
# (see _current_session_id), so this server's process lifetime ≈ the session's.
# We hook FastMCP's lifespan and sweep stale artifacts on both ends:
#
#   startup  — GUARANTEED to run (the server can't serve a tool without starting).
#              This is the backstop: it reaps whatever a previous session's
#              crash/SIGKILL left behind.
#   shutdown — best-effort (a try/finally runs on cooperative cancel — SIGTERM/
#              SIGINT — but a hard SIGKILL skips it). This is just timeliness.
#
# Two guards make the sweep safe, both already in conversion.py:
#   - growth guard: a fork that was RESUMED has more lines than at creation
#     (growth_exceeded) and carries unique conversation that exists nowhere else
#     — NEVER reaped here. Reaping grown-but-cold forks is a separate, weightier
#     retention decision (it destroys history, not a copy) tracked as its own
#     issue.
#   - age gate: a PRISTINE fork younger than the threshold may still be resumed
#     (you can convert now and `--resume` next session), so only pristine forks
#     older than the threshold are reaped. Pristine forks are pure duplicates of
#     an untouched source, so reaping one loses nothing — it's regenerable.
#
# Converted SESSIONS (subagent_to_session output) are never touched, matching
# delete_conversions: a session is for a human to open and manage.

_REAP_DEFAULT_AGE_HOURS = 24.0


def _reap_enabled() -> bool:
    """Reaper runs unless CC_EXPLORER_REAP is an explicit off value."""
    return os.environ.get("CC_EXPLORER_REAP", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _reap_age_seconds() -> float:
    """Reap-eligibility age in seconds (CC_EXPLORER_REAP_AGE_HOURS, default 24h).

    Only a strictly positive override is honored. Zero or negative would make
    *every* pristine fork eligible the instant it's written — a fork you convert
    now and mean to resume next session would be reaped before you got back to
    it — so a non-positive (or unparseable) value falls back to the default
    rather than silently arming an immediate sweep. To disable reaping entirely,
    set CC_EXPLORER_REAP=0; to sweep aggressively, use a small positive value.
    """
    raw = os.environ.get("CC_EXPLORER_REAP_AGE_HOURS")
    if raw:
        try:
            hours = float(raw)
            if hours > 0:
                return hours * 3600.0
        except ValueError:
            pass
    return _REAP_DEFAULT_AGE_HOURS * 3600.0


def _reaper_log(message: str) -> None:
    """Emit a reaper diagnostic to STDERR.

    stdout is the stdio MCP protocol channel — writing there corrupts framing.
    The lifespan runs outside any request, so there's no `ctx` to log through;
    stderr is the only safe sink (Claude Code captures it to its MCP server log).
    """
    print(f"[cc-explorer reaper] {message}", file=sys.stderr, flush=True)


def _reap_stale_conversions(age_seconds: float) -> list[Path]:
    """Delete pristine, cold subagent conversion artifacts. Returns reaped paths.

    Walks the projects root directly (8-line head-reads per candidate — no full
    session parse, so it's cheap enough for a startup hook). Only ever deletes
    files under `<project>/<session>/subagents/agent-*.jsonl` that (a) carry a
    valid x-converter-provenance line, (b) have NOT grown past lines_at_creation,
    and (c) are older than `age_seconds`. Session-shaped conversions are never
    matched by this glob, so converted sessions are structurally safe.

    Defensive throughout: any per-file error is skipped, never raised — cleanup
    must never take down the server.
    """
    reaped: list[Path] = []
    try:
        projects_root = _get_projects_dir()
    except OSError:
        return reaped
    if not projects_root.is_dir():
        return reaped

    for path in projects_root.glob("*/*/subagents/agent-*.jsonl"):
        try:
            sentinel = read_provenance(path)
            if sentinel is None:
                continue  # not a conversion artifact — a real dispatched subagent
            if growth_exceeded(path, sentinel):
                continue  # resumed/built-upon — unique history, hands off
            age = conversion_age_seconds(path, sentinel)
            if age is None or age <= age_seconds:
                continue  # too young — may still be resumed
            delete_agent_conversion(path)
            reaped.append(path)
        except OSError:
            continue
    return reaped


def _run_reaper(phase: str) -> None:
    """Run one reaper sweep, fully guarded. `phase` is 'startup' or 'shutdown'."""
    if not _reap_enabled():
        return
    try:
        reaped = _reap_stale_conversions(_reap_age_seconds())
    except Exception:  # never let cleanup break the server lifecycle
        # Log the full traceback (not just repr) so a reaper bug — which would
        # otherwise silently turn the sweep into a no-op for the whole process —
        # is diagnosable from the MCP server's stderr log.
        _reaper_log(f"{phase} sweep failed:\n{traceback.format_exc()}")
        return
    if reaped:
        _reaper_log(f"{phase}: reaped {len(reaped)} stale conversion artifact(s)")


@asynccontextmanager
async def _conversion_reaper_lifespan(server: "FastMCP"):
    """FastMCP lifespan: reap stale conversion artifacts on startup and shutdown.

    Startup runs the guaranteed backstop sweep; shutdown (in `finally`, so it
    survives cooperative cancellation) adds timeliness for the just-ended session.
    The sweep is blocking filesystem I/O, so it runs in a worker thread to keep
    the event loop free while the server comes up / winds down.
    """
    await asyncio.to_thread(_run_reaper, "startup")
    try:
        yield {}
    finally:
        await asyncio.to_thread(_run_reaper, "shutdown")


mcp = FastMCP(
    "cc-explorer",
    instructions=_INSTRUCTIONS,
    lifespan=_conversion_reaper_lifespan,
)


# =============================================================================
# Parameter repair (forgive the wrong guess, teach the right name)
# =============================================================================


class ParameterRepairMiddleware(Middleware):
    """Rewrite unambiguous wrong parameter names, and teach the rest.

    Sits at the tools/call boundary — one mechanism for every tool, present and
    future — and reads each tool's own JSON schema, so it needs no per-tool
    table. The pure logic lives in param_repair.py (schema in, arguments out);
    this class is just the FastMCP wiring.

    Both halves run before/after `call_next`:

    * BEFORE: aliases (`query` -> `patterns`, `project` -> `projects`, ...) are
      resolved and bare strings are wrapped for list parameters, so the call
      validates instead of erroring. The advertised schema is untouched — the
      client never sees an alias, and the repair leaves no trace in the result.
    * AFTER: a pydantic argument-validation error is re-raised as a ToolError
      whose message names the likely intended parameter and lists the tool's
      real ones. Errors raised INSIDE the tool body (transcript models, etc.)
      are not argument errors and pass through untouched — the pydantic title
      of a function-call validation is `call[...]`, which is the gate.
    """

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        schema = await self._tool_schema(context)
        arguments = context.message.arguments
        if schema and arguments:
            repaired = repair_arguments(arguments, schema)
            if repaired != arguments:
                # Mutate in place: call_next reads `context.message.arguments`.
                arguments.clear()
                arguments.update(repaired)
        try:
            return await call_next(context)
        except PydanticValidationError as exc:
            if schema is None or not exc.title.startswith("call["):
                raise
            raise ToolError(
                argument_error_message(
                    context.message.name,
                    schema,
                    context.message.arguments or {},
                    exc.errors(include_url=False),
                )
            ) from exc

    @staticmethod
    async def _tool_schema(
        context: MiddlewareContext[CallToolRequestParams],
    ) -> dict | None:
        """The called tool's input schema, or None if it can't be resolved.

        An unknown/disabled tool name resolves to nothing; that failure mode
        (stale tool names) belongs to call_tool, not here, so we get out of the
        way and let it raise its own error.
        """
        server = getattr(context.fastmcp_context, "fastmcp", None)
        if server is None:
            return None
        try:
            tool = await server.get_tool(context.message.name)
        except Exception:
            return None
        return getattr(tool, "parameters", None)


class ParserDiagnosticsMiddleware(Middleware):
    """Bound parser stderr to one summary per tool call, including failures."""

    async def on_call_tool(
        self,
        context: MiddlewareContext[CallToolRequestParams],
        call_next: CallNext[CallToolRequestParams, ToolResult],
    ) -> ToolResult:
        with collect_parser_diagnostics():
            return await call_next(context)


mcp.add_middleware(ParserDiagnosticsMiddleware())
mcp.add_middleware(ParameterRepairMiddleware())

_TOOL_ANNOTATIONS = {"readOnlyHint": True, "openWorldHint": False}

# convert_session writes new transcript copies (never modifying the source) — not
# read-only, but not destructive either: it only adds files.
_CONVERT_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": False,
    "openWorldHint": False,
}

# delete_conversions removes conversion artifacts from disk — destructive.
_DELETE_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "openWorldHint": False,
}

# rewind_transcript truncates a conversion artifact IN PLACE — the discarded tail
# is gone, so it is destructive (and idempotent: re-running the same rewind on an
# already-rewound file is a no-op once the tail is shorter than the cut point).
_REWIND_ANNOTATIONS = {
    "readOnlyHint": False,
    "destructiveHint": True,
    "idempotentHint": True,
    "openWorldHint": False,
}


# Shared param: which projects to look in. Omit ⇒ ALL projects (cross-project) for
# the search/locate tools; list_project_sessions overrides this to default to CWD
# so it doesn't dump every session on disk.
ProjectsParam = Annotated[
    list[str] | None,
    Field(
        description=(
            "Projects to look in — each a path or a bare name (bare → ~/projects/<name>); "
            "worktrees are flattened into their repo automatically. Omit to look across ALL "
            "projects (cross-project recall). Pass the `project` value from a search/list "
            "result to scope to one."
        )
    ),
]

HarnessesParam = Annotated[
    list[str] | None,
    Field(
        description=(
            "Transcript harnesses to include: 'claude' and/or 'codex'. "
            "Omit to search every supported harness."
        )
    ),
]


def _load_all_sessions(
    projects: list[str] | None,
    harnesses: list[str] | None = None,
) -> tuple[list[SessionInfo], list[str]]:
    """Load sessions across the selected projects (omit/empty ⇒ all projects).

    Returns (sessions, resolved_project_paths). Discovery pools across git
    worktrees and de-duplicates by session id (Corpus.discover); every ref is
    then promoted, so this PARSES the whole selection. Only the inventory tool
    (list_project_sessions, CWD-scoped by default) should use it — scoped tools
    narrow to refs first and promote only what they need.
    """
    proj_paths = resolve_projects(projects)
    sessions = promote_refs(Corpus.discover(proj_paths, harnesses=harnesses).refs)
    sort_sessions_newest_first(sessions)
    return sessions, proj_paths


def _resolve_session(
    session: str,
    projects: list[str] | None,
    harnesses: list[str] | None = None,
) -> SessionInfo:
    """Resolve a session id/prefix and promote exactly that one session.

    The common path for session-keyed tools: discovery is filename-only
    (Corpus.discover + narrow_to_ids — no transcript parse), ambiguity raises
    with the candidate projects listed, and only the resolved ref is parsed.
    """
    corpus = Corpus.discover(projects, harnesses=harnesses).narrow_to_ids([session])
    ref = resolve_unique_ref(corpus.refs, session)
    info = SessionInfo.load(ref)
    if info is None:
        raise ToolError(f"No session matching: {session}")
    return info


def _resolve_browsable_artifact(
    session: str, corpus: Corpus
) -> tuple[PrefixId, Path, str | None, str | None] | None:
    """Resolve an agent id to (id, transcript path, project, worktree).

    `browse_session` resolves real SESSIONS by ref, but a convert_session
    artifact is often a SUBAGENT whose id names no session file — and
    rewind_transcript points users at browse_session to read the cut turn off
    the artifact. So when the id isn't a session we fall through to here: the
    same filename-only resolver rewind/delete use (`Corpus.narrow_to_artifact_ids`
    + `resolve_artifacts`, NO transcript parse), which resolves the id to a
    subagent transcript path — the identity fields browse_session needs, so the
    agent transcript browses exactly like a session. Returns None when the id
    resolves to no subagent (the caller keeps the original "no session
    matching" error).
    """
    narrowed = corpus.narrow_to_artifact_ids([session])
    _, kind, full_id, path = resolve_artifacts([session], narrowed.refs)[0]
    if kind != "subagent" or path is None:
        return None
    # The PARENT session holding this agent: its transcript dir (`<id>.jsonl`
    # without the suffix → `<encoded>/<id>`) is an ancestor of the agent file
    # (`<encoded>/<id>/subagents/agent-*.jsonl`). Match on that session dir, not
    # `r.path.parent` (the encoded PROJECT dir), which every session in the project
    # shares — so we surface the holding session's own project AND worktree.
    holding = next(
        (r for r in narrowed.refs if r.path.with_suffix("") in path.parents), None
    )
    return (
        PrefixId(full_id),
        path,
        holding.project_path if holding else None,
        holding.worktree if holding else None,
    )


def _filter_by_date(
    sessions: list[SessionInfo],
    after: datetime | None,
    before: datetime | None,
) -> list[SessionInfo]:
    """Filter sessions by date range. Naive datetimes treated as UTC."""
    if after:
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)
        sessions = [s for s in sessions if s.first_timestamp and s.first_timestamp >= after]
    if before:
        if before.tzinfo is None:
            before = before.replace(tzinfo=timezone.utc)
        sessions = [s for s in sessions if s.first_timestamp and s.first_timestamp <= before]
    return sessions


def _current_session_id() -> str | None:
    """The Claude Code or Codex session that launched this MCP server, if known.

    Claude Code spawns a dedicated stdio MCP server per session and injects
    CLAUDE_CODE_SESSION_ID into that process's environment — undocumented but
    observed directly: each live session's server carries its own distinct id
    (confirmed via /proc/<pid>/environ across concurrent sessions). We read it
    so broad discovery tools can drop the *calling* conversation from results:
    the session doing the searching is the one thing it never wants back.

    Returns None when the var is absent (orphaned server, or one launched
    outside a session), in which case nothing is excluded. The value is frozen
    at process spawn; since the server is per-session that's the right value
    for its whole life, save the rare case where one server outlives an
    in-process session switch (`/clear`, resume) — a low-harm miss, never a
    wrong result.
    """
    return (
        os.environ.get("CLAUDE_CODE_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CODEX_SESSION_ID")
        or None
    )


def _current_claude_session_id() -> str | None:
    """Claude caller identity for Claude-only conversion lifecycle tools."""
    return os.environ.get("CLAUDE_CODE_SESSION_ID") or None


def _exclude_current_session(
    sessions: list[SessionInfo], include_current: bool
) -> tuple[list[SessionInfo], PrefixId | None]:
    """Drop the calling session from a list unless the caller opted to keep it.

    Returns (kept_sessions, excluded_id). excluded_id is set only when a
    session was actually removed, so callers can surface *why* an expected
    result is missing instead of omitting it silently.
    """
    if include_current:
        return sessions, None
    current = _current_session_id()
    if not current:
        return sessions, None
    kept = [s for s in sessions if s.session_id != current]
    if len(kept) == len(sessions):
        return sessions, None  # calling session wasn't in this list anyway
    return kept, PrefixId(current)


# Shared description for the errors_only filter. One wording, three tools.
_ERRORS_ONLY_DESC = (
    "Restrict hits to tool calls that FAILED (the transcript's own `is_error` "
    "flag), so you never have to guess what an error said. The pattern then "
    "narrows the TOPIC rather than the error vocabulary: patterns=['ssh'] with "
    "errors_only=true finds every failed ssh-related call, no matter how it "
    "phrased the failure. Only tool results can fail, so `role` no longer "
    "affects what matches (it still governs the surrounding context turns), and "
    "hide='outputs' is incompatible. For the no-pattern view — every failure in "
    "a window, classified and counted — use survey_failures."
)


def _parse_hide_or_raise(value: str | None) -> frozenset[str]:
    """parse_hide that converts ValueError to ToolError for MCP entry points.

    Lives here (not in models.py) to keep FastMCP exception types out of the
    pure-data layer. Every display tool that accepts `hide` needs this.
    """
    try:
        return parse_hide(value)
    except ValueError as e:
        raise ToolError(str(e))


def _check_errors_only(errors_only: bool, hide: frozenset[str]) -> None:
    """Refuse errors_only together with hide='outputs' — they cancel out.

    A failure only ever lives in a tool result, so hiding tool results while
    asking for failures can only return nothing. Say so rather than returning a
    confident empty answer the caller will read as "no failures here."
    """
    if errors_only and "outputs" in hide:
        raise ToolError(
            "errors_only=true is incompatible with hide='outputs': failures only "
            "appear in tool results, so hiding them leaves nothing to match. Drop "
            "'outputs' from `hide`."
        )


# UUIDs use hex digits and hyphens. The first 8 chars (the prefix form returned
# by grep_session) are pure hex. Anything else is a hallucination — usually a
# unix timestamp or a random word the model grabbed from a pipe-delimited line.
_TURN_ID_PATTERN = re.compile(r"^[0-9a-f]{8}(-[0-9a-f]{4,12})*$")


def _validate_turn_id(turn: str) -> None:
    """Reject empty or obviously-not-a-UUID turn values at the MCP boundary.

    Catches two real bugs from production: agents passing turn="" and agents
    passing the unix-timestamp field (e.g. "1775406360") instead of the
    actual turn UUID. The first 8 chars of a UUID are hex; a 10-digit
    decimal timestamp fails this regex on the very first character.
    """
    if not turn:
        raise ToolError("turn must be a non-empty UUID or 8+ char prefix")
    if not _TURN_ID_PATTERN.match(turn):
        raise ToolError(
            f"turn {turn!r} is not a valid UUID or prefix — expected hex digits "
            f"(e.g. 'a1b2c3d4'), not a timestamp or arbitrary string. "
            f"Grab the turn_id from the start of a pipe-delimited entry line."
        )


# =============================================================================
# Conversation tools
# =============================================================================


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_projects(harnesses: HarnessesParam = None) -> ProjectListResponse:
    """List every project cc-explorer can see, one row per repo (worktrees flattened).

    The cross-project orientation step — like `ls` over `~/.claude/projects`, but
    de-duplicated so a repo with many git worktrees shows up once, keyed by its
    real path. Each row has the project path (pass it to the `projects` param of
    any other tool), a session count, and last-active time, sorted most-recent
    first. Use this when you're not sure which project holds a conversation, then
    search_projects to find the phrase or list_project_sessions to drill in.
    """
    projects = discover_projects(harnesses)
    if not projects:
        raise ToolError("No Claude Code or Codex transcript projects found")
    return ProjectListResponse.from_projects(projects)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_project_sessions(
    projects: Annotated[
        list[str] | None,
        Field(
            description="Projects to list sessions for — paths or bare names (worktrees flattened). Omit for the CURRENT project (CWD); pass projects to list those instead. (To enumerate projects themselves, use list_projects.)"
        ),
    ] = None,
    harnesses: HarnessesParam = None,
    min_messages: Annotated[
        int,
        Field(description="Only sessions with at least N messages."),
    ] = 4,
    min_tools: Annotated[
        int,
        Field(description="Only sessions with at least N tool calls."),
    ] = 0,
    min_agents: Annotated[
        int,
        Field(
            description="Only sessions with at least N subagents in their full discovered population (`agents_present` — direct dispatches plus workflow-orchestrated orphans, NOT the top-down `agents` count). Set min_agents=1 to find every session that ran subagents, including workflow-only ones — the entry point to the agent-forensics tools (list_session_agents, get_agent_detail, audit_session_tools)."
        ),
    ] = 0,
    after: Annotated[
        datetime | None,
        Field(description="Only sessions after this datetime."),
    ] = None,
    before: Annotated[
        datetime | None,
        Field(description="Only sessions before this datetime."),
    ] = None,
) -> SessionListResponse:
    """List conversations in a project with stats: dates, message counts, human prompts, token usage, tool calls, agent dispatches.

    This is the orientation step — like `ls -la` on the project's chat history. Use it to see what exists before searching. Each row carries its `project` plus two agent counts: `agents` (dispatched directly) and `agents_present` (the full population including workflow orphans) — a gap between them means the session orchestrated workflows. `user_turns` (human prompts) against a high message/agent count flags a single prompt that fanned out into a long autonomous run. Pass min_agents=1 to narrow to sessions that ran subagents — the starting point for agent forensics (then drill in with list_session_agents / get_agent_detail / audit_session_tools). The calling conversation, if present, is kept but flagged `is_current: true` so you can tell which row is the session you're in.

    Defaults to the CURRENT project (CWD). Pass `projects` to list one or more named projects instead; to enumerate the projects themselves use list_projects, and to find a conversation when you don't know its project use search_projects.
    """
    proj_sel = projects if projects else [resolve_project(None)]
    sessions, _ = _load_all_sessions(proj_sel, harnesses)
    if not sessions:
        raise ToolError(f"No conversations found for {', '.join(proj_sel)}")

    sessions = [s for s in sessions if s.message_count >= min_messages]
    sessions = [s for s in sessions if s.stats.tool_use_count >= min_tools]
    sessions = [s for s in sessions if s.agents_present >= min_agents]
    sessions = _filter_by_date(sessions, after, before)

    if not sessions:
        raise ToolError("No conversations match filters")

    return SessionListResponse.from_sessions(sessions, current_session=_current_session_id())


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def search_projects(
    patterns: Annotated[
        list[str],
        Field(
            description="Regex patterns to scan for (case-insensitive). Results grouped by pattern, sorted by hit count."
        ),
    ],
    projects: ProjectsParam = None,
    harnesses: HarnessesParam = None,
    role: Annotated[
        ConversationRole,
        Field(
            description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."
        ),
    ] = ConversationRole.user,
    after: Annotated[
        datetime | None,
        Field(description="Only search sessions after this datetime."),
    ] = None,
    before: Annotated[
        datetime | None,
        Field(description="Only search sessions before this datetime."),
    ] = None,
    excerpt_width: Annotated[
        int,
        Field(description="Character width of centered excerpt examples."),
    ] = 150,
    include_current_session: Annotated[
        bool,
        Field(
            description="Include the calling conversation itself. Default False — the live session that invoked this search is excluded so it can't return itself as a (useless) hit. Set True to search across it too."
        ),
    ] = False,
    errors_only: Annotated[
        bool,
        Field(description=_ERRORS_ONLY_DESC),
    ] = False,
) -> SearchProjectsResponse:
    """Scan chat history across one or many projects for patterns, grouped by pattern with hit counts and per-project/session breakdowns.

    This is the cross-project recall surface: omit `projects` to search EVERY project at once when you remember a conversation but not where it happened. Each example hit names the `project` (and the `agent`, if the hit was inside a subagent body) so you can switch to that project and drill in. Pass `projects` to scope to specific ones.

    Search is exhaustive by default and spans the whole corpus: conversation text, tool inputs (Bash commands, file paths, grep patterns), tool outputs, assistant thinking — and subagent transcripts, not just the main session. The pattern is the precision tool — use tight regex to narrow noisy searches.

    Pass all your candidate search terms at once — each gets its own hit count and breakdown so you can see which terms are useful. Use separate patterns rather than regex OR pipes (e.g. ["facebook.*scrape", "fb_capture"] not "facebook.*scrape|fb_capture"). Results are sorted by hit count (hottest first). Follow up with grep_session (scoped to the returned project) or get_agent_detail (for an `agent` hit).

    Set errors_only=true to search only FAILED tool calls — the pattern then narrows the topic instead of having to guess the error's wording.
    """
    proj_paths = resolve_projects(projects)
    corpus = Corpus.discover(proj_paths, harnesses=harnesses)
    if not corpus.refs:
        raise ToolError(f"No conversations found for: {', '.join(proj_paths) or '(no projects)'}")

    no_match_error = ToolError(
        f"No matches for: {', '.join(patterns)} across {len(proj_paths)} project(s)"
        + (" (errors_only)" if errors_only else "")
        + ". Patterns are case-insensitive regex — try shorter or broader terms, set "
        "role='all' to search both sides, or widen the date range."
    )

    # errors_only narrows the corpus BEFORE the pattern prefilter: `is_error` is
    # a literal in the raw JSONL, so it names the ~40% of files that can hold a
    # failure at all without parsing anything.
    if errors_only:
        corpus, _ = narrow_to_error_sessions(corpus)
        if not corpus.refs:
            raise no_match_error

    # Raw-byte prefilter: cost scales with the answer, not the corpus. The
    # candidate set is a SUPERSET of true hits (rg-unsafe patterns or scanner
    # failure fall back to scan-all); only candidates are parsed, and the typed
    # matcher below (triage_multi -> _entry_matches) remains matcher of record.
    candidates = corpus.candidate_refs(patterns)
    if not candidates:
        raise no_match_error

    sessions = promote_refs(candidates)
    sort_sessions_newest_first(sessions)

    sessions = _filter_by_date(sessions, after, before)
    sessions, excluded = _exclude_current_session(sessions, include_current_session)
    if not sessions and excluded:
        # The calling conversation was the only session in scope. Don't blame
        # the patterns — point at the exclusion and how to override it.
        raise ToolError(
            f"The only session in scope is the calling conversation ({excluded}), "
            f"excluded by default. Pass include_current_session=true to search it."
        )

    base_types = ENTRY_TYPE_MAP[role]

    all_results = triage_multi(
        sessions,
        patterns,
        base_types=base_types,
        example_width=excerpt_width,
        errors_only=errors_only,
    )

    # Check if anything matched
    if not any(r for _, results in all_results for r in results):
        raise no_match_error

    return SearchProjectsResponse.from_triage(
        all_results,
        projects_searched=len(proj_paths),
        excluded_current_session=excluded,
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def grep_session(
    session: Annotated[
        str,
        Field(
            description="Session ID or prefix. Required — use search_projects to find session IDs first."
        ),
    ],
    patterns: Annotated[
        list[str],
        Field(
            description="Regex patterns to search for (case-insensitive). Each gets its own hit count and match list. Use separate patterns rather than `|`-OR (e.g. ['fb_capture', 'facebook.*scrape'] not 'fb_capture|facebook.*scrape') so you can see which terms land and which are dead weight."
        ),
    ],
    projects: ProjectsParam = None,
    harnesses: HarnessesParam = None,
    context: Annotated[
        int,
        Field(
            description="Number of surrounding turns to include with each match (like grep -C). Use `context=N` to widen the radius around each hit.",
            ge=0,
            le=5,
        ),
    ] = 2,
    role: Annotated[
        ConversationRole,
        Field(
            description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."
        ),
    ] = ConversationRole.user,
    limit: Annotated[
        int,
        Field(
            description="Max match blocks to return per pattern (like head -N). Each pattern is capped independently so a noisy term can't drown out productive ones."
        ),
    ] = 15,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs/outputs) to N chars. 0 = full content. The `match` line in each block is centered on the pattern hit so mid-entry matches stay visible.",
        ),
    ] = 500,
    hide: Annotated[
        str | None,
        Field(
            description="Comma-separated assistant-turn content to suppress from both search and display. Atoms: 'inputs' (tool calls), 'outputs' (tool results), 'thinking' (reasoning blocks). Default empty = search and show everything. Text is always visible and is not an atom.",
        ),
    ] = None,
    errors_only: Annotated[
        bool,
        Field(description=_ERRORS_ONLY_DESC),
    ] = False,
) -> GrepSessionResponse:
    """Show matches for one or more patterns within a single conversation, with surrounding context.

    Like `rg -C3` on a single file, but pattern-centric: pass all your candidate terms in one call and each gets its own hit count and match blocks. Zero-hit patterns are kept in the response so you see them as dead weight rather than guessing why they're missing.

    Search is exhaustive by default across text, tool inputs, tool outputs, and thinking blocks. Each entry includes its full character length so you can gauge size before calling read_turn.

    Match blocks have three fields: `before` (context turns before), `match` (the matching entry, excerpted on the hit so it stays visible even when truncated), and `after` (context turns after).

    Set errors_only=true to see only the FAILED tool calls in this session — the drill-in after survey_failures names a hot session.
    """
    if not patterns:
        raise ToolError("patterns must contain at least one pattern")

    hide_set = _parse_hide_or_raise(hide)
    _check_errors_only(errors_only, hide_set)
    target = _resolve_session(session, projects, harnesses)

    base_types = ENTRY_TYPE_MAP[role]

    # Single-pass over the session's entries — search_multi checks every
    # pattern per entry instead of re-walking the transcript per pattern.
    multi_results = search_multi(
        [target],
        patterns,
        base_types=base_types,
        context=context,
        max_results_per_pattern=limit,
        hide=hide_set,
        errors_only=errors_only,
    )
    pattern_results = multi_results[target.session_id]

    if not any(matches for _, matches, _ in pattern_results):
        scope = " among failed tool calls" if errors_only else ""
        raise ToolError(
            f"No matches for any pattern{scope}: {', '.join(patterns)}. Try shorter "
            f"or broader regex, set role='all', or use browse_session to read the "
            f"session directly."
        )

    return GrepSessionResponse.from_pattern_results(
        session_id=target.session_id,
        results=pattern_results,
        truncate=truncate,
        hide=hide_set,
        worktree=target.worktree,
        project=target.project_path,
        harness=target.harness,
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def grep_sessions(
    sessions: Annotated[
        list[str],
        Field(
            description="Session IDs or prefixes to search. Required — use search_projects to find which sessions are hot, then fan out across them in one call instead of looping grep_session."
        ),
    ],
    patterns: Annotated[
        list[str],
        Field(
            description="Regex patterns to search for (case-insensitive). Each gets its own hit count and match list per session. Use separate patterns rather than `|`-OR so you can see which terms land."
        ),
    ],
    projects: ProjectsParam = None,
    harnesses: HarnessesParam = None,
    context: Annotated[
        int,
        Field(
            description="Number of surrounding turns to include with each match (like grep -C).",
            ge=0,
            le=5,
        ),
    ] = 2,
    role: Annotated[
        ConversationRole,
        Field(
            description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."
        ),
    ] = ConversationRole.user,
    limit: Annotated[
        int,
        Field(
            description="Max match blocks to return per pattern per session. Each (session, pattern) cell is capped independently."
        ),
    ] = 10,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece to N chars. 0 = full content. The `match` line in each block is centered on the pattern hit so mid-entry matches stay visible.",
        ),
    ] = 500,
    hide: Annotated[
        str | None,
        Field(
            description="Comma-separated assistant-turn content to suppress. Atoms: 'inputs', 'outputs', 'thinking'. Default empty.",
        ),
    ] = None,
    errors_only: Annotated[
        bool,
        Field(description=_ERRORS_ONLY_DESC),
    ] = False,
) -> GrepSessionsResponse:
    """Fan out grep across multiple sessions in one call.

    Use this when you've already identified your hot sessions (via `search_projects`) and want context blocks across all of them for the same patterns. One call replaces N `grep_session` calls.

    Returns one entry per session that had at least one match (zero-hit sessions are omitted). Each entry has the same shape as `grep_session` output: per-pattern hit counts and match blocks with surrounding context. Sort order preserves the order of `sessions`.

    Set errors_only=true to see only FAILED tool calls — the fan-out drill-in after survey_failures names several hot sessions.
    """
    if not sessions:
        raise ToolError("sessions must contain at least one session id")
    if not patterns:
        raise ToolError("patterns must contain at least one pattern")

    hide_set = _parse_hide_or_raise(hide)
    _check_errors_only(errors_only, hide_set)
    corpus = Corpus.discover(projects, harnesses=harnesses).narrow_to_ids(sessions)

    # Resolve each session prefix to a ref, preserving input order, and promote
    # only the resolved refs. Ambiguous prefixes (matching >1 distinct session
    # across the corpus) raise rather than silently picking one; clean misses
    # (and empty sessions) go to not_found.
    resolved: list[SessionInfo] = []
    not_found: list[str] = []
    for sid in sessions:
        ref = resolve_unique_ref_or_none(corpus.refs, sid)
        info = SessionInfo.load(ref) if ref is not None else None
        if info is None:
            not_found.append(sid)
        else:
            resolved.append(info)

    # All-prefix-failure is handled together with all-pattern-failure below,
    # so the caller gets one consistent error path.

    base_types = ENTRY_TYPE_MAP[role]

    # One single-pass walk per session, all patterns at once.
    multi_results = search_multi(
        resolved,
        patterns,
        base_types=base_types,
        context=context,
        max_results_per_pattern=limit,
        hide=hide_set,
        errors_only=errors_only,
    )

    session_responses: list[GrepSessionResponse] = []
    for sess in resolved:  # preserve caller-provided order
        pattern_results = multi_results.get(sess.session_id, [])
        if not any(matches for _, matches, _ in pattern_results):
            continue
        session_responses.append(
            GrepSessionResponse.from_pattern_results(
                session_id=sess.session_id,
                results=pattern_results,
                truncate=truncate,
                hide=hide_set,
                worktree=sess.worktree,
                project=sess.project_path,
                harness=sess.harness,
            )
        )

    if not session_responses:
        # Distinguish two failure modes in the error text so the caller
        # can tell a typo (all prefixes unresolved) from a clean miss
        # (every prefix resolved but no patterns matched anything).
        #
        # Note: in the partial-resolve + all-miss case, `not_found` is
        # intentionally discarded — we raise the clean-miss ToolError so
        # the caller gets a single consistent "nothing to return" signal,
        # matching every other empty-result tool in this file. If you want
        # the typo diagnostic surfaced even on all-miss, switch this branch
        # to return an empty-sessions response with not_found populated.
        if not_found and len(not_found) == len(sessions):
            raise ToolError(f"No sessions matched: {', '.join(not_found)}")
        scope = " among failed tool calls" if errors_only else ""
        raise ToolError(
            f"No matches in any session{scope} for any pattern: {', '.join(patterns)}. "
            f"Try shorter or broader regex, set role='all', or confirm the session "
            f"ids with list_project_sessions."
        )

    return GrepSessionsResponse(
        sessions=session_responses,
        not_found=not_found or None,
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def read_turn(
    turn: Annotated[
        str,
        Field(
            description="Turn UUID or prefix to center on (from grep_session output)."
        ),
    ],
    session: Annotated[
        str | None,
        Field(
            description="Session ID or prefix. Optional — turn UUIDs are globally unique, but pass this to narrow the search explicitly.",
        ),
    ] = None,
    projects: ProjectsParam = None,
    harnesses: HarnessesParam = None,
    context: Annotated[
        int,
        Field(
            description="Number of turns before and after the anchor to include (radius). Use `context=N` to widen the window on each side.",
        ),
    ] = 3,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs/outputs) to N chars. 0 = full content. Bump this up when tool outputs are huge — volume lives here, not in `hide`.",
        ),
    ] = 0,
    hide: Annotated[
        str | None,
        Field(
            description="Comma-separated assistant-turn content to suppress from display. Atoms: 'inputs' (tool calls), 'outputs' (tool results), 'thinking' (reasoning blocks). Default empty = show everything. Text is always visible.",
        ),
    ] = None,
) -> ReadTurnResponse:
    """Read a specific moment in a conversation at full fidelity.

    Like `sed -n '450,470p'` — reads a specific section without pattern matching. Takes a turn UUID (from grep_session output) and returns the surrounding conversation.

    The `session` param is optional: turn UUIDs are globally unique across all sessions in a project, so passing just the turn is enough. Supply `session` only to disambiguate or for belt-and-suspenders clarity.

    Use the full_length values from grep_session to gauge entry sizes before reading. Use `context=N` to control the radius (turns on each side of the anchor).
    """
    _validate_turn_id(turn)
    hide_set = _parse_hide_or_raise(hide)

    target_session_id: str | None = None
    if session:
        # Session known → resolve and promote just that session.
        target = _resolve_session(session, projects, harnesses)
        sessions = [target]
        target_session_id = target.session_id
    else:
        # Only a turn id → it appears as a literal in exactly the raw JSONL
        # file(s) that contain the turn, so the prefilter finds the holding
        # session(s) without parsing the corpus. Turn ids are hex+hyphens; the
        # escaped literal is always prefilter-safe.
        corpus = Corpus.discover(projects, harnesses=harnesses)
        if not corpus.refs:
            raise ToolError("No conversations found")
        candidates = corpus.candidate_refs([re.escape(turn)])
        # Codex response items do not carry UUID turn ids.  The provider creates
        # stable UUIDv5 ids from their item identity, so those ids cannot hit the
        # raw-byte prefilter; include Codex refs for the typed matcher to resolve.
        candidate_keys = {(ref.harness, ref.session_id.full) for ref in candidates}
        candidates.extend(
            ref
            for ref in corpus.refs
            if ref.harness is Harness.codex
            and (ref.harness, ref.session_id.full) not in candidate_keys
        )
        sessions = promote_refs(candidates)
        if not sessions:
            raise ToolError(f"Turn {turn} not found")

    session_info, entries, agent_id = get_turn_context(
        sessions, turn, context, hide=hide_set, session_id=target_session_id
    )

    if not entries:
        raise ToolError(f"Turn {turn} not found")

    return ReadTurnResponse.from_entries(
        session_info, turn, entries, truncate=truncate, hide=hide_set, agent_id=agent_id
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def browse_session(
    session: Annotated[
        str,
        Field(
            description="Session ID or prefix — also accepts a convert_session artifact's agent id, so you can browse a converted subagent's transcript (e.g. to read a turn UUID off it before rewind_transcript). Use list_project_sessions / list_session_agents to find IDs."
        ),
    ],
    projects: ProjectsParam = None,
    harnesses: HarnessesParam = None,
    position: Annotated[
        str,
        Field(
            description="Which end to read: 'head' for the start, 'tail' for the end.",
        ),
    ] = "head",
    turns: Annotated[
        int,
        Field(
            description="Number of conversation turns to return (linear window from the position). Use `turns=N` to control window size.",
            ge=1,
            le=50,
        ),
    ] = 10,
    turn: Annotated[
        str | None,
        Field(
            description="Turn UUID to anchor on. With 'tail': read forward from this turn. With 'head': read up to this turn. Omit to read from actual start/end.",
        ),
    ] = None,
    role: Annotated[
        ConversationRole,
        Field(
            description="Which side to show: 'user' for human messages only, 'assistant' for agent responses only, 'all' for both.",
        ),
    ] = ConversationRole.all,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs/outputs) to N chars. 0 = full content. Bump this up when tool outputs are huge.",
        ),
    ] = 0,
    hide: Annotated[
        str | None,
        Field(
            description="Comma-separated assistant-turn content to suppress from display. Atoms: 'inputs' (tool calls), 'outputs' (tool results), 'thinking' (reasoning blocks). Default empty = show everything. Text is always visible.",
        ),
    ] = None,
) -> BrowseSessionResponse:
    """Read the first or last N turns of a conversation — like head/tail on a session.

    Quick orientation tool: see how a conversation started or where it ended up without needing a search pattern. Use 'head' to understand what the session was about, 'tail' to see the conclusion. Pass a turn UUID to anchor and paginate through a session.

    `turns=N` controls how many turns to return from the position (a linear window). This differs from `read_turn` and `grep_session` where `context=N` means a radius around an anchor.
    """
    hide_set = _parse_hide_or_raise(hide)
    if position not in ("head", "tail"):
        raise ToolError(f"position must be 'head' or 'tail', got: {position!r}")

    # Session-first resolution: a real session id resolves by filename. Only
    # when the id names NO session do we fall through to the artifact resolver,
    # so a converted SUBAGENT's agent id browses like a session
    # (rewind_transcript points users here to read its cut turn). Both paths
    # are filename-only — browsing needs the transcript path and identity
    # fields, never a promoted SessionInfo.
    corpus = Corpus.discover(projects, harnesses=harnesses)
    ref = resolve_unique_ref_or_none(corpus.narrow_to_ids([session]).refs, session)
    if ref is not None:
        browse_id, browse_path = ref.session_id, ref.path
        project_path, worktree = ref.project_path, ref.worktree
        browse_source = ref
        browse_harness = ref.harness
    else:
        artifact = _resolve_browsable_artifact(session, corpus)
        if artifact is None:
            raise ToolError(f"No session matching: {session}")
        browse_id, browse_path, project_path, worktree = artifact
        browse_source = browse_path
        browse_harness = Harness.claude

    base_types = ENTRY_TYPE_MAP[role]
    entry_types = conversation_types_for(hide_set, base_types)

    entries, total = browse_session_turns(
        browse_source, position, turns, anchor_turn=turn, entry_types=entry_types
    )

    if not entries:
        if turn:
            raise ToolError(f"Turn {turn} not found in session {session}")
        raise ToolError(f"Session {session} has no conversation turns")

    return BrowseSessionResponse.from_entries(
        session_id=browse_id,
        position=position,
        entries=entries,
        total=total,
        truncate=truncate,
        anchor=turn,
        hide=hide_set,
        worktree=worktree,
        project=project_path,
        harness=browse_harness,
    )


# =============================================================================
# Agent inspection tools
# =============================================================================


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_session_agents(
    session: Annotated[str, Field(description="Session ID or prefix to inspect.")],
    projects: ProjectsParam = None,
    task_output_dir: Annotated[
        str | None,
        Field(description="Directory containing saved .output files."),
    ] = None,
) -> SessionAgentsResponse:
    """List every subagent a session ran — type, status, token cost, duration, and whether its full record is available.

    Includes agents spawned by a workflow, not just ones the conversation dispatched directly, so the count reflects the session's real fan-out. Each row's `source` tells you whether to trust missing fields — and `workflow_run_id` lets you group agents from the same workflow run.

    Use when you want to see a session's fan-out before drilling in: which agents ran, which errored, which burned the most tokens. Step two of agent forensics — get a session id from list_project_sessions(min_agents=1), then from here pass an agent_id to get_agent_detail for the full prompt/result/trace, or audit the whole session's tool usage with audit_session_tools.
    """
    target = _resolve_session(session, projects)

    agents = discover_subagents(target.path)

    output_dir = Path(task_output_dir).expanduser() if task_output_dir else None
    resolve_output_files(agents, output_dir)
    scan_output_file_stats(agents)

    return SessionAgentsResponse.from_session(target, agents)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def get_agent_detail(
    agent_ids: Annotated[
        list[str],
        Field(description="Agent ID(s) or prefixes to inspect."),
    ],
    projects: ProjectsParam = None,
    session: Annotated[
        str | None,
        Field(description="Session ID prefix to narrow the search."),
    ] = None,
    task_output_dir: Annotated[
        str | None,
        Field(description="Directory containing saved .output files."),
    ] = None,
    trace: Annotated[
        bool,
        Field(description="Show chronological tool call trace from output file."),
    ] = False,
    no_reasoning: Annotated[
        bool,
        Field(description="Omit reasoning text from trace output."),
    ] = False,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs) to N chars. 0 = full content.",
        ),
    ] = 80,
) -> AgentDetailResponse | AgentListResponse:
    """Get one or more subagents' full story: the prompt they were given, the result they returned, token/tool stats, and (with trace=true) a chronological tool-by-tool timeline of what they did.

    Works for any agent list_session_agents returns, including ones a workflow spawned rather than the conversation requesting directly. Find agent_ids with list_session_agents.

    Use when you need what an agent was actually told and how it reached its answer — debugging why an agent went off the rails, recovering a result that scrolled out of context, or comparing what several parallel agents concluded. For a session-wide view of whether agents used their tools correctly (rather than one agent's full transcript), use audit_session_tools instead.
    """
    if session:
        # Session known → resolve and promote just that session.
        sessions = [_resolve_session(session, projects)]
    else:
        # Agent ids only → find the holding session(s) by filename (an agent id
        # matches its transcript's filename under some session's subagents dir)
        # and promote only those, instead of parsing every selected project.
        short = [a for a in agent_ids if len(a) < MIN_ID_LEN]
        if short:
            raise ToolError(
                f"Agent id(s) too short (<{MIN_ID_LEN} chars): {', '.join(short)} — "
                f"pass at least {MIN_ID_LEN} chars, or scope with `session` to "
                f"search within one session."
            )
        corpus = Corpus.discover(projects).narrow_to_artifact_ids(agent_ids)
        sessions = promote_refs(corpus.refs)
        if not sessions:
            raise ToolError(f"Agent(s) not found: {', '.join(agent_ids)}")

    output_dir = Path(task_output_dir).expanduser() if task_output_dir else None

    details: list[AgentDetailResponse] = []
    not_found: list[str] = []
    for aid in agent_ids:
        found, found_session = _find_agent(sessions, aid)
        if not found or not found_session:
            not_found.append(aid)
            continue

        resolve_output_files([found], output_dir)
        entries_map = scan_output_file_stats([found], keep_entries=trace)

        details.append(
            AgentDetailResponse.from_subagent(
                found,
                found_session,
                trace=trace,
                no_reasoning=no_reasoning,
                entries_map=entries_map,
                truncate=truncate,
            )
        )

    if not details:
        raise ToolError(f"Agent(s) not found: {', '.join(not_found)}")

    if len(details) == 1:
        return details[0]
    return AgentListResponse(agents=details)


def _find_agent(sessions, agent_id: str):
    """Search for an agent across sessions by ID prefix."""
    for s in sessions:
        agents = discover_subagents(s.path)
        for sa in agents:
            if matches_id(sa, agent_id):
                return sa, s
    return None, None


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def audit_session_tools(
    session: Annotated[
        str,
        Field(description="Session ID or prefix to audit."),
    ],
    projects: ProjectsParam = None,
    tool_name_filter: Annotated[
        str | None,
        Field(
            description="Substring filter applied to tool names — e.g. 'cc-explorer' to show only cc-explorer calls. Omit to include every tool. Per-tool counts in `tool_counts` are NOT filtered (so you still see what each agent used overall)."
        ),
    ] = None,
    task_output_dir: Annotated[
        str | None,
        Field(description="Directory containing saved .output files."),
    ] = None,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each tool input summary and error message to N chars.",
            ge=20,
        ),
    ] = 80,
) -> SessionToolAuditResponse:
    """Audit how every subagent in a session used its tools — the whole-session view, vs get_agent_detail's single-agent deep dive.

    Covers agents spawned by a workflow, not just ones the conversation dispatched directly, so a workflow's fan-out is audited rather than silently missing from the picture. `total_present` is how many agents the session ran; `total_audited` is how many could be inspected — a gap means some agents left no inspectable record. For each agent, returns tool counts, error rate, and a chronological list of tool calls (optionally filtered by name substring via tool_name_filter); each call includes timestamp, tool name, truncated input args, and an error flag set when the tool result was an error or zero-match response.

    Use this to answer 'are my agents using my tools right?' — which tools land vs fail, where retries happened, which agents over-call, and (with tool_name_filter='your-server') whether agents even reached for a specific MCP tool you shipped or ignored it. Get the session id from list_project_sessions(min_agents=1).
    """
    target = _resolve_session(session, projects)

    agents = discover_subagents(target.path)
    if not agents:
        raise ToolError(f"Session {session} dispatched no subagents")

    total_present = len(agents)

    output_dir = Path(task_output_dir).expanduser() if task_output_dir else None
    resolve_output_files(agents, output_dir)
    entries_map = scan_output_file_stats(agents, keep_entries=True)

    audits: list[AgentToolAudit] = []
    total_calls = 0
    total_errors = 0
    for sa in agents:
        # Conversion artifacts are copies of real transcripts — skip them so
        # their tool calls (which are the source's) are not counted twice.
        if sa.is_conversion_artifact:
            continue
        if not sa.agent_id or sa.agent_id not in entries_map:
            continue

        entries = entries_map[sa.agent_id]
        calls, tool_counts, error_count = extract_agent_tool_audit(
            entries, tool_name_filter=tool_name_filter, truncate=truncate
        )

        agent_total = sum(tool_counts.values())
        total_calls += agent_total
        total_errors += error_count

        audits.append(
            AgentToolAudit(
                agent_id=sa.agent_id,
                source=sa.source,
                workflow_run_id=sa.workflow_run_id,
                type=sa.subagent_type or "",
                description=sa.description or "",
                tool_call_count=agent_total,
                error_count=error_count,
                tool_counts=tool_counts,
                calls=[AgentToolCall(**c) for c in calls],
            )
        )

    # When total_present > 0 but total_audited == 0, return the response anyway
    # with empty agents — that asymmetry IS the signal the
    # total_present/total_audited fields exist to surface. Only the
    # zero-present case (handled above) raises ToolError, since there genuinely
    # isn't anything to report on.

    return SessionToolAuditResponse(
        session=PrefixId(target.session_id),
        project=target.project_path,
        worktree=target.worktree,
        title=target.title,
        total_present=total_present,
        total_audited=len(audits),
        total_tool_calls=total_calls,
        total_errors=total_errors,
        tool_name_filter=tool_name_filter,
        agents=audits,
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def get_activity_timeline(
    projects: ProjectsParam = None,
    after: Annotated[
        datetime | None,
        Field(description="Window start, inclusive. Naive datetimes are read in `tz`. Default: 7 days before `before`."),
    ] = None,
    before: Annotated[
        datetime | None,
        Field(description="Window end, exclusive (half-open [after, before)). Naive datetimes are read in `tz`. Default: now."),
    ] = None,
    bucket_minutes: Annotated[
        int,
        Field(description="Grid grain in minutes. The unit for every *_min field and the timeline bucket size."),
    ] = 5,
    tz: Annotated[
        str | None,
        Field(description="IANA tz name (e.g. 'America/Los_Angeles'). ALL day/hour bucketing and displayed timestamps use it. Omit for system local time."),
    ] = None,
) -> ActivityTimelineResponse:
    """Reconstruct cross-project attention over a time window: a bucket_minutes-grain grid (default 5 min) of turn counts plus pre-computed attention rollups, for analyzing how the fleet was driven.

    Answers 'what did my week of agent-driving actually look like' — how many sessions ran at once, when attention peaked, how much was hands-on vs autonomous machine-time, per project and per day. Omit `projects` to span EVERY project (cross-project attention is the point); pass `projects` to scope. Every session `id` and `project` returned can be passed straight back to read_turn / grep_session / list_session_agents to drill into a specific moment.

    The payload leads with rollups (summary, by-project, per-day) and ends with the per-session list and the sparse time-major grid, so the actionable aggregates survive truncation. Read the output schema for exact field definitions — key ones: a *human turn* is a non-interrupt user message; `interrupts` count mid-turn esc stops (a neutral fact); `turn_min` sums the harness's turn_duration records and is a FLOOR (interrupted turns emit none); subagent activity folds into its parent as agent turns. Agent-team worker sessions (non-null `team`/`team_role`) have user-role turns that are mostly INJECTED BY TEAMMATES (orchestration), not human-typed — those are counted as agent activity, not human turns; `summary.interactive.team_sessions` flags how many such sessions are in the window. Headless (sdk-cli) sessions are machine work — present in the sessions list and timeline grid, but excluded from every interactive attention rollup (active_min, multitask, peaks, the day arrays).
    """
    result = build_activity_timeline(
        projects=projects,
        after=after,
        before=before,
        bucket_minutes=bucket_minutes,
        tz=tz,
    )
    return ActivityTimelineResponse.model_validate(result)


def _parse_kinds_or_raise(value: str | None) -> list[FailureKind] | None:
    """parse_kinds that converts ValueError to ToolError for MCP entry points.

    Same split as `_parse_hide_or_raise`: the parser lives in models.py beside
    FailureKind and is unit-testable off the boundary; this is only the
    exception translation.
    """
    try:
        return parse_kinds(value)
    except ValueError as e:
        raise ToolError(str(e))


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def survey_failures(
    projects: ProjectsParam = None,
    after: Annotated[
        datetime | None,
        Field(description="Only failures at or after this datetime (naive read as UTC). Strongly recommended — it also lets the scan skip transcripts untouched since then, so a windowed survey is far cheaper than an all-time one."),
    ] = None,
    before: Annotated[
        datetime | None,
        Field(description="Only failures strictly before this datetime (naive read as UTC)."),
    ] = None,
    # Declared `str` with a None default ON PURPOSE, not `str | None`: a union
    # makes Pydantic emit an `anyOf` that Claude clients render as type
    # "unknown" instead of "string". MCP clients omit absent optionals rather
    # than sending null, so None never reaches Pydantic validation.
    kinds: Annotated[
        str,
        Field(description="Comma-separated failure kinds to keep, e.g. 'network,auth_failed,timeout' to hunt real breakage, or 'unclassified' to see only what the taxonomy could not name. Omit for all kinds. Naming 'cascade' here implies include_cascade=true — otherwise the default suppression would make that filter unanswerable. The full kind list (and what each means) is in the by_kind output schema."),
    ] = None,  # pyright: ignore[reportArgumentType]
    include_cascade: Annotated[
        bool,
        Field(description="Fold sibling-cancellation artifacts back into the counts. Default False: when one call in a parallel batch fails, the harness cancels its siblings and records each cancellation as its own error, so leaving them in multiplies that batch's apparent failure count. The suppressed total is always reported as `cascade_suppressed`."),
    ] = False,
    examples: Annotated[
        bool,
        Field(description="Attach a representative error excerpt to each kind and each unclassified shape. Default False, because error text is the expensive part of this payload and a first orienting call rarely needs it — turn it on once the counts tell you which kind or shape to look at."),
    ] = False,
    limit: Annotated[
        int,
        Field(description="Max rows per section (by_kind, unclassified, by_tool, by_session). Anything dropped is reported in that section's `*_overflow` field, never silently.", ge=1, le=200),
    ] = 10,
) -> SurveyFailuresResponse:
    """Find every failed tool call in a window, classified and counted — without knowing what the errors say.

    The entry point for "what broke?". Failure is otherwise not a queryable axis: you can slice transcripts by project, session, agent, role, pattern and date, but to reach a failure you have to guess its prose and regex for it. This reads the transcript's own `is_error` flag instead, so nothing depends on guessing vocabulary.

    Read the payload in this order. `total` / `sessions_affected` size the problem. `by_kind` splits it by what went wrong AND by `category` — most failure volume is category 'agent' (the model called a tool wrong and recovered), so filter to category 'environment' in your head when you are hunting real breakage. `unclassified` is the yield: recurring failure shapes no rule anticipated, ranked by how often they recurred. `by_tool` and `by_session` are drill-in targets.

    Then drill in with the tools you already have: grep_session / grep_sessions with errors_only=true on a hot session, audit_session_tools for one session's per-agent tool usage, read_turn for a specific moment. This tool orients; those read.

    Cost scales with the answer: only transcripts whose raw bytes contain a flagged error are parsed, and with `after` set, only those written since. Note the denominator scope — `by_tool.calls` counts calls in the SCANNED transcripts (the ones with at least one error), not corpus-wide, so read it as relative failure density between tools rather than an absolute reliability rate.
    """
    survey = run_failure_survey(
        projects=projects,
        after=after,
        before=before,
        kinds=_parse_kinds_or_raise(kinds),
        include_cascade=include_cascade,
    )
    if survey.total == 0:
        window = ""
        if after or before:
            window = f" in [{after or '...'}, {before or '...'})"
        # Name the remedy that is actually true. Suggesting "widen the window"
        # when N cascades were found and suppressed sends the caller chasing a
        # window that was never the problem.
        if survey.cascade_suppressed:
            remedy = (
                f"{survey.cascade_suppressed} sibling-cancellation artifact(s) WERE "
                f"found and suppressed — pass include_cascade=true to see them."
            )
        else:
            remedy = (
                "Widen the window, drop the `kinds` filter, or omit `projects` to "
                "survey every project."
            )
        raise ToolError(
            f"No failed tool calls found{window} across "
            f"{survey.sessions_scanned} scanned session(s)"
            + (f" for kinds={kinds}" if kinds else "")
            + ". "
            + remedy
        )
    return SurveyFailuresResponse.from_survey(survey, limit=limit, examples=examples)


# =============================================================================
# Conversion tools — copy a session into a subagent, or a subagent into a session
# =============================================================================


def _resolve_session_ref_for_convert(
    src_id: str, src_project: str | None
) -> SessionRef:
    """Resolve a session id/prefix to one SessionRef for conversion, or raise.

    Conversion reads the raw file itself, so resolution needs only filename
    identity (id, path, project) — no transcript parse at all. Scopes to
    `src_project` when given; otherwise searches all projects. Ambiguous
    prefixes raise with the candidate projects listed.
    """
    projects = [src_project] if src_project else None
    corpus = Corpus.discover(projects).narrow_to_ids([src_id])
    return resolve_unique_ref(corpus.refs, src_id)


def _source_stats(path: Path) -> TranscriptStats:
    """Token/compaction stats for a conversion SOURCE transcript.

    Conversion operates on raw JSONL and never parses the source typed, but the
    caller of a conversion needs the source's headroom facts (context fill,
    compactions) to plan how it interrogates the copy. Reads through the shared
    transcript cache, so a source already listed or searched this session costs
    nothing extra. Works for either direction — sessions and subagent files share
    the transcript format.
    """
    return TranscriptStats.from_entries(load_transcript(path))


def _resolve_agent_for_convert(src_id: str, src_project: str | None):
    """Resolve an agent id/prefix to (AgentFile, holding SessionRef), or raise.

    Narrows to the holding session(s) by filename FIRST (`narrow_to_artifact_ids`
    — a pure glob), then walks only those sessions' subagents dirs. A prefix
    matching agent files in more than one distinct full id raises with the
    holding projects listed, mirroring session-prefix ambiguity handling. No
    transcript is ever parsed.
    """
    proj_sel = [src_project] if src_project else None
    corpus = Corpus.discover(proj_sel).narrow_to_artifact_ids([src_id])

    matches: list[tuple] = []  # (AgentFile, SessionRef)
    for r in corpus.refs:
        for af in collect_agent_files(resolve_subagents_dir(r.path)):
            if af.agent_id and PrefixId(af.agent_id) == src_id:
                matches.append((af, r))

    if not matches:
        raise ToolError(f"No subagent matching: {src_id}")
    distinct = {af.agent_id for af, _ in matches}
    if len(distinct) > 1:
        where = ", ".join(sorted({r.project_path or "?" for _, r in matches}))
        raise ToolError(
            f"Agent prefix {src_id!r} is ambiguous — it matches {len(distinct)} "
            f"distinct agents (in: {where}). Pass a longer id or scope with src_project."
        )
    return matches[0]


def _project_dirs_for(project_path: str) -> list[Path]:
    """Every encoded ~/.claude/projects dir that pools into a project.

    Title-collision detection must span the whole project (all worktrees), so we
    reuse load_conversations' worktree pooling and collect the parent dirs of the
    discovered transcript files.
    """
    refs = load_conversations(project_path)
    dirs: set[Path] = set()
    for ref in refs.values():
        dirs.add(ref.path.parent)
    return sorted(dirs)


@mcp.tool(annotations=_CONVERT_ANNOTATIONS)
def convert_session(
    direction: Annotated[
        Literal["session_to_subagent", "subagent_to_session"],
        Field(description="Which way to convert: 'session_to_subagent' copies a session into a resumable subagent; 'subagent_to_session' copies a subagent out to a top-level session."),
    ],
    src_id: Annotated[
        str,
        Field(description="Source id or prefix — a session id (session_to_subagent) or an agent id (subagent_to_session). Must resolve uniquely; an ambiguous prefix errors with the candidates listed."),
    ],
    src_project: Annotated[
        str | None,
        Field(description="Optional single project (path or bare name) to scope source resolution. Default: search all projects."),
    ] = None,
    dest_parent_session: Annotated[
        str | None,
        Field(description="session_to_subagent only: the session to parent the new subagent under. Default: the calling session. Errors if omitted and the calling session is unknown."),
    ] = None,
    dest_title: Annotated[
        str | None,
        Field(description="subagent_to_session only: custom title for the new session. Default: 'converted-<first 8 of src_id>'. Must be unique among custom titles in the dest project; a collision errors."),
    ] = None,
    dest_project: Annotated[
        str | None,
        Field(description="subagent_to_session only: project (path or bare name) to write the new session into. Default: the source's own project."),
    ] = None,
) -> ConvertSessionResponse:
    """Convert a session into a subagent, or a subagent into a session. Always a copy — the source transcript is never modified, moved, or deleted.

    direction='session_to_subagent': copy a session (any project) into a new subagent parented under dest_parent_session (default: the calling session). The conversation continues as a subagent: resume it like any completed background agent — SendMessage with the returned created_id — and ask it what you need; its whole history is in its context window. Use this when grep answers aren't enough: questions of reasoning, synthesis, or meaning; summaries at any altitude; judgment calls briefed with new evidence; or using a past session as a domain expert whose context you don't want to rebuild. SendMessage does NOT require agent-teams — it resumes any background subagent by id, and a conversion artifact is one. If SendMessage is not in your toolset it is deferred, not absent: load it with ToolSearch query "select:SendMessage", then convert and resume as normal.

    direction='subagent_to_session': copy a subagent out to a top-level session a human can open — the response carries the exact `claude -r` command. Use when the user wants to read or continue an agent's run interactively.

    The response includes what you need to compose the first message: suggested_handoff (a converted conversation has no way to know its interlocutor changed — message senders are not labeled on the wire), the original environment (cwd and whether it still exists, git branch, Claude Code version, age), model history, turn count, and tail state. It also carries the source's headroom facts — source_context_tokens (how full its window already was, i.e. what each resume replays and how little room is left) and source_compactions (how much of its early memory is a summary rather than the original turns) — so you can size the interview before you start it. Conversion artifacts carry lineage, are excluded from search, and are labeled in agent listings; remove them with delete_conversions."""
    if direction == "session_to_subagent":
        src = _resolve_session_ref_for_convert(src_id, src_project)

        parent_id = dest_parent_session or _current_claude_session_id()
        if not parent_id:
            raise ToolError(
                "dest_parent_session is required: the calling session is unknown "
                "(CLAUDE_CODE_SESSION_ID is not set), so there is no default parent. "
                "Pass the session id to parent the new subagent under."
            )

        # The parent session's on-disk directory is <projectDir>/<parentSessionId>.
        # Resolve it from the parent session itself so worktree-pooled parents land
        # in the right encoded dir (not assumed to be the source's project).
        parent = _resolve_session_ref_for_convert(parent_id, None)
        parent_session_dir = parent.path.with_suffix("")

        # Subagents the SOURCE session ran are NOT copied — their results already
        # appear inline. Report the count so the caller knows context was folded in.
        # Conversion artifacts are excluded — they are copies, not dispatched runs.
        nested = sum(
            1 for sa in discover_subagents(src.path)
            if not sa.is_conversion_artifact
        )

        # Read the source's headroom facts BEFORE writing anything: a stats
        # failure after the write would strand an artifact whose created_id the
        # caller never receives, and so can never delete.
        src_stats = _source_stats(src.path)

        result = convert_session_to_subagent(
            src_session_id=src.session_id.full,
            src_path=src.path,
            src_project_path=src.project_path or "",
            dest_parent_session_id=parent.session_id.full,
            dest_parent_session_dir=parent_session_dir,
            nested_agents=nested,
        )
        return ConvertSessionResponse.from_result(result, src_stats)

    # subagent_to_session
    af, holding = _resolve_agent_for_convert(src_id, src_project)
    agent_full = af.agent_id

    dest_proj_path = (
        resolve_project(dest_project) if dest_project else (holding.project_path or "")
    )
    if not dest_proj_path:
        raise ToolError(
            "Cannot determine destination project — the source has no project path "
            "and dest_project was not given."
        )

    title = dest_title or f"converted-{agent_full[:8]}"

    # `claude --resume "<title>"` resolves by scanning custom-title lines; a
    # duplicate breaks resolution, so refuse a colliding title up front.
    taken = existing_custom_titles(_project_dirs_for(dest_proj_path))
    if title in taken:
        raise ToolError(
            f"Title {title!r} already exists in project {Path(dest_proj_path).name!r} — "
            f"`claude --resume` resolves by title, so it must be unique. Pass a distinct dest_title."
        )

    # Write into the same encoded dir the source's session lives in (the project's
    # main transcript dir), so the new session is discoverable under that project.
    dest_proj_dir = holding.path.parent if not dest_project else _dest_project_dir(dest_proj_path)

    # src_project_path is the HOLDING project (where the source subagent lives),
    # not the destination. This is what gets stamped into provenance/from.project
    # and _converted_from.project. The response.project continues to carry the
    # destination project (set on ConversionResult.project via the caller below).
    src_proj_path = holding.project_path or ""

    # Same ordering rule as the other direction: stats before the write, so a
    # stats failure can't leave a written session the caller was never told about.
    src_stats = _source_stats(af.path)

    try:
        result = convert_subagent_to_session(
            src_agent_id=agent_full,
            src_path=af.path,
            src_project_path=src_proj_path,
            dest_project_dir=dest_proj_dir,
            dest_title=title,
        )
    except FileExistsError as e:
        raise ToolError(str(e))
    # Override result.project to the destination (conversion.py sets it to
    # src_project_path; callers expect response.project == destination project).
    result.project = dest_proj_path
    return ConvertSessionResponse.from_result(result, src_stats)


def _dest_project_dir(project_path: str) -> Path:
    """The main-worktree encoded dir for a project, for writing a new session into.

    Reuses load_conversations' pooling to find where the project's main-worktree
    transcripts live (the first transcript with worktree=None). Falls back to the
    canonical encoded dir derived from the path when the project has no sessions
    yet.
    """
    refs = load_conversations(project_path)
    for ref in refs.values():
        if ref.worktree is None:
            return ref.path.parent
    # No main-worktree session on disk yet — derive the encoded dir from the path.
    from ._claude_paths import _canonicalize_path, _get_project_dir

    return _get_project_dir(_canonicalize_path(project_path))


def _resolve_artifact_for_rewind(
    src_id: str, src_project: str | None
) -> tuple[str, str, Path]:
    """Resolve a session-or-agent id to (kind, full_id, path) for rewind, or raise.

    Accepts EITHER a session id or an agent id (you rewind whatever a convert
    produced), scoped to `src_project` when given. Delegates to the shared
    artifact resolver (the same one delete_conversions uses), which enforces the
    minimum-id-length floor — an in-place mutation must not fire on a sloppy
    prefix — and raises on an ambiguous prefix with the candidates listed.

    Resolution is filename-only end to end (`narrow_to_artifact_ids` +
    `resolve_artifacts`): parsing every transcript just to resolve one id is a
    full-corpus read that times out the MCP call mid-mutation (and even one
    busy project's parse is multi-second). An id that matches no filename still
    flows through the resolver with an empty corpus, so it remains the single
    authority for both the too-short guard and the no-match message.
    """
    proj_sel = [src_project] if src_project else None
    corpus = Corpus.discover(proj_sel).narrow_to_artifact_ids([src_id])
    _, kind, full_id, path = resolve_artifacts([src_id], corpus.refs)[0]
    if not kind or path is None:
        raise ToolError(f"No session or subagent matching: {src_id}")
    return (kind, full_id, path)


@mcp.tool(annotations=_REWIND_ANNOTATIONS)
def rewind_transcript(
    src_id: Annotated[
        str,
        Field(description="The conversion artifact to rewind — a session id or an agent id (prefixes accepted, but must resolve uniquely and be at least 6 chars). Only artifacts created by convert_session can be rewound."),
    ],
    turn: Annotated[
        str,
        Field(description="Turn UUID (or prefix) to cut at — a user/assistant line in this transcript. Discover it by running browse_session / read_turn / grep_session against the artifact id itself (browse_session accepts a converted subagent's agent id). convert_session PRESERVES source turn UUIDs, so a turn id from the ORIGINAL session is also a valid cut point on the artifact."),
    ],
    cut: Annotated[
        Literal["after", "before"],
        Field(description="'after' (default): keep through the named turn — it becomes the new tail. 'before': discard the named turn and everything after it — use this to rewind to just before a user prompt so you can re-drive from there."),
    ] = "after",
    src_project: Annotated[
        str | None,
        Field(description="Optional single project (path or bare name) to scope source resolution. Default: search all projects."),
    ] = None,
) -> RewindTranscriptResponse:
    """Truncate a converted transcript IN PLACE at a chosen turn, discarding everything after — so the artifact resumes from that earlier point.

    For rewinding a converted session or subagent back to a known turn to replay it: re-run a skill from a fixed starting state, regenerate a different user prompt, or retry from before a branch you didn't like. The cut is in place and destructive — the discarded tail is gone (this is not a copy; use convert_session if you want to preserve the original).

    ONLY conversion artifacts are eligible: the file must carry an x-converter-provenance line (the same "this is ours to mutate" trust surface delete_conversions keys off). A real session or normally-dispatched subagent is refused untouched — we never truncate a transcript we didn't write. Unlike delete_conversions, a converted SESSION is eligible (it is yours to replay), and there is no growth guard: rewinding a resumed/grown artifact is the whole point.

    cut='after' keeps through the named turn; cut='before' drops the named turn onward (rewind to just before a user prompt to regenerate it). After the cut the tail is trimmed to a resumable boundary — trailing noise and any dangling assistant tool_use (which would otherwise break resume) are dropped and reported. The response leads with the artifact id and the exact `invocation` to resume it (SendMessage for a subagent, `claude -r` for a session); `lines_at_creation` is re-stamped so the artifact stays deletable by delete_conversions."""
    _validate_turn_id(turn)
    kind, full_id, path = _resolve_artifact_for_rewind(src_id, src_project)

    if not is_conversion_artifact(path):
        raise ToolError(
            f"{kind} {full_id[:12]} is not a conversion artifact (no "
            f"x-converter-provenance line) — refusing to rewind a transcript we "
            f"didn't write. Convert it first with convert_session if you want a "
            f"mutable copy."
        )

    try:
        result = rewind_transcript_file(
            transcript_path=path,
            artifact_id=full_id,
            kind=kind,
            turn=turn,
            cut=cut,
        )
    except ValueError as e:
        raise ToolError(str(e))
    return RewindTranscriptResponse.from_result(result)


# Refusal reasons (kept as constants so the sweep and explicit-id paths share
# identical wording, and tests can assert on stable substrings).
_REFUSE_NOT_CONVERSION = (
    "not a conversion artifact (no x-converter-provenance line) — refusing to "
    "delete a real session or subagent"
)
_REFUSE_GROWTH = (
    "conversion has been resumed or built upon since creation; someone may depend "
    "on it — confirm with the user before removing. Pass force=true with this id if "
    "you created this conversion and have captured what you need from it."
)
_REFUSE_SESSION = (
    "converted sessions are for humans to manage; delete_conversions only removes "
    "subagent conversions. Remove the file manually."
)


@mcp.tool(annotations=_DELETE_ANNOTATIONS)
def delete_conversions(
    ids: Annotated[
        Optional[list[str]],
        Field(description="Conversion artifact ids (agent ids, prefixes accepted) to delete. Omit to delete ALL conversion-tagged SUBAGENTS under the calling session. Converted SESSIONS are never deletable by this tool (even by explicit id) — remove those files manually."),
    ] = None,
    force: Annotated[
        bool,
        Field(description="Delete a listed conversion even though it has been resumed since creation. Honored ONLY for ids you name in `ids` — force with `ids` omitted is an error, so the sweep stays conservative and keeps reporting resumed artifacts in `refused`. This is the escape hatch for the interview pattern: converting a session, asking it one batched question (which IS a resume), then removing the copy you just made. It does not weaken any other guard — a real session, a normally-dispatched subagent, and a converted session are still refused."),
    ] = False,
) -> DeleteConversionsResponse:
    """Delete SUBAGENT conversion artifacts created by convert_session — and ONLY those.

    Each subagent id is verified to carry a valid x-converter-provenance line (the sole trust surface — meta.json is rewritten on resume and isn't trusted) before anything is removed. Two guards protect a tagged artifact from deletion: if its line count has grown past `lines_at_creation`, it was resumed or built upon and is refused (someone may now depend on it); ids that resolve to a real but NON-conversion artifact (or don't resolve at all) are refused with a per-id reason. Only the growth guard is escapable, and only for explicitly listed ids, with `force` — use it on a conversion you created and are done interrogating. This tool can never delete a genuine session or a normally-dispatched subagent.

    Converted SESSIONS are refused unconditionally — even when tagged, passed by explicit id, and forced — because a session is for a human to open and manage; remove its file manually if you mean to.

    Omit `ids` to sweep every conversion-tagged subagent under the calling session that passes the growth guard (a cleanup-after-yourself default); grown ones are reported in `refused`, not silently skipped."""
    deleted: list[DeletedConversion] = []
    refused: list[RefusedDeletion] = []

    if force and ids is None:
        # force is a per-artifact judgment ("I made this one and I'm done with
        # it"), not a mode. Blanket-forcing a sweep would delete conversions the
        # caller never created, so the sweep never honors it.
        raise ToolError(
            "force=true requires explicit ids: it is only honored for conversions you "
            "name. A sweep (ids omitted) stays conservative — resumed conversions under "
            "the calling session are reported in `refused`. Re-call with the ids you "
            "want removed."
        )

    if ids is None:
        # Sweep mode: every conversion subagent under the calling session only.
        current = _current_claude_session_id()
        if not current:
            raise ToolError(
                "Cannot sweep: the calling session is unknown (CLAUDE_CODE_SESSION_ID "
                "is not set). Pass explicit ids instead."
            )
        try:
            holding = _resolve_session_ref_for_convert(current, None)
        except ToolError as e:
            raise ToolError(
                f"delete_conversions sweep failed: could not resolve the calling "
                f"session ({current!r}): {e}. "
                f"Pass explicit ids instead of relying on the sweep."
            ) from e
        for af in collect_agent_files(resolve_subagents_dir(holding.path)):
            if not af.is_conversion_artifact:
                continue
            if growth_exceeded(af.path):
                refused.append(RefusedDeletion(id=af.agent_id, reason=_REFUSE_GROWTH))
                continue
            delete_agent_conversion(af.path)
            deleted.append(
                DeletedConversion(id=af.agent_id, kind="subagent", path=str(af.path))
            )
        return DeleteConversionsResponse(deleted=deleted, refused=refused)

    # Resolve all ids over a filename-only corpus, narrowed to the holding
    # session(s) by filename first, so an unscoped delete never parses a
    # transcript (the full-corpus-parse hazard rewind hit). Ids that match
    # nothing by name fall through to resolve_artifacts as no-match
    # placeholders and are refused per-id below.
    corpus = Corpus.discover(None).narrow_to_artifact_ids(ids)
    resolved = resolve_artifacts(ids, corpus.refs)

    for raw_id, kind, full_id, path in resolved:
        if not kind or path is None:
            refused.append(
                RefusedDeletion(id=raw_id, reason="no session or subagent matches this id")
            )
            continue
        if kind == "session":
            # Sessions are never deletable here — even tagged ones.
            refused.append(RefusedDeletion(id=raw_id, reason=_REFUSE_SESSION))
            continue
        # subagent
        if not is_conversion_artifact(path):
            refused.append(RefusedDeletion(id=raw_id, reason=_REFUSE_NOT_CONVERSION))
            continue
        # force overrides the growth guard, and only here — the caller named this
        # exact artifact, so "someone may depend on it" is their call to make.
        if not force and growth_exceeded(path):
            refused.append(RefusedDeletion(id=raw_id, reason=_REFUSE_GROWTH))
            continue
        delete_agent_conversion(path)
        deleted.append(DeletedConversion(id=full_id, kind="subagent", path=str(path)))

    return DeleteConversionsResponse(deleted=deleted, refused=refused)


def main():
    mcp.run()
