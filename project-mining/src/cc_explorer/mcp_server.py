"""MCP server wrapping the cc_explorer library.

Exposes Claude Code chat log exploration as MCP tools via FastMCP.
All tools are read-only and return typed Pydantic response models.
FastMCP auto-generates output schemas from return type annotations.
"""

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from .formatting import matches_id
from .models import parse_hide
from .utils import PrefixId
from .responses import (
    AgentDetailResponse,
    AgentListResponse,
    AgentToolAudit,
    AgentToolCall,
    BrowseSessionResponse,
    GrepSessionResponse,
    GrepSessionsResponse,
    ReadTurnResponse,
    SearchProjectResponse,
    SessionAgentsResponse,
    SessionListResponse,
    SessionToolAuditResponse,
)
from .search import (
    ENTRY_TYPE_MAP,
    ConversationRole,
    SessionInfo,
    browse_session_turns,
    conversation_types_for,
    get_turn_context,
    load_sessions,
    resolve_project,
    search_multi,
    triage_multi,
)
from .subagents import (
    discover_subagents,
    extract_agent_tool_audit,
    resolve_output_files,
    scan_output_file_stats,
)

_INSTRUCTIONS = """\
cc-explorer explores Claude Code chat history — your past conversations and the
subagents they spawned — stored as per-project JSONL transcripts. All tools are
read-only and scoped to one project (CWD by default; pass `project` to target
another). There are two lenses:

1. Conversations — find and read what was discussed.
   Orient with list_project_sessions (like `ls` on the history), scan for terms
   across sessions with search_project (which session is hot), then zoom in:
   grep_session / grep_sessions for matches-in-context, read_turn /
   browse_session to read at full fidelity.

2. Agent forensics — see what a session's subagents actually did.
   Start from list_project_sessions(min_agents=1) to find sessions that spawned
   agents, then list_session_agents to see every agent the session ran (workflow
   ones included), get_agent_detail for one agent's prompt / result / tool-trace,
   and audit_session_tools to check whether the agents used their tools correctly
   (per-tool counts, error rates, retries).
"""

mcp = FastMCP("cc-explorer", instructions=_INSTRUCTIONS)

