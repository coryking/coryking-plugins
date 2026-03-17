"""MCP server wrapping the cc_explorer library.

Exposes Claude Code chat log exploration as MCP tools via FastMCP.
All tools are read-only and return structured dicts.

Conversation text in results uses entry line format:
  [U:id] user text     — human message (U = user, id = first 8 chars of turn UUID)
  [A:id] assistant text — assistant message with smart tool call summaries
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Literal, Optional

from fastmcp import FastMCP
from pydantic import Field

from .formatting import (
    format_agent_detail,
    format_conversation_list,
    format_manifest_view,
    format_quote,
    format_search_results,
    format_session_view,
    format_triage_results,
    matches_id,
)
from .search import (
    ENTRY_TYPE_MAP,
    ScopeType,
    TriageResult,
    get_turn_context,
    load_sessions,
    resolve_project,
    search as do_search,
    triage,
)
from .subagents import extract_subagents, resolve_output_files, scan_output_file_stats

mcp = FastMCP("cc-explorer")

_TOOL_ANNOTATIONS = {"readOnlyHint": True, "openWorldHint": False}


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def search_chat_history(
    patterns: Annotated[
        list[str],
        Field(
            description="Regex pattern(s) to search for. Multiple patterns are OR'd and force count mode."
        ),
    ],
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
    session: Annotated[
        str | None,
        Field(description="Session ID prefix to narrow search to one conversation."),
    ] = None,
    context: Annotated[
        int,
        Field(
            description="Number of surrounding messages to include with each match."
        ),
    ] = 1,
    scope: Annotated[
        Literal["messages", "tools", "all"],
        Field(
            description="Search scope: 'messages' for conversation text, 'tools' for Bash/Read/Edit/Grep inputs, 'all' for both."
        ),
    ] = "messages",
    counts_only: Annotated[
        bool,
        Field(
            description="Force count mode -- show per-session match counts instead of content."
        ),
    ] = False,
    example_width: Annotated[
        int,
        Field(description="Character width of example excerpts in triage mode."),
    ] = 150,
    limit: Annotated[
        int,
        Field(description="Max results before auto-switching to count mode."),
    ] = 30,
) -> dict[str, Any]:
    """Search Claude Code chat logs for patterns. This is a DISCOVERY tool — use it to find WHERE things were discussed, then use quote_chat_moment to READ what was said.

    Returns two response shapes depending on hit volume:
    - **Content mode** (few hits): full matched entries with context and turn UUIDs. Read these directly.
    - **Triage mode** (many hits, or multi-pattern): per-session hit counts grouped by pattern, with a short example excerpt. Use session IDs to narrow your next search, or grab turn UUIDs and quote_chat_moment to read the actual conversations.

    Don't keep searching for answers in triage examples — they're for locating, not reading. Once you've found the relevant sessions/turns, switch to quote_chat_moment.

    Conversation text uses [U:id]/[A:id] entry line format where id is the first 8 chars of the turn UUID.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        return {"error": f"No conversations found for {proj}"}

    scope_val = ScopeType(scope)

    # --scope tools/all implies searching all entry types
    if scope_val in (ScopeType.tools, ScopeType.all):
        entry_types = ENTRY_TYPE_MAP["all"]
    else:
        entry_types = ENTRY_TYPE_MAP["human"]

    # Apply session filter early
    if session:
        sessions = [s for s in sessions if s.session_id.startswith(session)]
        if not sessions:
            return {"error": f"No session matching: {session}"}

    if counts_only or len(patterns) > 1:
        # Count mode: triage across all patterns
        all_results: list[tuple[str, TriageResult]] = []
        for pat in patterns:
            results = triage(sessions, pat, entry_types, example_width=example_width, scope=scope_val)
            for r in results:
                all_results.append((pat, r))

        if not all_results:
            return {"error": f"No matches for: {', '.join(patterns)}"}

        all_results.sort(key=lambda x: x[1].count, reverse=True)
        return format_triage_results(all_results)

    # Single pattern: auto-triage then maybe expand
    pat = patterns[0]
    triage_results = triage(sessions, pat, entry_types, example_width=example_width, scope=scope_val)

    if not triage_results:
        return {"error": f"No matches for: {pat}"}

    total_hits = sum(r.count for r in triage_results)

    if total_hits <= limit:
        # Few enough hits -- show content
        result = do_search(
            sessions,
            pat,
            entry_types,
            context,
            max_results=limit,
            scope=scope_val,
        )
        if not result.matches:
            return {"error": f"No matches for: {pat}"}
        return format_search_results(result, pat)
    else:
        # Too many hits -- show counts
        all_results = [(pat, r) for r in triage_results]
        return format_triage_results(all_results)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def quote_chat_moment(
    turn: Annotated[
        str,
        Field(
            description="Turn UUID (or prefix) to center on, from search output."
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
        Field(description="Number of messages before and after the target turn."),
    ] = 3,
) -> dict[str, Any]:
    """Pull the full conversation moment around a specific turn UUID.

    Returns entries as [U:id]/[A:id] formatted strings with full untruncated text.
    Use turn UUIDs from search_chat_history output.
    """
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        return {"error": f"No conversations found for {proj}"}

    session_info, entries = get_turn_context(sessions, turn, context)

    if not entries:
        return {"error": f"Turn {turn} not found"}

    return format_quote(session_info, turn, context, entries)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_chat_sessions(
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
) -> dict[str, Any]:
    """List Claude Code conversations for a project with usage stats (message count, agents, tokens, tools)."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        return {"error": f"No conversations found for {proj}"}

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
        return {"error": "No conversations match filters"}

    return format_conversation_list(sessions)


@mcp.tool(annotations=_TOOL_ANNOTATIONS)
def list_agent_sessions(
    project: Annotated[
        str | None,
        Field(
            description="Project path, bare name (expands to ~/projects/<name>), or omit for CWD."
        ),
    ] = None,
) -> dict[str, Any]:
    """List all sessions that spawned subagents, with agent counts and tool usage."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        return {"error": f"No conversations found for {proj}"}

    agent_sessions = [s for s in sessions if s.stats.agent_count > 0]

    if not agent_sessions:
        return {"error": "No sessions with subagents found."}

    return format_manifest_view(agent_sessions)


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
) -> dict[str, Any]:
    """List all agents spawned by a specific session, with stats and output file info."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        return {"error": f"No conversations found for {proj}"}

    target = None
    for s in sessions:
        if s.session_id.startswith(session):
            target = s
            break

    if not target:
        return {"error": f"Session {session} not found"}

    agents = extract_subagents(target.path)

    output_dir = Path(task_output_dir).expanduser() if task_output_dir else None
    resolve_output_files(agents, output_dir)
    scan_output_file_stats(agents)

    return format_session_view(target, agents, compaction=compaction)


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
) -> dict[str, Any]:
    """Get full prompt, result, stats, and optional tool trace for specific agent(s)."""
    proj = resolve_project(project)
    sessions = load_sessions(proj)
    if not sessions:
        return {"error": f"No conversations found for {proj}"}

    if session:
        sessions = [s for s in sessions if s.session_id.startswith(session)]
        if not sessions:
            return {"error": f"Session {session} not found"}

    output_dir = Path(task_output_dir).expanduser() if task_output_dir else None

    details: list[dict[str, Any]] = []
    for aid in agent_ids:
        found, found_session = _find_agent(sessions, aid)
        if not found or not found_session:
            details.append({"error": f"Agent {aid} not found"})
            continue

        resolve_output_files([found], output_dir)
        entries_map = scan_output_file_stats([found], keep_entries=trace)

        details.append(
            format_agent_detail(
                found,
                found_session,
                trace=trace,
                no_reasoning=no_reasoning,
                entries_map=entries_map,
                compaction=compaction,
            )
        )

    if len(details) == 1:
        return details[0]
    return {"agents": details}


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
