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

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator

from .utils import PrefixId, smart_truncate


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
    id: PrefixId
    name: str
    input: dict[str, Any]
    caller: Optional[dict] = None


class ToolResultContent(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: PrefixId
    content: Union[str, list[dict[str, Any]]]
    is_error: Optional[bool] = None
    agentId: Optional[PrefixId] = None


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
    uuid: PrefixId
    parentUuid: Optional[PrefixId] = None
    timestamp: datetime
    sessionId: PrefixId
    isSidechain: bool = False
    userType: str = ""
    cwd: str = ""
    version: str = ""
    agentId: Optional[PrefixId] = None
    gitBranch: Optional[str] = None

    def display(self, truncate: int) -> str:
        """Short display string for unknown entry kinds (no role/id; pipe line carries that)."""
        _ = truncate  # unused here; same signature as HumanEntry / AssistantTranscriptEntry
        return "[?]"


class HumanEntry(BaseTranscriptEntry):
    """Actual human messages — the user talking."""
    type: Literal["user"]
    message: UserMessageModel
    isMeta: Optional[bool] = None

    def display(self, truncate: int) -> str:
        text = extract_text(self)
        return smart_truncate(text, truncate)


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

    def display(self, truncate: int) -> str:
        text = extract_text(self)
        tool_summaries: list[str] = []
        for item in self.message.content:
            if isinstance(item, ToolUseContent):
                detail = format_tool_input(item.name, item.input, truncate=truncate)
                tool_summaries.append(f"→ {item.name}({detail})")
        parts: list[str] = []
        if text:
            parts.append(smart_truncate(text, truncate))
        if tool_summaries:
            parts.append("  ".join(tool_summaries))
        return "  ".join(parts) if parts else ""


class SummaryTranscriptEntry(BaseModel):
    """Context compaction summary."""
    type: Literal["summary"]
    summary: str
    leafUuid: PrefixId
    cwd: Optional[str] = None
    sessionId: Optional[PrefixId] = None


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
    sessionId: PrefixId
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


# =============================================================================
# Text extraction (moved from parser.py)
# =============================================================================


def _strip_system_xml(text: str) -> str:
    """Remove system/metadata XML from raw string messages.

    Keeps meaningful content inside <result> tags.
    """
    text = re.sub(r"<usage>[\s\S]*?</usage>", "", text)
    text = re.sub(r"</?task-notification>", "", text)
    text = re.sub(r"<task-id>[^<]*</task-id>", "", text)
    text = re.sub(r"<tool-use-id>[^<]*</tool-use-id>", "", text)
    text = re.sub(r"<status>[^<]*</status>", "", text)
    text = re.sub(r"<summary>[^<]*</summary>", "", text)
    text = re.sub(r"</?result>", "", text)
    text = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>", "", text)
    text = re.sub(
        r"</?(?:command-name|command-message|command-args|local-command-stdout|"
        r"local-command-caveat|user-prompt-submit-hook)>[^<]*",
        "",
        text,
    )
    text = re.sub(r"Full transcript available at:.*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(entry: Union[HumanEntry, AssistantTranscriptEntry]) -> str:
    """Extract readable text from an entry's content blocks.

    Joins TextContent.text values. Handles str | list[ContentItem] content.
    Strips system XML wrappers from raw string content.
    """
    content = entry.message.content

    if isinstance(content, str):
        return _strip_system_xml(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, TextContent):
            text = _strip_system_xml(item.text).strip()
            if text and not text.startswith("[Request interrupted by user"):
                parts.append(text)
    return "\n".join(parts)


# =============================================================================
# Tool input summarization (moved from formatting.py)
# =============================================================================


def format_tool_input(name: str, inp: dict[str, Any], truncate: int = 80) -> str:
    """Format a tool's input for display.

    truncate controls detail level (0 = full input, N = cap at N chars).
    Per-tool-name logic picks the most relevant field to show;
    truncate controls how much of it is visible.
    """
    import json

    if truncate == 0:
        # Full input — JSON-format the entire input dict
        return json.dumps(inp, indent=2, default=str)

    if name == "Read" and "file_path" in inp:
        return smart_truncate(inp["file_path"], truncate)
    if name in ("navigate", "WebFetch") and "url" in inp:
        return smart_truncate(inp["url"], truncate)
    if name == "javascript_tool" and "text" in inp:
        return smart_truncate(inp["text"], truncate)
    if name == "Grep" and "pattern" in inp:
        s = f"/{inp['pattern']}/"
        if "path" in inp:
            s += f" {inp['path']}"
        return smart_truncate(s, truncate)
    if name == "Glob" and "pattern" in inp:
        s = inp["pattern"]
        if "path" in inp:
            s += f" in {inp['path']}"
        return smart_truncate(s, truncate)
    if name == "Edit" and "file_path" in inp:
        return smart_truncate(inp["file_path"], truncate)
    if name == "Write" and "file_path" in inp:
        return smart_truncate(inp["file_path"], truncate)
    if name == "Bash" and "command" in inp:
        return smart_truncate(inp["command"], truncate)
    # Default: stringify and truncate
    return smart_truncate(str(inp), truncate)
