"""Extract subagent spawn/completion data from a session transcript.

Parses agent tool_use blocks (spawn) and their corresponding tool_result
blocks (completion) to build a list of subagents with metadata: agentId,
timestamp, status, tokens, duration, description, prompt, and result text.

Three tool names dispatch agents:
- "Agent" — current foreground subagent tool (has subagent_type, prompt, description)
- "Task" — older name for the same foreground tool (identical shape, used pre-Mar 2026)
- "TaskCreate" — background task API (has subject, description, activeForm)

Async agents report back via task-notification entries later in the
transcript. We parse those to update status from async_launched to
completed (or whatever the notification says).

Optionally joins against saved .output files from --task-output-dir to
report output file stats and compaction detection.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Union

from .models import (
    AGENT_TOOL_NAMES,
    AssistantTranscriptEntry,
    CompactionEvent,
    ContentItem,
    QueueOperationTranscriptEntry,
    TextContent,
    ToolResultContent,
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    TranscriptStats,
)
from .parser import load_transcript
from .utils import PrefixId


@dataclass
class SubagentInfo:
    """Metadata about a single subagent spawned during a session."""

    tool_use_id: PrefixId
    agent_id: PrefixId = PrefixId("")
    subagent_type: str = ""
    description: str = ""
    prompt: str = ""
    result_text: str = ""
    status: str = "unknown"
    timestamp: Optional[datetime] = None

    # Completion stats (from toolUseResult on completed agents)
    # Optional here is meaningful — distinguishes "no data" from "zero usage"
    total_duration_ms: Optional[int] = None
    total_tokens: Optional[int] = None
    total_tool_use_count: Optional[int] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cache_creation_input_tokens: Optional[int] = None
    cache_read_input_tokens: Optional[int] = None

    # Tool name frequency from output file
    tool_name_counts: dict[str, int] = field(default_factory=dict)

    # Output file (from async_launched or /tmp path)
    output_file: str = ""
    output_file_resolved: str = ""  # actual path found (may differ if --task-output-dir)
    output_file_exists: bool = False
    output_file_size: int = 0
    output_entry_count: int = 0
    compaction_events: list[CompactionEvent] = field(default_factory=list)

    @property
    def total_input_tokens(self) -> Optional[int]:
        """Total input tokens (input + cache_creation + cache_read)."""
        if self.input_tokens is None:
            return None
        return (
            (self.input_tokens or 0)
            + (self.cache_creation_input_tokens or 0)
            + (self.cache_read_input_tokens or 0)
        )


def _parse_agent_spawn(
    tool_id: str, tool_name: str, inp: dict[str, Any], timestamp: datetime
) -> SubagentInfo:
    """Create SubagentInfo from a tool_use block's input.

    Agent/Task share the same fields (subagent_type, description, prompt).
    TaskCreate uses different fields (subject, description, activeForm).
    """
    if tool_name == "TaskCreate":
        return SubagentInfo(
            tool_use_id=tool_id,
            subagent_type="background",
            description=inp.get("subject", inp.get("description", "")),
            prompt=inp.get("description", ""),
            timestamp=timestamp,
        )
    # Agent or Task — same shape
    return SubagentInfo(
        tool_use_id=tool_id,
        subagent_type=inp.get("subagent_type", ""),
        description=inp.get("description", ""),
        prompt=inp.get("prompt", ""),
        timestamp=timestamp,
    )


def extract_subagents(transcript_path: Path) -> list[SubagentInfo]:
    """Parse a session transcript and extract all agent tool spawns with results.

    Uses typed transcript entries from load_transcript(). Detects three tool
    names: Agent (current), Task (older, same shape), and TaskCreate
    (background task API, different fields).

    Handles three result patterns:
    1. Sync completed — toolUseResult dict with status=completed, full stats
    2. Async launched — toolUseResult dict with status=async_launched, outputFile
       Completion arrives later via task-notification XML in a user entry
    3. Rejected — toolUseResult is string containing "rejected"

    Returns SubagentInfo list ordered by appearance in the transcript.
    """
    entries = load_transcript(transcript_path)

    spawns: dict[str, SubagentInfo] = {}  # keyed by tool_use_id
    spawn_order: list[str] = []
    # Reverse lookup: agentId -> tool_use_id (for task-notification matching)
    agent_id_map: dict[str, str] = {}

    for entry in entries:
        if isinstance(entry, AssistantTranscriptEntry):
            for item in entry.message.content:
                if isinstance(item, ToolUseContent) and item.name in AGENT_TOOL_NAMES:
                    info = _parse_agent_spawn(
                        item.id, item.name, item.input, entry.timestamp
                    )
                    spawns[item.id] = info
                    spawn_order.append(item.id)

        elif isinstance(entry, ToolResultEntry):
            tur = entry.toolUseResult
            content_items = _content_as_list(entry.message.content)

            # Case 1a: dict toolUseResult with agentId (Agent/Task sync or async_launched)
            if isinstance(tur, dict) and "agentId" in tur:
                matched_id = _find_tool_use_id(content_items, spawns)
                if matched_id:
                    info = spawns[matched_id]
                    info.agent_id = tur.get("agentId", "")
                    info.status = tur.get("status", "unknown")
                    info.output_file = tur.get("outputFile", "")

                    # Build reverse lookup for task-notification matching
                    if info.agent_id:
                        agent_id_map[info.agent_id] = matched_id

                    _apply_completion_stats(info, tur)

                    # Extract result text from sync completions
                    if info.status not in ("async_launched", "rejected"):
                        info.result_text = _extract_tool_result_text(
                            content_items, matched_id
                        )

            # Case 1b: dict toolUseResult with task (TaskCreate result)
            elif isinstance(tur, dict) and "task" in tur:
                matched_id = _find_tool_use_id(content_items, spawns)
                if matched_id:
                    task_info = tur["task"]
                    info = spawns[matched_id]
                    info.agent_id = str(task_info.get("id", ""))
                    info.status = "background"
                    # Build reverse lookup using task ID
                    if info.agent_id:
                        agent_id_map[info.agent_id] = matched_id

            # Case 2: rejected tool call (string toolUseResult)
            elif isinstance(tur, str) and "rejected" in tur.lower():
                matched_id = _find_tool_use_id(content_items, spawns)
                if matched_id:
                    spawns[matched_id].status = "rejected"

            # Case 3: task-notification in content
            notification_text = _extract_notification_text(content_items)
            if notification_text:
                _update_from_notification(notification_text, spawns, agent_id_map)

        # Case 4: task-notification in queue-operation entry
        elif isinstance(entry, QueueOperationTranscriptEntry):
            notification_text = _extract_notification_text(entry.content)
            if notification_text:
                _update_from_notification(notification_text, spawns, agent_id_map)

    return [spawns[tid] for tid in spawn_order if tid in spawns]


def _content_as_list(
    content: Union[str, list[ContentItem]],
) -> list[ContentItem]:
    """Normalize message content to a list (parser usually does this, but be safe)."""
    if isinstance(content, list):
        return content
    return []


def _apply_completion_stats(info: SubagentInfo, tur: dict[str, Any]) -> None:
    """Apply token/duration stats from a toolUseResult dict."""
    if "totalDurationMs" in tur:
        info.total_duration_ms = tur["totalDurationMs"]
    if "totalTokens" in tur:
        info.total_tokens = tur["totalTokens"]
    if "totalToolUseCount" in tur:
        info.total_tool_use_count = tur["totalToolUseCount"]

    usage = tur.get("usage", {})
    if usage:
        info.input_tokens = usage.get("input_tokens")
        info.output_tokens = usage.get("output_tokens")
        info.cache_creation_input_tokens = usage.get("cache_creation_input_tokens")
        info.cache_read_input_tokens = usage.get("cache_read_input_tokens")


def _extract_notification_text(
    content: Union[str, list[ContentItem], None],
) -> Optional[str]:
    """Extract task-notification XML from entry content."""
    if content is None:
        return None
    if isinstance(content, str) and "<task-notification>" in content:
        return content
    if isinstance(content, list):
        for item in content:
            if isinstance(item, TextContent) and "<task-notification>" in item.text:
                return item.text
    return None


def _update_from_notification(
    text: str,
    spawns: dict[str, SubagentInfo],
    agent_id_map: dict[str, str],
) -> None:
    """Parse a task-notification XML string and update the matching subagent."""
    task_id_match = re.search(r"<task-id>([^<]+)</task-id>", text)
    status_match = re.search(r"<status>([^<]+)</status>", text)
    if not task_id_match:
        return

    agent_id = task_id_match.group(1).strip()
    status = status_match.group(1).strip() if status_match else "completed"

    # Look up which spawn this agent corresponds to
    tool_use_id = agent_id_map.get(agent_id)
    if not tool_use_id or tool_use_id not in spawns:
        return

    info = spawns[tool_use_id]
    info.status = status

    # Extract result text from notification
    result_match = re.search(r"<result>([\s\S]*?)</result>", text)
    if result_match and not info.result_text:
        info.result_text = result_match.group(1).strip()

    # Extract usage stats if present in the notification
    usage_match = re.search(r"<usage>([\s\S]*?)</usage>", text)
    if usage_match:
        usage_text = usage_match.group(1)
        tokens_match = re.search(r"total_tokens:\s*(\d+)", usage_text)
        tools_match = re.search(r"tool_uses:\s*(\d+)", usage_text)
        duration_match = re.search(r"duration_ms:\s*(\d+)", usage_text)

        if tokens_match and info.total_tokens is None:
            info.total_tokens = int(tokens_match.group(1))
        if tools_match and info.total_tool_use_count is None:
            info.total_tool_use_count = int(tools_match.group(1))
        if duration_match and info.total_duration_ms is None:
            info.total_duration_ms = int(duration_match.group(1))


def _find_tool_use_id(
    content: list[ContentItem],
    spawns: dict[str, SubagentInfo],
) -> Optional[str]:
    """Find the tool_use_id in content that matches a known spawn."""
    for item in content:
        if isinstance(item, ToolResultContent) and item.tool_use_id in spawns:
            return item.tool_use_id
    return None


def _extract_tool_result_text(
    content: list[ContentItem],
    tool_use_id: str,
) -> str:
    """Extract result text from a sync agent's tool_result content."""
    for item in content:
        if isinstance(item, ToolResultContent) and item.tool_use_id == tool_use_id:
            if isinstance(item.content, str):
                return item.content
            elif isinstance(item.content, list):
                parts = []
                for block in item.content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        parts.append(block.get("text", ""))
                return "\n".join(parts)
    return ""