_TOOL_ANNOTATIONS = {"readOnlyHint": True, "openWorldHint": False}


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
    """The Claude Code session that launched THIS MCP server, if known.

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


def _parse_hide_or_raise(value: str | None) -> frozenset[str]:
    """parse_hide that converts ValueError to ToolError for MCP entry points.

    Lives here (not in models.py) to keep FastMCP exception types out of the
    pure-data layer. Every display tool that accepts `hide` needs this.
    """
    try:
        return parse_hide(value)
    except ValueError as e:
        raise ToolError(str(e))


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
def list_project_sessions(
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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

    This is the orientation step — like `ls -la` on the project's chat history. Use it to see what exists before searching. Each row carries two agent counts: `agents` (dispatched directly) and `agents_present` (the full population including workflow orphans) — a gap between them means the session orchestrated workflows. `user_turns` (human prompts) against a high message/agent count flags a single prompt that fanned out into a long autonomous run. Pass min_agents=1 to narrow to sessions that ran subagents — the starting point for agent forensics (then drill in with list_session_agents / get_agent_detail / audit_session_tools). The calling conversation, if present, is kept but flagged `is_current: true` so you can tell which row is the session you're in.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj, with_agents_present=True)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    sessions = [s for s in sessions if s.message_count >= min_messages]
    sessions = [s for s in sessions if s.stats.tool_use_count >= min_tools]
    sessions = [s for s in sessions if s.agents_present >= min_agents]
    sessions = _filter_by_date(sessions, after, before)

    if not sessions:
        raise ToolError("No conversations match filters")

    return SessionListResponse.from_sessions(sessions, current_session=_current_session_id())


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def search_project(
    patterns: Annotated[
        list[str],
        Field(
            description="Regex patterns to scan for (case-insensitive). Results grouped by pattern, sorted by hit count."
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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
) -> SearchProjectResponse:
    """Scan a project's chat history for patterns, grouped by pattern with per-pattern hit counts and session breakdowns.

    Search is exhaustive by default: every pattern is checked against conversation text, tool inputs (Bash commands, file paths, grep patterns), tool outputs, and assistant thinking. The pattern is the precision tool — use tight regex to narrow noisy searches instead of a scope flag.

    Pass all your candidate search terms at once — each gets its own hit count and session list so you can see which terms are useful and which aren't. Use separate patterns rather than regex OR pipes (e.g. ["facebook.*scrape", "fb_capture"] not "facebook.*scrape|fb_capture") to get this per-term breakdown. Results are sorted by hit count (hottest patterns and sessions first) and include session dates for chronological context. Follow up with grep_session on sessions that look promising.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

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
        sessions, patterns, base_types=base_types, example_width=excerpt_width
    )

    # Check if anything matched
    if not any(r for _, results in all_results for r in results):
        raise ToolError(
            f"No matches for: {', '.join(patterns)}. Patterns are case-insensitive "
            f"regex — try shorter or broader terms, set role='all' to search both "
            f"sides, or widen the date range."
        )

    return SearchProjectResponse.from_triage(all_results, excluded_current_session=excluded)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def grep_session(
    session: Annotated[
        str,
        Field(
            description="Session ID or prefix. Required — use search_project to find session IDs first."
        ),
    ],
    patterns: Annotated[
        list[str],
        Field(
            description="Regex patterns to search for (case-insensitive). Each gets its own hit count and match list. Use separate patterns rather than `|`-OR (e.g. ['fb_capture', 'facebook.*scrape'] not 'fb_capture|facebook.*scrape') so you can see which terms land and which are dead weight."
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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
) -> GrepSessionResponse:
    """Show matches for one or more patterns within a single conversation, with surrounding context.

    Like `rg -C3` on a single file, but pattern-centric: pass all your candidate terms in one call and each gets its own hit count and match blocks. Zero-hit patterns are kept in the response so you see them as dead weight rather than guessing why they're missing.

    Search is exhaustive by default across text, tool inputs, tool outputs, and thinking blocks. Each entry includes its full character length so you can gauge size before calling read_turn.

    Match blocks have three fields: `before` (context turns before), `match` (the matching entry, excerpted on the hit so it stays visible even when truncated), and `after` (context turns after).
    """
    if not patterns:
        raise ToolError("patterns must contain at least one pattern")

    hide_set = _parse_hide_or_raise(hide)
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    # Filter to the target session
    sessions = [s for s in sessions if PrefixId(s.session_id) == session]
    if not sessions:
        raise ToolError(f"No session matching: {session}")

    base_types = ENTRY_TYPE_MAP[role]

    # Single-pass over the session's entries — search_multi checks every
    # pattern per entry instead of re-walking the transcript per pattern.
    multi_results = search_multi(
        sessions,
        patterns,
        base_types=base_types,
        context=context,
        max_results_per_pattern=limit,
        hide=hide_set,
    )
    pattern_results = multi_results[sessions[0].session_id]

    if not any(matches for _, matches, _ in pattern_results):
        raise ToolError(
            f"No matches for any pattern: {', '.join(patterns)}. Try shorter or "
            f"broader regex, set role='all', or use browse_session to read the "
            f"session directly."
        )

    return GrepSessionResponse.from_pattern_results(
        session_id=sessions[0].session_id,
        results=pattern_results,
        truncate=truncate,
        hide=hide_set,
        worktree=sessions[0].worktree,
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def grep_sessions(
    sessions: Annotated[
        list[str],
        Field(
            description="Session IDs or prefixes to search. Required — use search_project to find which sessions are hot, then fan out across them in one call instead of looping grep_session."
        ),
    ],
    patterns: Annotated[
        list[str],
        Field(
            description="Regex patterns to search for (case-insensitive). Each gets its own hit count and match list per session. Use separate patterns rather than `|`-OR so you can see which terms land."
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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
) -> GrepSessionsResponse:
    """Fan out grep across multiple sessions in one call.

    Use this when you've already identified your hot sessions (via `search_project`) and want context blocks across all of them for the same patterns. One call replaces N `grep_session` calls.

    Returns one entry per session that had at least one match (zero-hit sessions are omitted). Each entry has the same shape as `grep_session` output: per-pattern hit counts and match blocks with surrounding context. Sort order preserves the order of `sessions`.
    """
    if not sessions:
        raise ToolError("sessions must contain at least one session id")
    if not patterns:
        raise ToolError("patterns must contain at least one pattern")

    hide_set = _parse_hide_or_raise(hide)
    proj = resolve_project(project)
    all_sessions = load_sessions(proj)
    if not all_sessions:
        raise ToolError(f"No conversations found for {proj}")

    # Resolve each session prefix to a SessionInfo, preserving input order
    resolved: list = []
    not_found: list[str] = []
    for sid in sessions:
        match = next((s for s in all_sessions if PrefixId(s.session_id) == sid), None)
        if match is None:
            not_found.append(sid)
        else:
            resolved.append(match)

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
        raise ToolError(
            f"No matches in any session for any pattern: {', '.join(patterns)}. "
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
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    target_session_id: str | None = None
    if session:
        target = [s for s in sessions if PrefixId(s.session_id) == session]
        if not target:
            raise ToolError(f"No session matching: {session}")
        target_session_id = target[0].session_id

    session_info, entries = get_turn_context(
        sessions, turn, context, hide=hide_set, session_id=target_session_id
    )

    if not entries:
        raise ToolError(f"Turn {turn} not found")

    return ReadTurnResponse.from_entries(session_info, turn, entries, truncate=truncate, hide=hide_set)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def browse_session(
    session: Annotated[
        str,
        Field(
            description="Session ID or prefix. Use list_project_sessions to find session IDs."
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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

    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    target = [s for s in sessions if PrefixId(s.session_id) == session]
    if not target:
        raise ToolError(f"No session matching: {session}")

    base_types = ENTRY_TYPE_MAP[role]
    entry_types = conversation_types_for(hide_set, base_types)

    entries, total = browse_session_turns(
        target[0], position, turns, anchor_turn=turn, entry_types=entry_types
    )

    if not entries:
        if turn:
            raise ToolError(f"Turn {turn} not found in session {session}")
        raise ToolError(f"Session {session} has no conversation turns")

    return BrowseSessionResponse.from_entries(
        session_id=target[0].session_id,
        position=position,
        entries=entries,
        total=total,
        truncate=truncate,
        anchor=turn,
        hide=hide_set,
        worktree=target[0].worktree,
    )


# =============================================================================
# Agent inspection tools
# =============================================================================


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_session_agents(
    session: Annotated[str, Field(description="Session ID or prefix to inspect.")],
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
    task_output_dir: Annotated[
        str | None,
        Field(description="Directory containing saved .output files."),
    ] = None,
) -> SessionAgentsResponse:
    """List every subagent a session ran — type, status, token cost, duration, and whether its full record is available.

    Includes agents spawned by a workflow, not just ones the conversation dispatched directly, so the count reflects the session's real fan-out. Each row's `source` tells you whether to trust missing fields — and `workflow_run_id` lets you group agents from the same workflow run.

    Use when you want to see a session's fan-out before drilling in: which agents ran, which errored, which burned the most tokens. Step two of agent forensics — get a session id from list_project_sessions(min_agents=1), then from here pass an agent_id to get_agent_detail for the full prompt/result/trace, or audit the whole session's tool usage with audit_session_tools.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    target = None
    for s in sessions:
        if PrefixId(s.session_id) == session:
            target = s
            break

    if not target:
        raise ToolError(f"Session {session} not found")

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
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    if session:
        sessions = [s for s in sessions if PrefixId(s.session_id) == session]
        if not sessions:
            raise ToolError(f"Session {session} not found")

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
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
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
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    target = next((s for s in sessions if PrefixId(s.session_id) == session), None)
    if target is None:
        raise ToolError(f"Session {session} not found")

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
        worktree=target.worktree,
        title=target.title,
        total_present=total_present,
        total_audited=len(audits),
        total_tool_calls=total_calls,
        total_errors=total_errors,
        tool_name_filter=tool_name_filter,
        agents=audits,
    )


def main():
    mcp.run()
