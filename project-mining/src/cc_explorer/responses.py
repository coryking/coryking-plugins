"""Pydantic response models for cc-explorer MCP tools.

FastMCP auto-generates output schemas from return type annotations.
These models ARE the output documentation — field names, types, and
descriptions appear in the tool schema the agent sees.

Conversation text in chats arrays uses pipe-delimited entry line format:
  timestamp|role|turn_id|full_length|display
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict, Field

from .formatting import format_entry_line, format_session_date, format_session_ref, render_trace
from .utils import PrefixId

if TYPE_CHECKING:
    from .search import MatchHit, PatternTriageResults, SessionInfo
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

    session_id: PrefixId = Field(description="Session identifier.")
    date: datetime | None = Field(default=None, description="Timestamp of first message.")
    title: str | None = Field(default=None, description="Auto-generated title from first human message.")
    messages: int = Field(description="Total message count.")
    agents: int = Field(description="Number of subagent dispatches.")
    context_tokens: int = Field(description="Last assistant turn's input tokens (context window size).")
    output_tokens: int = Field(description="Total output tokens across all turns.")
    tools: int = Field(description="Total tool_use invocations.")

    @classmethod
    def from_session_info(cls, s: SessionInfo) -> SessionSummary:
        return cls(
            session_id=s.session_id,
            date=s.first_timestamp,
            title=s.title,
            messages=s.message_count,
            agents=s.stats.agent_count,
            context_tokens=s.stats.context_tokens,
            output_tokens=s.stats.output_tokens,
            tools=s.stats.tool_use_count,
        )


# =============================================================================
# list_project_sessions / list_agent_sessions
# =============================================================================


class SessionListResponse(SparseModel):
    """List of sessions with stats."""

    total: int = Field(description="Number of sessions returned.")
    sessions: list[SessionSummary] = Field(description="Session summaries, most recent first.")

    @classmethod
    def from_sessions(cls, sessions: list[SessionInfo]) -> SessionListResponse:
        return cls(
            total=len(sessions),
            sessions=[SessionSummary.from_session_info(s) for s in sessions],
        )


# =============================================================================
# search_project
# =============================================================================


MAX_EXAMPLES_PER_PATTERN = 7


class PatternMatch(SparseModel):
    """Results for a single search pattern across all sessions."""

    pattern: str = Field(description="The regex pattern that was searched.")
    hits: int = Field(description="Total match count across all sessions.")
    sessions: int = Field(description="Number of sessions containing matches.")
    examples: list[str] | None = Field(
        default=None,
        description="Pipe-delimited: 'session_id|YYYY-MM-DD|excerpt'. Capped to avoid token bloat.",
    )


class SearchProjectResponse(SparseModel):
    """Pattern-centric search results across a project's chat history."""

    matches: list[PatternMatch] = Field(
        description="Patterns with hits, sorted by hit count descending. Zero-hit patterns omitted."
    )

    @classmethod
    def from_triage(
        cls,
        all_results: PatternTriageResults,
        excerpt_width: int = 150,
    ) -> SearchProjectResponse:
        matches: list[PatternMatch] = []

        for pat, results in all_results:
            total_hits = sum(r.count for r in results)
            if total_hits == 0:
                continue

            all_examples = [
                f"{PrefixId(r.session.session_id)}|{format_session_date(r.session.first_timestamp)}|{r.first_match_example}"
                for r in results
                if r.first_match_example
            ]
            examples = all_examples[:MAX_EXAMPLES_PER_PATTERN] or None

            matches.append(
                PatternMatch(
                    pattern=pat,
                    hits=total_hits,
                    sessions=len(results),
                    examples=examples,
                )
            )

        matches.sort(key=lambda m: m.hits, reverse=True)
        return cls(matches=matches)


# =============================================================================
# grep_session
# =============================================================================


class MatchBlock(SparseModel):
    """A single match with surrounding context turns."""

    chats: list[str] = Field(
        description="Pipe-delimited entry lines: timestamp|role|turn_id|full_length|display. Match entry surrounded by context turns.",
    )