def resolve_output_files(
    subagents: list[SubagentInfo],
    task_output_dir: Optional[Path] = None,
) -> None:
    """Check output file availability and optionally join with saved copies.

    Mutates SubagentInfo in place: sets output_file_resolved, output_file_exists,
    output_file_size.
    """
    # Build agentId -> saved file map from task_output_dir
    saved_files: dict[str, Path] = {}
    if task_output_dir and task_output_dir.is_dir():
        for output_path in task_output_dir.glob("*.output"):
            agent_id = _read_agent_id(output_path)
            if agent_id:
                saved_files[agent_id] = output_path

    for info in subagents:
        # Try saved files first (by agentId)
        if info.agent_id and info.agent_id in saved_files:
            resolved = saved_files[info.agent_id]
            info.output_file_resolved = str(resolved)
            info.output_file_exists = True
            info.output_file_size = resolved.stat().st_size
            continue

        # Fall back to original outputFile path
        if info.output_file:
            p = Path(info.output_file)
            info.output_file_resolved = str(p)
            if p.exists():
                info.output_file_exists = True
                info.output_file_size = p.stat().st_size


def scan_output_file_stats(
    subagents: list[SubagentInfo],
    keep_entries: bool = False,
) -> dict[str, list[TranscriptEntry]]:
    """For subagents with available output files, compute stats and detect compaction.

    Uses load_transcript + TranscriptStats.from_entries() to parse the output
    file, then backfills any missing stats on SubagentInfo (tokens, tools,
    duration) and populates compaction events.

    Also counts tool name frequency into info.tool_name_counts.

    Returns {agent_id: entries} when keep_entries=True, empty dict otherwise.
    """
    entries_map: dict[str, list[TranscriptEntry]] = {}

    for info in subagents:
        if not info.output_file_exists or not info.output_file_resolved:
            continue

        path = Path(info.output_file_resolved)
        try:
            entries = load_transcript(path)
        except OSError:
            continue

        info.output_entry_count = len(entries)

        stats = TranscriptStats.from_entries(entries)
        info.compaction_events = list(stats.compaction_events)

        # Count tool names
        tool_counter: Counter[str] = Counter()
        for entry in entries:
            if isinstance(entry, AssistantTranscriptEntry):
                for item in entry.message.content:
                    if isinstance(item, ToolUseContent):
                        tool_counter[item.name] += 1
        info.tool_name_counts = dict(tool_counter.most_common())

        # Backfill stats that weren't available from the session transcript
        if info.input_tokens is None and stats.input_tokens > 0:
            info.input_tokens = stats.input_tokens
        if info.output_tokens is None and stats.output_tokens > 0:
            info.output_tokens = stats.output_tokens
        if info.total_tool_use_count is None and stats.tool_use_count > 0:
            info.total_tool_use_count = stats.tool_use_count
        if info.total_duration_ms is None and stats.duration_ms is not None:
            info.total_duration_ms = stats.duration_ms

        if keep_entries and info.agent_id:
            entries_map[info.agent_id] = entries

    return entries_map


def _read_agent_id(path: Path) -> Optional[str]:
    """Read agentId from line 1 of an .output file."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            first_line = f.readline().strip()
            if first_line:
                data = json.loads(first_line)
                return data.get("agentId")
    except (json.JSONDecodeError, OSError):
        pass
    return None
