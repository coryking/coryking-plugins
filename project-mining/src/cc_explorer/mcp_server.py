"""MCP server wrapping the cc_explorer library.

Exposes Claude Code chat log exploration as MCP tools via FastMCP.
All tools are read-only and return typed Pydantic response models.
FastMCP auto-generates output schemas from return type annotations.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from .formatting import matches_id
from .responses import (
    AgentDetailResponse,
    AgentListResponse,
    GrepSessionResponse,
    ReadTurnResponse,
    SearchProjectResponse,
    SessionAgentsResponse,
    SessionListResponse,
)
from .search import (
    ENTRY_TYPE_MAP,
    PatternTriageResults,
    ScopeType,
    get_turn_context,
    load_sessions,
    resolve_project,
    search as do_search,
    triage,
)
from .subagents import extract_subagents, resolve_output_files, scan_output_file_stats

mcp = FastMCP("cc-explorer")

_TOOL_ANNOTATIONS = {"readOnlyHint": True, "openWorldHint": False}


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
    min_agents: Annotated[
        int | None,
        Field(description="Only sessions with at least N agents."),
    ] = None,
    after: Annotated[
        str | None,
        Field(description="Only sessions after this date (YYYY-MM-DD)."),
    ] = None,
    before: Annotated[
        str | None,
        Field(description="Only sessions before this date (YYYY-MM-DD)."),
    ] = None,
) -> SessionListResponse:
    """List conversations in a project with stats: dates, message counts, token usage, tool calls, agent dispatches.

    This is the orientation step — like `ls -la` on the project's chat history. Use it to see what exists before searching.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    if min_agents is not None:
        sessions = [s for s in sessions if s.stats.agent_count >= min_agents]

    if after:
        after_dt = datetime.strptime(after, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        sessions = [
            s for s in sessions if s.first_timestamp and s.first_timestamp >= after_dt
        ]

    if before:
        before_dt = datetime.strptime(before, "%Y-%m-%d").replace(
            tzinfo=timezone.utc
        )
        sessions = [
            s
            for s in sessions
            if s.first_timestamp and s.first_timestamp <= before_dt
        ]

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
        Literal["user", "assistant", "all"],
        Field(
            description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."
        ),
    ] = "user",
    scope: Annotated[
        Literal["messages", "tools", "all"],
        Field(
            description="Content scope: 'messages' for conversation text, 'tools' for tool inputs (Bash commands, file paths, grep patterns), 'all' for both. Using 'tools' or 'all' searches both roles regardless of the role parameter."
        ),
    ] = "messages",
    excerpt_width: Annotated[
        int,
        Field(description="Character width of centered excerpt examples."),
    ] = 150,
) -> SearchProjectResponse:
    """Scan a project's chat history for patterns and report where they appear.

    Like `rg -c` across all sessions — tells you which patterns are productive and which sessions are hot. Use this to orient before drilling into a specific session with grep_session.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    scope_val = ScopeType(scope)

    # --scope tools/all implies searching all entry types
    if scope_val in (ScopeType.tools, ScopeType.all):
        entry_types = ENTRY_TYPE_MAP["all"]
    else:
        entry_types = ENTRY_TYPE_MAP[role]

    all_results: PatternTriageResults = []
    for pat in patterns:
        results = triage(sessions, pat, entry_types, example_width=excerpt_width, scope=scope_val)
        all_results.append((pat, results))

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
            description="Number of surrounding turns to include with each match (like grep -C)."
        ),
    ] = 2,
    role: Annotated[
        Literal["user", "assistant", "all"],
        Field(
            description="Which side of the conversation to search: 'user' for human messages, 'assistant' for agent responses, 'all' for both."
        ),
    ] = "user",
    scope: Annotated[
        Literal["messages", "tools", "all"],
        Field(
            description="Content scope: 'messages' for conversation text, 'tools' for tool inputs (Bash commands, file paths, grep patterns), 'all' for both."
        ),
    ] = "messages",
    limit: Annotated[
        int,
        Field(
            description="Max matches to return (like head -N). Overflow is truncated, not mode-switched."
        ),
    ] = 30,
) -> GrepSessionResponse:
    """Show matches for a pattern within a single conversation, with surrounding context.

    Like `rg -C3` on a single file — returns matching entries with surrounding turns for context. Each entry includes its full character length so you can gauge size before calling read_turn.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    # Filter to the target session
    sessions = [s for s in sessions if s.session_id.startswith(session)]
    if not sessions:
        raise ToolError(f"No session matching: {session}")

    scope_val = ScopeType(scope)

    # --scope tools/all implies searching all entry types
    if scope_val in (ScopeType.tools, ScopeType.all):
        entry_types = ENTRY_TYPE_MAP["all"]
    else:
        entry_types = ENTRY_TYPE_MAP[role]

    result = do_search(
        sessions,
        pattern,
        entry_types,
        context,
        max_results=limit,
        scope=scope_val,
    )

    if not result.matches:
        raise ToolError(f"No matches for: {pattern}")

    return GrepSessionResponse.from_matches(
        session_id=sessions[0].session_id,
        matches=result.matches,
        total=result.total_matches,
        limit=limit,
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
    limit: Annotated[
        int | None,
        Field(
            description="Max characters per entry in output. Entries exceeding this are truncated. Omit for full text."
        ),
    ] = None,
) -> ReadTurnResponse:
    """Read a specific moment in a conversation at full fidelity.

    Like `sed -n '450,470p'` — reads a specific section without pattern matching. Takes a turn UUID (from grep_session output) and returns the surrounding conversation.

    Use the full_length values from grep_session to gauge entry sizes before reading.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    session_info, entries = get_turn_context(sessions, turn, context)

    if not entries:
        raise ToolError(f"Turn {turn} not found")

    return ReadTurnResponse.from_entries(session_info, turn, entries, limit=limit)


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
) -> SessionListResponse:
    """List all sessions that spawned subagents, with agent counts and tool usage."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    agent_sessions = [s for s in sessions if s.stats.agent_count > 0]

    if not agent_sessions:
        raise ToolError("No sessions with subagents found.")

    return SessionListResponse.from_sessions(agent_sessions)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_session_agents(
    session: Annotated[
        str, Field(description="Session ID or prefix to inspect.")
    ],
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
    compaction: Annotated[
        bool, Field(description="Show compaction details.")
    ] = False,
) -> SessionAgentsResponse:
    """List all agents spawned by a specific session, with stats and output file info."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    target = None
    for s in sessions:
        if s.session_id.startswith(session):
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
        Field(
            description="Session ID prefix to narrow the search."
        ),
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
    compaction: Annotated[
        bool, Field(description="Show compaction details.")
    ] = False,
) -> AgentDetailResponse | AgentListResponse:
    """Get full prompt, result, stats, and optional tool trace for specific agent(s)."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        raise ToolError(f"No conversations found for {proj}")

    if session:
        sessions = [s for s in sessions if s.session_id.startswith(session)]
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