class GrepSessionResponse(SparseModel):
    """Matches for a pattern within a single session."""

    session_id: PrefixId = Field(description="Session identifier.")
    showing: int = Field(description="Number of matches returned.")
    total_hits: int = Field(description="Total matches found (may exceed showing if overflow).")
    overflow: str | None = Field(
        default=None,
        description="Hint when results were truncated — narrow your pattern or use read_turn.",
    )
    matches: list[MatchBlock] = Field(
        description="Match blocks in chronological order, each a window of turns around a hit.",
    )

    @classmethod
    def from_matches(
        cls,
        session_id: str,
        matches: list[MatchHit],
        total: int,
        limit: int,
        tool_detail: int = 80,
    ) -> GrepSessionResponse:

        overflow = None
        if len(matches) < total:
            overflow = "narrow your pattern or use read_turn for specific entries"

        match_blocks: list[MatchBlock] = []
        for match in matches:
            chats: list[str] = [format_entry_line(e, tool_detail=tool_detail) for e in match.context_before]
            chats.append(format_entry_line(match.entry, tool_detail=tool_detail))
            chats.extend(format_entry_line(e, tool_detail=tool_detail) for e in match.context_after)
            match_blocks.append(MatchBlock(chats=chats))

        return cls(
            session_id=PrefixId(session_id),
            showing=len(matches),
            total_hits=total,
            overflow=overflow,
            matches=match_blocks,
        )


# =============================================================================
# read_turn
# =============================================================================


class ReadTurnResponse(SparseModel):
    """A specific moment in a conversation at full fidelity."""

    session_id: PrefixId | None = Field(default=None, description="Session identifier.")
    turn_id: PrefixId = Field(description="Turn identifier.")
    chats: list[str] = Field(
        description="Pipe-delimited entry lines: timestamp|role|turn_id|full_length|display. Full untruncated text unless limit was set.",
    )

    @classmethod
    def from_entries(
        cls,
        session_info: Optional[SessionInfo],
        turn: str,
        entries: list,
        limit: int | None = None,
        tool_detail: int = 80,
    ) -> ReadTurnResponse:

        truncate = limit if limit else 0
        chats = [format_entry_line(e, truncate=truncate, tool_detail=tool_detail) for e in entries]

        return cls(
            session_id=PrefixId(session_info.session_id) if session_info else None,
            turn_id=PrefixId(turn),
            chats=chats,
        )


# =============================================================================
# browse_session
# =============================================================================


class BrowseSessionResponse(SparseModel):
    """First or last N conversation turns from a session."""

    session_id: PrefixId = Field(description="Session identifier.")
    position: str = Field(description="'head' or 'tail' — which end was read.")
    showing: int = Field(description="Number of turns returned.")
    total_turns: int = Field(description="Total conversation turns in the session.")
    anchor: PrefixId | None = Field(default=None, description="Turn used as anchor, if one was specified.")
    chats: list[str] = Field(
        description="Pipe-delimited entry lines: timestamp|role|turn_id|full_length|display.",
    )

    @classmethod
    def from_entries(
        cls,
        session_id: str,
        position: str,
        entries: list,
        total: int,
        truncate: int = 0,
        tool_detail: int = 80,
        anchor: str | None = None,
    ) -> BrowseSessionResponse:
        chats = [format_entry_line(e, truncate=truncate, tool_detail=tool_detail) for e in entries]
        return cls(
            session_id=PrefixId(session_id),
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

    session_id: PrefixId = Field(description="Session identifier.")
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
            session_id=target.session_id,
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

    session_id: PrefixId = Field(description="Parent session identifier.")
    date: datetime | None = Field(default=None, description="Timestamp of session start.")
    title: str | None = Field(default=None, description="Session title.")
    agent_id: PrefixId = Field(description="Agent identifier.")
    tool_use_id: PrefixId = Field(description="Tool use ID that spawned this agent.")
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
        trace: bool = False,
        no_reasoning: bool = False,
        entries_map: Optional[dict] = None,
        tool_detail: int = 80,
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
                tool_detail=tool_detail,
            )

        return cls(
            session_id=found_session.session_id,
            date=found_session.first_timestamp,
            title=found_session.title,
            agent_id=found.agent_id,
            tool_use_id=found.tool_use_id,
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
