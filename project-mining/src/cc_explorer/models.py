# Adapted from claude-code-log by Daniel Demmel (MIT License)
# https://github.com/daaain/claude-code-log
"""Typed Pydantic models for Claude Code JSONL transcript entries.

Design: UserTranscriptEntry from claude-code-log is split into three classes
so isinstance() is the filtering mechanism:
- HumanEntry — actual human messages
- ToolResultEntry — tool output fed back to model (has toolUseResult)
- MetaEntry — system-injected messages (has isMeta)

Additional entry types for records that claude-code-log skips:
- ProgressEntry — streaming progress records (~87% of some subagent files)
- FileSnapshotEntry — file-history-snapshot records (~10% of main transcripts)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator


# Coerce None → 0 for token fields that the API may return as null
NoneAsZero = Annotated[int, BeforeValidator(lambda v: 0 if v is None else v)]


# =============================================================================
# Content Models
# =============================================================================


class TextContent(BaseModel):
    type: Literal["text"]
    text: str


class ImageSource(BaseModel):
    type: Literal["base64"]
    media_type: str
    data: str


class ImageContent(BaseModel):
    type: Literal["image"]
    source: ImageSource


class ThinkingContent(BaseModel):
    type: Literal["thinking"]
    thinking: str
    signature: Optional[str] = None


class ToolUseContent(BaseModel):
    type: Literal["tool_use"]
    id: str
    name: str
    input: dict[str, Any]
    caller: Optional[dict] = None


class ToolResultContent(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: str
    content: Union[str, list[dict[str, Any]]]
    is_error: Optional[bool] = None
    agentId: Optional[str] = None


ContentItem = Union[
    TextContent,
    ToolUseContent,
    ToolResultContent,
    ThinkingContent,
    ImageContent,
]


# =============================================================================
# Message Models
# =============================================================================


class UsageInfo(BaseModel):
    input_tokens: NoneAsZero = 0
    cache_creation_input_tokens: NoneAsZero = 0
    cache_read_input_tokens: NoneAsZero = 0
    output_tokens: NoneAsZero = 0
    service_tier: Optional[str] = None
    server_tool_use: Optional[dict[str, Any]] = None


class UserMessageModel(BaseModel):
    role: Literal["user"]
    content: Union[str, list[ContentItem]]


class AssistantMessageModel(BaseModel):
    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    model: str
    content: list[ContentItem]
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Optional[UsageInfo] = None


# Flexible type for toolUseResult field
ToolUseResult = Union[
    str,
    list[Any],
    dict[str, Any],
]


# =============================================================================
# Transcript Entry Models
# =============================================================================


class BaseTranscriptEntry(BaseModel):
    """Common fields across all transcript entries."""
    uuid: str
    parentUuid: Optional[str] = None
    timestamp: datetime
    sessionId: str
    isSidechain: bool = False
    userType: str = ""
    cwd: str = ""
    version: str = ""
    agentId: Optional[str] = None
    gitBranch: Optional[str] = None


class HumanEntry(BaseTranscriptEntry):
    """Actual human messages — the user talking."""
    type: Literal["user"]
    message: UserMessageModel
    isMeta: Optional[bool] = None


class ToolResultEntry(BaseTranscriptEntry):
    """Tool output fed back to the model (has toolUseResult)."""
    type: Literal["user"]
    message: UserMessageModel
    toolUseResult: Optional[ToolUseResult] = None
    isMeta: Optional[bool] = None
    agentId: Optional[str] = None


class MetaEntry(BaseTranscriptEntry):
    """System-injected messages — skill loads, command caveats (isMeta=True)."""
    type: Literal["user"]
    message: UserMessageModel
    isMeta: Literal[True] = True
    toolUseResult: Optional[ToolUseResult] = None


class AssistantTranscriptEntry(BaseTranscriptEntry):
    """Assistant response with content blocks."""
    type: Literal["assistant"]
    message: AssistantMessageModel
    requestId: Optional[str] = None


class SummaryTranscriptEntry(BaseModel):
    """Context compaction summary."""
    type: Literal["summary"]
    summary: str
    leafUuid: str
    cwd: Optional[str] = None
    sessionId: Optional[str] = None


class SystemTranscriptEntry(BaseTranscriptEntry):
    """System messages — warnings, notifications, hook summaries."""
    type: Literal["system"]
    content: Optional[str] = None
    subtype: Optional[str] = None
    level: Optional[str] = None
    hasOutput: Optional[bool] = None
    hookErrors: Optional[list[str]] = None
    hookInfos: Optional[list[dict[str, Any]]] = None
    preventedContinuation: Optional[bool] = None


class QueueOperationTranscriptEntry(BaseModel):
    """Queue operations for message queueing tracking."""
    type: Literal["queue-operation"]
    operation: Literal["enqueue", "dequeue", "remove", "popAll"]
    timestamp: datetime
    sessionId: str
    content: Optional[Union[list[ContentItem], str]] = None


class ProgressEntry(BaseModel):
    """Streaming progress records — bulk of subagent files, always skipped."""
    type: Literal["progress"]
    model_config = {"extra": "allow"}


class FileSnapshotEntry(BaseModel):
    """File history snapshot records."""
    type: Literal["file-history-snapshot"]
    model_config = {"extra": "allow"}


# The union of all entry types the parser can produce
TranscriptEntry = Union[
    HumanEntry,
    ToolResultEntry,
    MetaEntry,
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    QueueOperationTranscriptEntry,
    ProgressEntry,
    FileSnapshotEntry,
]


# Tool names that dispatch agents (foreground or background)
AGENT_TOOL_NAMES = {"Agent", "Task", "TaskCreate"}


@dataclass
class CompactionEvent:
    """A detected context compaction in a transcript."""

    turn: int
    from_tokens: int
    to_tokens: int
    drop_pct: float


@dataclass
class TranscriptStats:
    """Summary stats computed from a list of transcript entries.

    Works on any transcript — main sessions and subagent .output files
    share the same JSONL format.

    context_tokens: last assistant turn's input (actual context window size)
    input_tokens: total input across all turns
    output_tokens: total output across all turns (new tokens generated)
    duration_ms: elapsed time from first to last entry timestamp
    compaction_events: detected context window compactions (>30% drop from peak)
    """

    context_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_use_count: int = 0
    agent_count: int = 0
    duration_ms: Optional[int] = None
    compaction_events: list[CompactionEvent] = field(default_factory=list)

    @classmethod
    def from_entries(cls, entries: list[TranscriptEntry]) -> TranscriptStats:
        stats = cls()
        peak_context = 0
        prev_context = 0
        turn_num = 0
        first_ts: Optional[datetime] = None
        last_ts: Optional[datetime] = None

        for entry in entries:
            # Track timestamps from typed entries (not getattr)
            if isinstance(entry, (BaseTranscriptEntry, QueueOperationTranscriptEntry)):
                if first_ts is None:
                    first_ts = entry.timestamp
                last_ts = entry.timestamp

            if not isinstance(entry, AssistantTranscriptEntry):
                continue
            usage = entry.message.usage
            if usage:
                turn_num += 1
                turn_input = (
                    usage.input_tokens
                    + usage.cache_creation_input_tokens
                    + usage.cache_read_input_tokens
                )
                if turn_input > 0:
                    stats.context_tokens = turn_input  # overwrite — last real turn wins
                stats.input_tokens += turn_input
                stats.output_tokens += usage.output_tokens

                # Compaction detection: context drops >30% from peak
                if turn_input > peak_context:
                    peak_context = turn_input
                if prev_context > 10000 and turn_input < prev_context * 0.7:
                    drop_pct = (1 - turn_input / prev_context) * 100
                    stats.compaction_events.append(CompactionEvent(
                        turn=turn_num,
                        from_tokens=prev_context,
                        to_tokens=turn_input,
                        drop_pct=drop_pct,
                    ))
                prev_context = turn_input

            for item in entry.message.content:
                if isinstance(item, ToolUseContent):
                    stats.tool_use_count += 1
                    if item.name in AGENT_TOOL_NAMES:
                        stats.agent_count += 1

        # Duration from first to last timestamp — datetime math, no parsing
        if first_ts and last_ts and first_ts != last_ts:
            stats.duration_ms = int((last_ts - first_ts).total_seconds() * 1000)

        return stats
