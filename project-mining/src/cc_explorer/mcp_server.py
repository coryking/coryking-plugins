"""MCP server wrapping the cc_explorer library.

Exposes Claude Code chat log exploration as MCP tools via FastMCP.
All tools are read-only and return typed Pydantic response models.
FastMCP auto-generates output schemas from return type annotations.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from .formatting import matches_id
from .models import AssistantTranscriptEntry, ToolResultEntry, parse_agent_content
from .utils import PrefixId
from .responses import (
    AgentDetailResponse,
    AgentListResponse,
    BrowseSessionResponse,
    GrepSessionResponse,
    ReadTurnResponse,
    SearchProjectResponse,
    SessionAgentsResponse,
    SessionListResponse,
)
from .search import (
    ENTRY_TYPE_MAP,
    ConversationRole,
    ScopeType,
    SessionInfo,
    browse_session_turns,
    get_turn_context,
    load_sessions,
    resolve_project,
    search as do_search,
    triage_multi,
)
from .subagents import extract_subagents, resolve_output_files, scan_output_file_stats

mcp = FastMCP("cc-explorer")

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
        Field(description="Only sessions with at least N agents."),
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
    """List conversations in a project with stats: dates, message counts, token usage, tool calls, agent dispatches.

    This is the orientation step — like `ls -la` on the project's chat history. Use it to see what exists before searching.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    sessions = [s for s in sessions if s.message_count >= min_messages]
    sessions = [s for s in sessions if s.stats.tool_use_count >= min_tools]
    sessions = [s for s in sessions if s.stats.agent_count >= min_agents]
    sessions = _filter_by_date(sessions, after, before)

    if not sessions:
        raise ToolError("No conversations match filters")

    return SessionListResponse.from_sessions(sessions)


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
    scope: Annotated[
        ScopeType,
        Field(
            description="Content scope: 'messages' for conversation text, 'tools' for tool inputs (Bash commands, file paths, grep patterns), 'all' for both. Using 'tools' or 'all' searches both roles regardless of the role parameter."
        ),
    ] = ScopeType.messages,
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
) -> SearchProjectResponse:
    """Scan a project's chat history for patterns, grouped by pattern with per-pattern hit counts and session breakdowns.

    Pass all your candidate search terms at once — each gets its own hit count and session list so you can see which terms are useful and which aren't. Use separate patterns rather than regex OR pipes (e.g. ["facebook.*scrape", "fb_capture"] not "facebook.*scrape|fb_capture") to get this per-term breakdown. Results are sorted by hit count (hottest patterns and sessions first) and include session dates for chronological context. Follow up with grep_session on sessions that look promising.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    sessions = _filter_by_date(sessions, after, before)

    # --scope tools/all implies searching all entry types
    if scope in (ScopeType.tools, ScopeType.all):
        entry_types = ENTRY_TYPE_MAP[ConversationRole.all]
    else:
        entry_types = ENTRY_TYPE_MAP[role]

    all_results = triage_multi(
        sessions, patterns, entry_types, example_width=excerpt_width, scope=scope
    )

    # Check if anything matched
    if not any(r for _, results in all_results for r in results):
        raise ToolError(f"No matches for: {', '.join(patterns)}")

    return SearchProjectResponse.from_triage(all_results, excerpt_width=excerpt_width)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def grep_session(
    session: Annotated[
        str,
        Field(
            description="Session ID or prefix. Required — use search_project to find session IDs first."
        ),
    ],
    pattern: Annotated[
        str,
        Field(description="Regex pattern to search for (case-insensitive)."),
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
    scope: Annotated[
        ScopeType,
        Field(
            description="Content scope: 'messages' for conversation text, 'tools' for tool inputs (Bash commands, file paths, grep patterns), 'all' for both."
        ),
    ] = ScopeType.messages,
    limit: Annotated[
        int,
        Field(
            description="Max matches to return (like head -N). Overflow is truncated, not mode-switched."
        ),
    ] = 30,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs/outputs) to N chars. 0 = full content.",
        ),
    ] = 500,
    agent_content: Annotated[
        str | None,
        Field(
            description="Comma-separated content to show for assistant turns beyond text: 'thinking', 'inputs' (tool call summaries), 'outputs' (tool results). Default: 'inputs'. Empty string = text only. Pair with truncate when using 'outputs' — tool results can be very large.",
        ),
    ] = None,
) -> GrepSessionResponse:
    """Show matches for a pattern within a single conversation, with surrounding context.

    Like `rg -C3` on a single file — returns matching entries with surrounding turns for context. Each entry includes its full character length so you can gauge size before calling read_turn.
    """
    content_set = parse_agent_content(agent_content)
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    # Filter to the target session
    sessions = [s for s in sessions if PrefixId(s.session_id) == session]
    if not sessions:
        raise ToolError(f"No session matching: {session}")

    # --scope tools/all implies searching all entry types
    if scope in (ScopeType.tools, ScopeType.all):
        entry_types = ENTRY_TYPE_MAP[ConversationRole.all]
    else:
        entry_types = ENTRY_TYPE_MAP[role]

    result = do_search(
        sessions,
        pattern,
        entry_types,
        context,
        max_results=limit,
        scope=scope,
        agent_content=content_set,
    )

    if not result.matches:
        raise ToolError(f"No matches for: {pattern}")

    return GrepSessionResponse.from_matches(
        session_id=sessions[0].session_id,
        matches=result.matches,
        total=result.total_matches,
        limit=limit,
        truncate=truncate,
        agent_content=content_set,
    )


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def read_turn(
    turn: Annotated[
        str,
        Field(
            description="Turn UUID or prefix to center on (from grep_session output)."
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
        Field(description="Number of turns before and after to include."),
    ] = 3,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs/outputs) to N chars. 0 = full content.",
        ),
    ] = 0,
    agent_content: Annotated[
        str | None,
        Field(
            description="Comma-separated content to show for assistant turns beyond text: 'thinking', 'inputs' (tool call summaries), 'outputs' (tool results). Default: 'inputs'. Empty string = text only. Pair with truncate when using 'outputs' — tool results can be very large.",
        ),
    ] = None,
) -> ReadTurnResponse:
    """Read a specific moment in a conversation at full fidelity.

    Like `sed -n '450,470p'` — reads a specific section without pattern matching. Takes a turn UUID (from grep_session output) and returns the surrounding conversation.

    Use the full_length values from grep_session to gauge entry sizes before reading.
    """
    content_set = parse_agent_content(agent_content)
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    session_info, entries = get_turn_context(sessions, turn, context, agent_content=content_set)

    if not entries:
        raise ToolError(f"Turn {turn} not found")

    return ReadTurnResponse.from_entries(session_info, turn, entries, truncate=truncate, agent_content=content_set)


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
        Field(description="Number of conversation turns to return.", ge=1, le=50),
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
            description="Truncate each content piece (text, tool inputs/outputs) to N chars. 0 = full content.",
        ),
    ] = 0,
    agent_content: Annotated[
        str | None,
        Field(
            description="Comma-separated content to show for assistant turns beyond text: 'thinking', 'inputs' (tool call summaries), 'outputs' (tool results). Default: 'inputs'. Empty string = text only. Pair with truncate when using 'outputs' — tool results can be very large.",
        ),
    ] = None,
) -> BrowseSessionResponse:
    """Read the first or last N turns of a conversation — like head/tail on a session.

    Quick orientation tool: see how a conversation started or where it ended up without needing a search pattern. Use 'head' to understand what the session was about, 'tail' to see the conclusion. Pass a turn UUID to anchor and paginate through a session.
    """
    content_set = parse_agent_content(agent_content)
    if position not in ("head", "tail"):
        raise ToolError(f"position must be 'head' or 'tail', got: {position!r}")

    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    target = [s for s in sessions if PrefixId(s.session_id) == session]
    if not target:
        raise ToolError(f"No session matching: {session}")

    entry_types = ENTRY_TYPE_MAP[role]
    # Add ToolResultEntry when outputs requested and assistant entries are included
    if "outputs" in content_set and AssistantTranscriptEntry in entry_types:
        entry_types = entry_types + (ToolResultEntry,)

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
        agent_content=content_set,
    )


# =============================================================================
# Agent inspection tools
# =============================================================================


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_agent_sessions(
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
    after: Annotated[
        datetime | None,
        Field(description="Only sessions after this datetime."),
    ] = None,
    before: Annotated[
        datetime | None,
        Field(description="Only sessions before this datetime."),
    ] = None,
) -> SessionListResponse:
    """List all sessions that spawned subagents, with agent counts and tool usage."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    agent_sessions = [s for s in sessions if s.stats.agent_count > 0]
    agent_sessions = _filter_by_date(agent_sessions, after, before)

    if not agent_sessions:
        raise ToolError("No sessions with subagents found.")

    return SessionListResponse.from_sessions(agent_sessions)


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
    compaction: Annotated[bool, Field(description="Show compaction details.")] = False,
) -> SessionAgentsResponse:
    """List all agents spawned by a specific session, with stats and output file info."""
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

    agents = extract_subagents(target.path)

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
    compaction: Annotated[bool, Field(description="Show compaction details.")] = False,
    truncate: Annotated[
        int,
        Field(
            description="Truncate each content piece (text, tool inputs) to N chars. 0 = full content.",
        ),
    ] = 80,
) -> AgentDetailResponse | AgentListResponse:
    """Get full prompt, result, stats, and optional tool trace for specific agent(s)."""
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
        agents = extract_subagents(s.path)
        for sa in agents:
            if matches_id(sa, agent_id):
                return sa, s
    return None, None


def main():
    mcp.run()
