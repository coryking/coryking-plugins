"""Pydantic response models for cc-explorer MCP tools.

FastMCP auto-generates output schemas from return type annotations.
These models ARE the output documentation — field names, types, and
descriptions appear in the tool schema the agent sees.

Conversation text in chats arrays uses pipe-delimited entry line format:
  turn_id|timestamp|role|full_length|display
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field

from .formatting import format_entry_line, format_session_date, render_trace
from .utils import PrefixId

if TYPE_CHECKING:
    from .search import MatchHit, PatternTriageResults, ProjectInfo, SessionInfo
    from .subagents import SubagentInfo


class SparseModel(BaseModel):
    """Base for response models — omits None values in serialization."""

    model_config = ConfigDict(populate_by_name=True)

    def model_dump(self, **kwargs):
        kwargs.setdefault("exclude_none", True)
        return super().model_dump(**kwargs)

    def model_dump_json(self, **kwargs):
        kwargs.setdefault("exclude_none", True)
        return super().model_dump_json(**kwargs)


# =============================================================================
# Shared sub-models
# =============================================================================


class SessionSummary(SparseModel):
    """Summary of a single conversation session."""

    session: PrefixId = Field(description="Session identifier — pass this back as the `session` param to other tools.")
    project: str | None = Field(default=None, description="Project this session belongs to — pass to the `projects` param to scope other tools to it. Useful when results span projects.")
    date: datetime | None = Field(default=None, description="Timestamp of first message.")
    title: str | None = Field(default=None, description="Auto-generated title from first human message.")
    worktree: str | None = Field(
        default=None,
        description="Git worktree name this session lived in. Absent for the main worktree; present (e.g. 'happy-lehmann') for linked worktrees, which includes Claude Desktop dispatched sessions under `.claude-worktrees/`. Labeled sessions are often programmatically dispatched rather than interactively typed — calibrate signal accordingly.",
    )
    messages: int = Field(description="Total message count (human + assistant turns). Teammate DMs (agent-team orchestration) ARE counted here but NOT in `user_turns`, so a team-worker session legitimately shows user_turns=0 with a high message_count — check `team` to tell that apart from autonomous fan-out.")
    user_turns: int = Field(description="Human prompts — how many times the user actually spoke. A small number against many agents or messages flags a single prompt that fanned out into a long autonomous run. EXCLUDES teammate-injected turns (agent-team orchestration DMs, see `team`) AND interrupt sentinels (mid-turn esc, not a prompt): in a worker session most user-role turns are peer/orchestrator DMs, not human attention, so they don't count here. Aligned with the activity timeline's human_turns.")
    team: str | None = Field(default=None, description="Agent-team name (teamName) when this session is a team worker, else absent. Its user-role turns are mostly teammate-injected (orchestration), not human-typed — see `user_turns`.")
    team_role: str | None = Field(default=None, description="This worker's role in the team (agentName), e.g. 'reviewer-3'. Absent outside agent-team sessions.")
    agents: int = Field(description="Subagents dispatched directly by the parent transcript (Task/Agent/TaskCreate blocks). Top-down view — does NOT count workflow-orchestrated agents.")
    agents_present: int = Field(description="Full discovered subagent population — direct dispatches plus on-disk orphans (notably workflow-orchestrated agents). Matches list_session_agents' total_agents. When this exceeds `agents`, the session ran workflows. The min_agents filter matches on this number.")
    context_tokens: int = Field(description="Last assistant turn's input tokens (context window size).")
    output_tokens: int = Field(description="Total output tokens across all turns.")
    tools: int = Field(description="Total tool_use invocations.")
    is_current: bool | None = Field(
        default=None,
        description="True for the calling conversation itself — the session you are reading this from. Absent for every other session. Use it to skip yourself.",
    )
    is_conversion_artifact: bool | None = Field(
        default=None,
        description="True when this session is a conversion artifact — copied here via convert_session, not an original conversation. Its content duplicates the source transcript. Remove with delete_conversions (converted sessions are managed manually). Absent for normal sessions.",
    )

    @classmethod
    def from_session_info(cls, s: SessionInfo, is_current: bool = False) -> SessionSummary:
        return cls(
            session=s.session_id,
            project=s.project_path,
            date=s.first_timestamp,
            title=s.title,
            worktree=s.worktree,
            messages=s.message_count,
            user_turns=s.user_turns,
            team=s.team,
            team_role=s.team_role,
            agents=s.stats.agent_count,
            agents_present=s.agents_present,
            context_tokens=s.stats.context_tokens,
            output_tokens=s.stats.output_tokens,
            tools=s.stats.tool_use_count,
            is_current=is_current or None,
            is_conversion_artifact=s.is_conversion_artifact or None,
        )


# =============================================================================
# list_projects
# =============================================================================


class ProjectSummary(SparseModel):
    """One logical project on disk, pooled across its git worktrees."""

    path: str = Field(description="Canonical project path (git repo root) — pass to the `projects` param of other tools to scope to it.")
    name: str = Field(description="Project basename, for display.")
    sessions: int = Field(description="Number of conversation transcripts, summed across the project's worktrees.")
    last_active: datetime | None = Field(default=None, description="Most recent transcript modification time.")


class ProjectListResponse(SparseModel):
    """Every project cc-explorer can see, worktrees flattened into one entry each."""

    total: int = Field(description="Number of projects returned.")
    projects: list[ProjectSummary] = Field(description="Projects, most recently active first.")

    @classmethod
    def from_projects(cls, projects: list[ProjectInfo]) -> ProjectListResponse:
        return cls(
            total=len(projects),
            projects=[
                ProjectSummary(
                    path=p.path,
                    name=p.name,
                    sessions=p.session_count,
                    last_active=p.last_active,
                )
                for p in projects
            ],
        )


# =============================================================================
# list_project_sessions
# =============================================================================


class SessionListResponse(SparseModel):
    """List of sessions with stats."""

    total: int = Field(description="Number of sessions returned.")
    sessions: list[SessionSummary] = Field(description="Session summaries, most recent first.")

    @classmethod
    def from_sessions(
        cls,
        sessions: list[SessionInfo],
        current_session: str | None = None,
    ) -> SessionListResponse:
        return cls(
            total=len(sessions),
            sessions=[
                SessionSummary.from_session_info(
                    s, is_current=current_session is not None and s.session_id == current_session
                )
                for s in sessions
            ],
        )


# =============================================================================
# search_projects
# =============================================================================


MAX_EXAMPLES_PER_PATTERN = 10


class SearchHitExample(SparseModel):
    """One example hit for a pattern, tagged with where it lives.

    `project` and `agent` are what make a cross-project, subagent-aware search
    actionable: they tell you which project to switch to and whether the hit was
    inside a subagent body — so you can follow up with `grep_session` (scoped to
    that project) or `get_agent_detail`.
    """

    project: str = Field(description="Project the hit lives in — pass to the `projects` param of other tools to scope to it.")
    session: PrefixId = Field(description="Session containing the hit.")
    date: str | None = Field(default=None, description="Session date (YYYY-MM-DD).")
    agent: PrefixId | None = Field(
        default=None,
        description="Subagent body the hit was in, if any. Absent for the main transcript.",
    )
    excerpt: str = Field(description="Match excerpt centered on the hit.")


class PatternMatch(SparseModel):
    """Results for a single search pattern across every searched project."""

    pattern: str = Field(description="The regex pattern that was searched.")
    hits: int = Field(description="Total match count across all projects/sessions.")
    projects: int = Field(description="Number of distinct projects containing matches.")
    sessions: int = Field(description="Number of sessions containing matches.")
    examples: list[SearchHitExample] | None = Field(
        default=None,
        description="Example hits (project/session/agent tagged), spread across projects, capped to avoid token bloat.",
    )


class SearchProjectsResponse(SparseModel):
    """Pattern-centric search results across one or more projects.

    Summary first: `total_hits` / `projects_searched` up top, then patterns
    sorted by hit count, each carrying the projects + sessions it landed in.
    This is the cross-project recall surface (#21): omit `projects` to search
    everything and find which project a remembered conversation lives in.
    """

    total_hits: int = Field(description="Total matches across all patterns and projects.")
    projects_searched: int = Field(description="How many projects were searched.")
    matches: list[PatternMatch] = Field(
        description="Patterns with hits, sorted by hit count descending. Zero-hit patterns omitted."
    )
    excluded_current_session: PrefixId | None = Field(
        default=None,
        description="The calling conversation was excluded from this search (it would otherwise match itself). Absent when nothing was excluded. Pass include_current_session=true to search it too.",
    )

    @classmethod
    def from_triage(
        cls,
        all_results: PatternTriageResults,
        projects_searched: int,
        excluded_current_session: PrefixId | None = None,
    ) -> SearchProjectsResponse:
        """Build from triage over a flattened cross-project session list.

        Each TriageResult carries its session's `project_path`, so project
        provenance is read straight off the result — no per-project bookkeeping
        in the tool layer.
        """
        matches: list[PatternMatch] = []
        grand_total = 0
        for pat, results in all_results:
            total_hits = sum(r.count for r in results)
            if total_hits == 0:
                continue
            grand_total += total_hits

            distinct_projects = {r.session.project_path for r in results if r.session.project_path}

            # Hottest sessions first; examples capped to bound tokens.
            ranked = sorted(results, key=lambda r: r.count, reverse=True)
            examples = [
                SearchHitExample(
                    project=r.session.project_path or "",
                    session=PrefixId(r.session.session_id),
                    date=format_session_date(r.session.first_timestamp) or None,
                    agent=r.agent_id,
                    excerpt=r.first_match_example,
                )
                for r in ranked
                if r.first_match_example
            ][:MAX_EXAMPLES_PER_PATTERN] or None

            matches.append(
                PatternMatch(
                    pattern=pat,
                    hits=total_hits,
                    projects=len(distinct_projects),
                    sessions=len(results),
                    examples=examples,
                )
            )

        matches.sort(key=lambda m: m.hits, reverse=True)
        return cls(
            total_hits=grand_total,
            projects_searched=projects_searched,
            matches=matches,
            excluded_current_session=excluded_current_session,
        )


# =============================================================================
# grep_session
# =============================================================================


class MatchBlock(SparseModel):
    """A single match with surrounding context turns.

    `before` and `after` are context turns (pipe-delimited entry lines) on
    either side of the match. `match` is the matching entry itself, rendered
    with a centered excerpt when truncated so the hit is always visible.
    """

    agent: PrefixId | None = Field(
        default=None,
        description="Subagent body this match came from (a `subagents/agent-*.jsonl` transcript). Absent when the match is in the main session transcript. Pass it to get_agent_detail to inspect that agent.",
    )
    before: list[str] = Field(
        default_factory=list,
        description="Context turns before the match (pipe-delimited entry lines).",
    )
    match: str = Field(
        description="The matching entry (pipe-delimited entry line). Truncated with a centered excerpt so the pattern hit is visible.",
    )
    after: list[str] = Field(
        default_factory=list,
        description="Context turns after the match (pipe-delimited entry lines).",
    )


class GrepPatternResult(SparseModel):
    """Per-pattern results within a single session.

    Mirrors `search_projects`'s per-pattern shape so you can see at a glance
    which terms landed (`hits`) and which were dead weight. The actual context
    blocks live in `matches`.
    """

    pattern: str = Field(description="The regex pattern that was searched.")
    hits: int = Field(description="Total matches in this session for this pattern.")
    showing: int = Field(description="Number of match blocks returned (after limit).")
    overflow: str | None = Field(
        default=None,
        description="Hint when results were truncated — narrow this pattern or use read_turn.",
    )
    matches: list[MatchBlock] = Field(
        default_factory=list,
        description="Match blocks in chronological order. Empty list for zero-hit patterns.",
    )


class GrepSessionResponse(SparseModel):
    """Matches for one or more patterns within a single session.

    Pattern-centric: each input pattern gets its own hit count and match list,
    so you can tell which terms are productive without re-running. Zero-hit
    patterns are kept in the response (with `hits: 0`) so you see them as
    dead weight rather than guessing why they're missing.
    """

    session: PrefixId = Field(description="Session identifier.")
    project: str | None = Field(
        default=None,
        description="Project this session belongs to. Present when the session was located across projects.",
    )
    worktree: str | None = Field(
        default=None,
        description="Git worktree name this session lived in, if not the main one. Labeled sessions are often dispatch-driven rather than interactively typed.",
    )
    patterns: list[GrepPatternResult] = Field(
        description="Per-pattern results, sorted by hit count descending.",
    )

    @classmethod
    def from_pattern_results(
        cls,
        session_id: str,
        results: list[tuple[str, list[MatchHit], int]],
        truncate: int,
        hide: frozenset[str] = frozenset(),
        worktree: str | None = None,
        project: str | None = None,
    ) -> GrepSessionResponse:
        """Build response from a list of (pattern, matches, total_hits) tuples."""

        pattern_results: list[GrepPatternResult] = []
        for pattern, matches, total in results:
            compiled = re.compile(pattern, re.IGNORECASE)
            match_blocks: list[MatchBlock] = []
            for match in matches:
                before = [
                    format_entry_line(e, truncate=truncate, hide=hide)
                    for e in match.context_before
                ]
                match_line = format_entry_line(
                    match.entry, truncate=truncate, hide=hide, center_pattern=compiled
                )
                after = [
                    format_entry_line(e, truncate=truncate, hide=hide)
                    for e in match.context_after
                ]
                match_blocks.append(
                    MatchBlock(
                        agent=match.agent_id,
                        before=before,
                        match=match_line,
                        after=after,
                    )
                )

            overflow = None
            if len(matches) < total:
                overflow = "narrow this pattern or use read_turn for specific entries"

            pattern_results.append(
                GrepPatternResult(
                    pattern=pattern,
                    hits=total,
                    showing=len(matches),
                    overflow=overflow,
                    matches=match_blocks,
                )
            )

        pattern_results.sort(key=lambda p: p.hits, reverse=True)
        return cls(
            session=PrefixId(session_id),
            project=project,
            worktree=worktree,
            patterns=pattern_results,
        )


class GrepSessionsResponse(SparseModel):
    """Pattern matches across multiple sessions in a single call.

    The fan-out version of `grep_session`: same per-pattern shape per session,
    but you pass N sessions and N patterns and get back one entry per session
    that had at least one hit. Sessions with zero matches across all patterns
    are omitted (intentionally — `not_found` is the way to distinguish a typo
    from an empty result).
    """

    sessions: list[GrepSessionResponse] = Field(
        description="Per-session results, in the order the caller passed them. Only sessions with at least one match across the given patterns are included.",
    )
    not_found: list[str] | None = Field(
        default=None,
        description="Session prefixes the caller passed that did not resolve to any session. Present only when at least one prefix failed to resolve and at least one resolved — when every prefix fails the tool raises ToolError instead.",
    )


# =============================================================================
# read_turn
# =============================================================================


class ReadTurnResponse(SparseModel):
    """A specific moment in a conversation at full fidelity."""

    session: PrefixId | None = Field(default=None, description="Session identifier.")
    project: str | None = Field(
        default=None,
        description="Project this session belongs to. Present when the turn was located across projects.",
    )
    worktree: str | None = Field(
        default=None,
        description="Git worktree name this session lived in, if not the main one.",
    )
    agent: PrefixId | None = Field(
        default=None,
        description="Subagent body this turn lives in (a `subagents/agent-*.jsonl` transcript). Absent when the turn is in the main session transcript.",
    )
    turn: PrefixId = Field(description="Turn identifier.")
    chats: list[str] = Field(
        description="Pipe-delimited entry lines: turn_id|timestamp|role|full_length|display. Full untruncated text unless truncate was set.",
    )

    @classmethod
    def from_entries(
        cls,
        session_info: Optional[SessionInfo],
        turn: str,
        entries: list,
        truncate: int,
        hide: frozenset[str] = frozenset(),
        agent_id: Optional[PrefixId] = None,
    ) -> ReadTurnResponse:

        chats = [format_entry_line(e, truncate=truncate, hide=hide) for e in entries]

        return cls(
            session=PrefixId(session_info.session_id) if session_info else None,
            project=session_info.project_path if session_info else None,
            worktree=session_info.worktree if session_info else None,
            agent=agent_id,
            turn=PrefixId(turn),
            chats=chats,
        )


# =============================================================================
# browse_session
# =============================================================================


class BrowseSessionResponse(SparseModel):
    """First or last N conversation turns from a session."""

    session: PrefixId = Field(description="Session identifier.")
    project: str | None = Field(
        default=None,
        description="Project this session belongs to. Present when the session was located across projects.",
    )
    worktree: str | None = Field(
        default=None,
        description="Git worktree name this session lived in, if not the main one.",
    )
    position: str = Field(description="'head' or 'tail' — which end was read.")
    showing: int = Field(description="Number of turns returned.")
    total_turns: int = Field(description="Total conversation turns in the session.")
    anchor: PrefixId | None = Field(default=None, description="Turn used as anchor, if one was specified.")
    chats: list[str] = Field(
        description="Pipe-delimited entry lines: turn_id|timestamp|role|full_length|display.",
    )

    @classmethod
    def from_entries(
        cls,
        session_id: str,
        position: str,
        entries: list,
        total: int,
        truncate: int,
        anchor: str | None = None,
        hide: frozenset[str] = frozenset(),
        worktree: str | None = None,
        project: str | None = None,
    ) -> BrowseSessionResponse:
        chats = [format_entry_line(e, truncate=truncate, hide=hide) for e in entries]
        return cls(
            session=PrefixId(session_id),
            project=project,
            worktree=worktree,
            position=position,
            showing=len(entries),
            total_turns=total,
            anchor=PrefixId(anchor) if anchor else None,
            chats=chats,
        )


# =============================================================================
# list_session_agents
# =============================================================================


class AgentSummary(SparseModel):
    """Summary of a single subagent spawned during a session."""

    agent_id: PrefixId = Field(description="Agent identifier.")
    tool_use_id: PrefixId = Field(description="Tool use ID that spawned this agent.")
    source: str = Field(
        description="Whether this agent's record is complete and how it relates to the conversation. 'dispatched' — the conversation requested it and its full run is available. 'dispatch_only' — the conversation requested it but no run is available (rejected, never started, or no longer kept), so result/stats/trace will be missing. 'orphan' — it ran with a full record but the conversation didn't request it directly, typically because a workflow spawned it."
    )
    workflow_run_id: str | None = Field(
        default=None,
        description="The workflow run this agent belongs to; null if it wasn't spawned by a workflow. Agents sharing a value ran in the same workflow.",
    )
    is_conversion_artifact: bool | None = Field(
        default=None,
        description="True when this agent is a CONVERSION ARTIFACT — a session or subagent copied here via convert_session, not a real dispatched run. Its transcript duplicates a real one and is excluded from search. Remove it with delete_conversions. Absent for normal agents.",
    )
    date: datetime | None = Field(default=None, description="Timestamp when agent was spawned.")
    type: str = Field(description="Subagent type (e.g. 'general-purpose', 'Explore').")
    status: str = Field(description="Agent status: completed, error, async_launched, unknown.")
    description: str = Field(description="Short description passed to the agent.")
    input_tokens: int | None = Field(default=None, description="Total input tokens (input + cache).")
    output_tokens: int | None = Field(default=None, description="Total output tokens.")
    tools: int | None = Field(default=None, description="Total tool invocations.")
    duration_ms: int | None = Field(default=None, description="Wall-clock duration in milliseconds.")

    @classmethod
    def from_subagent(cls, sa: SubagentInfo) -> AgentSummary:
        return cls(
            agent_id=sa.agent_id,
            tool_use_id=sa.tool_use_id,
            source=sa.source,
            workflow_run_id=sa.workflow_run_id,
            is_conversion_artifact=sa.is_conversion_artifact or None,
            date=sa.timestamp,
            type=sa.subagent_type or "",
            status=sa.status,
            description=sa.description or "",
            input_tokens=sa.total_input_tokens,
            output_tokens=sa.output_tokens,
            tools=sa.total_tool_use_count,
            duration_ms=sa.total_duration_ms,
        )


class SessionAgentsResponse(SparseModel):
    """All agents spawned by a specific session."""

    session: PrefixId = Field(description="Session identifier.")
    project: str | None = Field(
        default=None,
        description="Project this session belongs to. Present when located across projects.",
    )
    worktree: str | None = Field(
        default=None,
        description="Git worktree name this session lived in, if not the main one.",
    )
    date: datetime | None = Field(default=None, description="Timestamp of session start.")
    title: str | None = Field(default=None, description="Session title.")
    total_agents: int = Field(description="Number of agents in this session.")
    agents: list[AgentSummary] = Field(description="Agent summaries in chronological order.")

    @classmethod
    def from_session(
        cls,
        target: SessionInfo,
        agents: list[SubagentInfo],
    ) -> SessionAgentsResponse:
        return cls(
            session=target.session_id,
            project=target.project_path,
            worktree=target.worktree,
            date=target.first_timestamp,
            title=target.title,
            total_agents=len(agents),
            agents=[AgentSummary.from_subagent(sa) for sa in agents],
        )


# =============================================================================
# get_agent_detail
# =============================================================================


class OutputFileInfo(SparseModel):
    """Stats about an agent's saved output file."""

    path: str | None = Field(default=None, description="Resolved file path.")
    size_bytes: int = Field(description="File size in bytes.")
    entries: int = Field(description="Number of transcript entries.")
    compactions: int = Field(description="Number of context compaction events detected.")


class AgentDetailResponse(SparseModel):
    """Full detail for a single agent: prompt, result, stats, optional trace."""

    session: PrefixId = Field(description="Parent session identifier.")
    project: str | None = Field(
        default=None,
        description="Project the parent session belongs to. Present when located across projects.",
    )
    worktree: str | None = Field(
        default=None,
        description="Git worktree name the parent session lived in, if not the main one.",
    )
    date: datetime | None = Field(default=None, description="Timestamp of session start.")
    title: str | None = Field(default=None, description="Session title.")
    agent_id: PrefixId = Field(description="Agent identifier.")
    tool_use_id: PrefixId = Field(description="Tool use ID that spawned this agent.")
    source: str = Field(
        description="Whether this agent's record is complete and how it relates to the conversation. 'dispatched' — the conversation requested it and its full run is available. 'dispatch_only' — the conversation requested it but no run is available (rejected, never started, or no longer kept), so result/stats/trace will be missing. 'orphan' — it ran with a full record but the conversation didn't request it directly, typically because a workflow spawned it."
    )
    workflow_run_id: str | None = Field(
        default=None,
        description="The workflow run this agent belongs to; null if it wasn't spawned by a workflow. Agents sharing a value ran in the same workflow.",
    )
    type: str = Field(description="Subagent type.")
    status: str = Field(description="Agent status.")
    date_started: datetime | None = Field(default=None, description="Timestamp when agent was spawned.")
    input_tokens: int | None = Field(default=None, description="Total input tokens.")
    output_tokens: int | None = Field(default=None, description="Total output tokens.")
    tools: int | None = Field(default=None, description="Total tool invocations.")
    tool_counts: dict[str, int] | None = Field(default=None, description="Tool name -> invocation count.")
    duration_ms: int | None = Field(default=None, description="Wall-clock duration in milliseconds.")
    output_file: OutputFileInfo | None = Field(default=None, description="Output file stats, if the file exists.")
    prompt: str | None = Field(default=None, description="Full prompt passed to the agent.")
    result: str | None = Field(default=None, description="Agent's result text.")
    trace: list[str] | None = Field(
        default=None,
        description="Chronological tool call trace lines: 'HH:MM:SS  ToolName  summary'.",
    )

    @classmethod
    def from_subagent(
        cls,
        found: SubagentInfo,
        found_session: SessionInfo,
        *,
        truncate: int,
        trace: bool = False,
        no_reasoning: bool = False,
        entries_map: Optional[dict] = None,
    ) -> AgentDetailResponse:

        output_file = None
        if found.output_file_exists:
            output_file = OutputFileInfo(
                path=found.output_file_resolved,
                size_bytes=found.output_file_size,
                entries=found.output_entry_count,
                compactions=len(found.compaction_events),
            )

        trace_lines = None
        if trace and entries_map and found.agent_id in entries_map:
            trace_lines = render_trace(
                entries_map[found.agent_id],
                show_reasoning=not no_reasoning,
                truncate=truncate,
            )

        return cls(
            session=found_session.session_id,
            project=found_session.project_path,
            worktree=found_session.worktree,
            date=found_session.first_timestamp,
            title=found_session.title,
            agent_id=found.agent_id,
            tool_use_id=found.tool_use_id,
            source=found.source,
            workflow_run_id=found.workflow_run_id,
            type=found.subagent_type or "",
            status=found.status,
            date_started=found.timestamp,
            input_tokens=found.total_input_tokens,
            output_tokens=found.output_tokens,
            tools=found.total_tool_use_count,
            tool_counts=found.tool_name_counts or None,
            duration_ms=found.total_duration_ms,
            output_file=output_file,
            prompt=found.prompt or None,
            result=found.result_text or None,
            trace=trace_lines,
        )


class AgentListResponse(SparseModel):
    """Multiple agent details (when get_agent_detail receives multiple IDs)."""

    agents: list[AgentDetailResponse] = Field(description="Detail for each requested agent.")


# =============================================================================
# audit_session_tools
# =============================================================================


class AgentToolCall(SparseModel):
    """A single tool invocation inside a subagent's transcript."""

    time: str = Field(description="HH:MM:SS local timestamp.")
    tool: str = Field(description="Short tool name (after the last `__` for MCP tools).")
    input_summary: str = Field(description="Truncated tool input args, single-line.")
    error: bool = Field(default=False, description="True when the tool result was an error or no-match.")
    error_text: str | None = Field(default=None, description="Truncated error message when error=True.")


class AgentToolAudit(SparseModel):
    """Per-agent tool usage audit: counts, error rate, full chronological trace."""

    agent_id: PrefixId = Field(description="Agent identifier.")
    source: str = Field(
        description="Whether this agent's record is complete and how it relates to the conversation. 'dispatched' — the conversation requested it and its full run is available. 'dispatch_only' — the conversation requested it but no run is available (rejected, never started, or no longer kept), so result/stats/trace will be missing. 'orphan' — it ran with a full record but the conversation didn't request it directly, typically because a workflow spawned it."
    )
    workflow_run_id: str | None = Field(
        default=None,
        description="The workflow run this agent belongs to; null if it wasn't spawned by a workflow. Agents sharing a value ran in the same workflow.",
    )
    type: str = Field(description="Subagent type.")
    description: str = Field(description="Short description passed to the agent.")
    tool_call_count: int = Field(description="Total tool invocations made by this agent.")
    error_count: int = Field(description="Tool calls whose result was an error or no-match.")
    tool_counts: dict[str, int] = Field(description="Short tool name → invocation count.")
    calls: list[AgentToolCall] = Field(
        default_factory=list,
        description="Chronological list of tool calls. Filtered by `tool_name_filter` if provided.",
    )


class SessionToolAuditResponse(SparseModel):
    """Per-subagent tool usage audit for a single session.

    Built to answer "are my agents using my tools right?" without writing a
    one-off script. For each subagent in the session, returns tool counts,
    error rate, and a chronological trace of tool calls (optionally filtered
    by name substring).
    """

    session: PrefixId = Field(description="Session identifier.")
    project: str | None = Field(
        default=None,
        description="Project this session belongs to. Present when located across projects.",
    )
    worktree: str | None = Field(
        default=None,
        description="Git worktree name this session lived in, if not the main one.",
    )
    title: str | None = Field(default=None, description="Session title.")
    total_present: int = Field(
        description="How many subagents the session ran in total, including any spawned by workflows. The real measure of fan-out."
    )
    total_audited: int = Field(
        description="How many of those agents could be inspected. When this is less than total_present, the difference is agents that left no inspectable record (rejected, never ran, or unreadable) — they are absent from `agents`."
    )
    total_tool_calls: int = Field(description="Aggregate tool calls across all audited agents.")
    total_errors: int = Field(description="Aggregate error/no-match results across all audited agents.")
    tool_name_filter: str | None = Field(
        default=None,
        description="Substring filter applied to tool names, if any.",
    )
    agents: list[AgentToolAudit] = Field(description="Per-agent audits in dispatch order.")


# =============================================================================
# Activity timeline (get_activity_timeline)
# =============================================================================
#
# A cross-project attention timeline over a time window. The payload leads with
# rollups (window, summary) because truncation eats the bottom — the sessions
# list and the sparse timeline grid, the two large sections, come last.
#
# Timestamp labels are all rendered in `window.tz`: "Day MM-DD HH:MM" for
# session start/end and the summary/day peaks, "MM-DD HH:MM" for timeline keys,
# "HH:MM" for within-day fields. Session `id`s are 8-char prefixes and `project`
# values are repo names — both can be passed straight back to the other
# cc-explorer tools (read_turn, grep_session, list_session_agents, ...).


class ActivityWindow(SparseModel):
    """The resolved time window and grid grain."""

    after: str = Field(description="Window start (inclusive), ISO-8601 in `tz`.")
    before: str = Field(description="Window end (exclusive), ISO-8601 in `tz`.")
    tz: str = Field(description="IANA tz name all labels and day/hour bucketing use.")
    bucket_minutes: int = Field(description="Grid grain in minutes.")
    days: int = Field(description="Number of whole days the window spans (DST-robust: a 7-local-day window reports 7 even when it straddles a DST transition). The top-level `days` array is keyed by local calendar date and may contain one MORE entry than this when the window's edges land mid-day across calendar boundaries.")


class ActivityProjectRollup(SparseModel):
    """Per-project aggregates. Interactive and headless are both reflected, but
    active_min/human_turns count interactive work only."""

    project: str = Field(description="Repo name — pass to `projects` on other tools.")
    sessions: int = Field(description="Interactive sessions touching this window.")
    headless_sessions: int = Field(description="Headless (sdk-cli) sessions — machine runs.")
    active_min: int = Field(description="Minutes with >=1 interactive human turn in this project (union across its sessions).")
    human_turns: int = Field(description="Interactive human turns in-window.")
    turn_min: int = Field(description="Sum of turn_duration minutes (interactive + headless). FLOOR — interrupted turns emit no duration.")


class ActivityInteractiveSummary(SparseModel):
    """Interactive (human-driven) attention rollup. Headless work is excluded."""

    sessions: int = Field(description="Distinct interactive sessions with in-window activity.")
    active_min: int = Field(description="Minutes with >=1 interactive human turn in any session = (#such buckets) x bucket_minutes.")
    multitask_min: int = Field(description="Minutes with >=2 distinct interactive sessions taking human turns in the same bucket.")
    peak_sessions_driven: int = Field(description="Max distinct interactive sessions with a human turn in one bucket.")
    peak_at: str | None = Field(default=None, description="When peak_sessions_driven occurred (Day MM-DD HH:MM).")
    peak_autonomous_sessions: int = Field(description="Max distinct sessions with agent activity and NO human turn in one bucket.")
    peak_autonomous_at: str | None = Field(default=None, description="When peak_autonomous_sessions occurred.")
    human_turns: int = Field(description="Interactive human turns (interrupts excluded).")
    interrupts: int = Field(description="Times the human stopped the agent mid-turn (esc) across interactive sessions. A fact, no valence.")
    machine_hours: int | float = Field(description="Sum of interactive turn_min, in hours. FLOOR.")
    team_sessions: int = Field(description="Interactive sessions belonging to an agent team (non-null team). Their user-role turns are mostly teammate-injected, not human-typed — counted as agent activity, not attention.")


class ActivityHeadlessSummary(SparseModel):
    """Headless (sdk-cli) rollup — automated/cron/SDK runs, segregated from attention."""

    sessions: int = Field(description="Headless sessions with in-window activity.")
    human_turns: int = Field(description="Internal human turns in headless transcripts (orchestration prompts, not human attention).")
    machine_hours: int | float = Field(description="Sum of headless turn_min, in hours.")


class ActivitySummary(SparseModel):
    """Top-of-payload rollup: interactive attention, headless machine work, by-project."""

    interactive: ActivityInteractiveSummary
    headless: ActivityHeadlessSummary
    by_project: list[ActivityProjectRollup] = Field(description="Per-project rollups, busiest first.")


class ActivityDay(SparseModel):
    """One local calendar day, interactive sessions only."""

    date: str = Field(description="Local date label (Day MM-DD).")
    active_min: int = Field(description="Minutes this day with >=1 interactive human turn.")
    multitask_min: int = Field(description="Minutes this day with >=2 interactive sessions driven at once.")
    peak: int = Field(description="Max distinct interactive sessions driven in one bucket this day.")
    peak_at: str | None = Field(default=None, description="When the day's peak occurred (HH:MM).")
    human_turns_by_hour: list[int] = Field(description="24 ints — interactive human turns per local hour (0-23).")
    sessions_driven_by_hour: list[int] = Field(description="24 ints — distinct interactive sessions with >=1 human turn in that local hour (union of session ids across the hour's buckets; a session active across several buckets of the hour counts once).")
    agent_turns_by_hour: list[int] = Field(description="24 ints — interactive agent turns per local hour.")


class ActivitySession(SparseModel):
    """One session's roll-up. id and project pass straight back to other tools."""

    id: PrefixId = Field(description="8-char session id — pass as `session` to read_turn/grep_session/list_session_agents.")
    project: str = Field(description="Repo name — pass to `projects`.")
    headless: bool = Field(description="True for sdk-cli (claude -p / SDK / cron). Machine work, excluded from interactive rollups.")
    entrypoint: str | None = Field(default=None, description="Raw entrypoint value, e.g. 'cli' (interactive) or 'sdk-cli' (headless); other values are possible. Don't switch on it for the interactive/headless split — the `headless` boolean is authoritative.")
    team: str | None = Field(default=None, description="Agent-team name (teamName) when this session is a team worker, else null. Its user-role turns are mostly teammate-injected (orchestration), not human-typed.")
    team_role: str | None = Field(default=None, description="This worker's role in the team (agentName), e.g. 'reviewer-3'. null outside agent-team sessions.")
    model: str | None = Field(default=None, description="Dominant (most frequent) assistant model id in-window.")
    branches: list[str] = Field(description="Distinct gitBranch values, first-appearance order.")
    start: str | None = Field(default=None, description="First in-window activity bucket (Day MM-DD HH:MM).")
    end: str | None = Field(default=None, description="Last in-window activity bucket (Day MM-DD HH:MM).")
    human_turns: int = Field(description="Non-interrupt human turns in-window.")
    agent_turns: int = Field(description="Agent turns (deduped API requests + tool-result markers); subagent activity folded in.")
    amplification: float | None = Field(default=None, description="agent_turns / human_turns. null when human_turns == 0.")
    n_sub: int = Field(description="Subagents with any in-window activity, folded into this session.")
    turn_min: int = Field(description="Sum of turn_duration minutes (incl. folded subagents). FLOOR.")
    human_active_min: int = Field(description="Buckets with >=1 human turn x bucket_minutes.")
    agent_only_min: int = Field(description="Buckets with agent activity and 0 human turns x bucket_minutes.")
    interrupts: int = Field(description="Mid-turn stops (esc) in this session. A fact, no valence.")
    title: str | None = Field(default=None, description="Auto title from first human message (same logic as list_project_sessions).")
    opening: str | None = Field(default=None, description="First non-interrupt human turn, whitespace-collapsed, ~300 chars.")
    closing: str | None = Field(default=None, description="Last non-interrupt human turn, whitespace-collapsed, ~200 chars.")
    summary: str | None = Field(default=None, description="Latest stored Claude Code summary entry, or null if none.")


class ActivityTimelineResponse(SparseModel):
    """Cross-project activity timeline. Summary first (survives truncation),
    then per-day rollups, the per-session list, and last the sparse grid."""

    window: ActivityWindow
    summary: ActivitySummary
    days: list[ActivityDay] = Field(description="One row per local calendar day, interactive only.")
    sessions: list[ActivitySession] = Field(description="Interactive sessions first (turn_min desc), then headless (turn_min desc).")
    timeline: dict[str, dict[str, list[int]]] = Field(
        description="Time-major sparse grid: {'MM-DD HH:MM': {session_id: [human_turns, agent_turns]}}. Active buckets only; ALL sessions incl. headless; subagents folded into the parent as agent turns."
    )


# =============================================================================
# convert_session
# =============================================================================
#
# Lead with the actionable: `operation`/`direction`/`created_id`/`invocation`
# come first so the agent sees "what was created and how to use it" before the
# diagnostic detail (environment, models, lineage) that survives truncation
# below it.


class ConversionModels(SparseModel):
    """Assistant model history of the converted transcript."""

    first: str | None = Field(default=None, description="First assistant model id in the copy.")
    last: str | None = Field(default=None, description="Last assistant model id in the copy.")
    counts: dict[str, int] = Field(
        default_factory=dict, description="model id -> number of assistant turns."
    )


class ConversionEnvironment(SparseModel):
    """The converted conversation's original runtime context.

    A converted conversation has no live environment of its own — these are read
    off the source transcript so the caller can judge whether the cwd/branch it
    assumed still exist before acting on its answers.
    """

    original_cwd: str | None = Field(default=None, description="cwd the source ran in.")
    cwd_exists: bool = Field(description="Whether original_cwd still exists on disk.")
    original_branch: str | None = Field(default=None, description="gitBranch the source ran on.")
    branch_exists: bool | None = Field(
        default=None,
        description="Whether original_branch is still a valid ref. null when the cwd is gone or isn't a git repo (can't tell).",
    )
    cc_version: str | None = Field(default=None, description="Claude Code version the source ran under.")
    last_timestamp: str | None = Field(default=None, description="Timestamp of the source's last turn.")
    age_days: int | None = Field(default=None, description="Whole days from the source's last turn to now.")


class ConvertSessionResponse(SparseModel):
    """Result of a session<->subagent conversion (always a copy).

    Leads with what was created and the exact next step (`invocation`). The
    source transcript is never touched. `suggested_handoff` is the first message
    to send a converted session, since a copied conversation can't tell its
    interlocutor changed.
    """

    operation: str = Field(description="Always 'copy' — the source is never modified, moved, or deleted.")
    direction: str = Field(description="'session_to_subagent' or 'subagent_to_session'.")
    created_id: str = Field(description="The new agent id (session_to_subagent) or session uuid (subagent_to_session).")
    invocation: str = Field(description="The exact next step to use the created artifact.")
    parent_session: str | None = Field(
        default=None,
        description="session_to_subagent: the session the new subagent is parented under.",
    )
    title: str | None = Field(default=None, description="subagent_to_session: the new session's custom title.")
    project: str | None = Field(default=None, description="subagent_to_session: the project the new session was written to.")
    turns: int = Field(description="Conversation turns copied (user+assistant), after trailing trim.")
    trimmed_trailing: int = Field(description="Trailing noise turns dropped (empty/interrupt/command scaffolding).")
    dropped_branches: int | None = Field(
        default=None,
        description="Lines dropped because they were on abandoned edit-branches or embedded sidechain turns not on the active thread. 0 when nothing was dropped; absent when no walk was possible (fallback to file order).",
    )
    tail_state: str = Field(description="'clean' (ends on an assistant turn) or 'pending_user_input' (ends on a real user turn awaiting a reply).")
    nested_agents: int | None = Field(
        default=None,
        description="session_to_subagent only: subagents the SOURCE session ran. They are NOT copied — their results already appear inline in the conversation. Absent/0 for subagent sources.",
    )
    models: ConversionModels = Field(description="Assistant model history of the copy.")
    environment: ConversionEnvironment = Field(description="The source's original cwd/branch/version/age.")
    suggested_handoff: str | None = Field(
        default=None,
        description="session_to_subagent only: the first message to send the converted subagent, telling it its interlocutor changed (senders are not labeled on the wire).",
    )
    lineage: list[dict[str, str]] = Field(
        description="Accumulated conversion chain: each hop is {as: 'session'|'subagent', id: ...}, oldest first.",
    )

    @classmethod
    def from_result(cls, r) -> "ConvertSessionResponse":
        if r.direction == "session_to_subagent":
            invocation = f'SendMessage(to: "{r.created_id}")'
        else:
            invocation = (
                f"claude -r {r.created_id}   "
                f'(or: claude --resume "{r.title}" — resolves by title too)'
            )
        return cls(
            operation="copy",
            direction=r.direction,
            created_id=r.created_id,
            invocation=invocation,
            parent_session=r.parent_session,
            title=r.title,
            project=r.project,
            turns=r.turns,
            trimmed_trailing=r.trimmed_trailing,
            dropped_branches=r.dropped_branches if r.dropped_branches is not None else None,
            tail_state=r.tail_state,
            nested_agents=(r.nested_agents or None) if r.direction == "session_to_subagent" else None,
            models=ConversionModels(**r.models),
            environment=ConversionEnvironment(**r.environment),
            suggested_handoff=r.suggested_handoff,
            lineage=r.lineage,
        )


# =============================================================================
# delete_conversions
# =============================================================================


class DeletedConversion(SparseModel):
    """One conversion artifact that was deleted."""

    id: str = Field(description="The agent id or session id deleted.")
    kind: str = Field(description="'subagent' or 'session'.")
    path: str = Field(description="Transcript file that was removed.")


class RefusedDeletion(SparseModel):
    """One id that was NOT deleted, with the reason."""

    id: str = Field(description="The id that was refused.")
    reason: str = Field(description="Why it wasn't deleted: not found, not a conversion artifact (no provenance line), grown since creation (resumed/built upon), or a converted session (humans manage those).")


class DeleteConversionsResponse(SparseModel):
    """Result of delete_conversions: what was removed and what was refused.

    Only SUBAGENT artifacts carrying a valid x-converter-provenance line AND
    unchanged since creation are deletable. Grown artifacts, converted sessions,
    and non-conversion files are refused with a per-id reason rather than touched.
    """

    deleted: list[DeletedConversion] = Field(
        default_factory=list, description="Conversion artifacts removed."
    )
    refused: list[RefusedDeletion] = Field(
        default_factory=list, description="Ids not removed, each with a reason."
    )
