# Adapted from claude-code-log by Daniel Demmel (MIT License)
# https://github.com/daaain/claude-code-log
"""JSONL parsing into typed transcript entries.

Core functions:
- load_transcript(path) — parse a JSONL file into typed entries
- load_conversations(project_path) — find all JSONL files for a project
- extract_text — re-exported from models.py
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence, Union, cast

from pydantic import BaseModel

from .utils import PrefixId
from .models import (
    AssistantTranscriptEntry,
    ContentItem,
    FileSnapshotEntry,
    HumanEntry,
    ImageContent,
    MetaEntry,
    ProgressEntry,
    QueueOperationTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    TextContent,
    ThinkingContent,
    ToolResultContent,
    ToolResultEntry,
    ToolUseContent,
    TranscriptEntry,
    UserMessageModel,
)


# =============================================================================
# Content Item Creation
# =============================================================================

CONTENT_ITEM_CREATORS: dict[str, type[BaseModel]] = {
    "text": TextContent,
    "tool_result": ToolResultContent,
    "image": ImageContent,
    "tool_use": ToolUseContent,
    "thinking": ThinkingContent,
}

USER_CONTENT_TYPES: Sequence[str] = ("text", "tool_result", "image")
ASSISTANT_CONTENT_TYPES: Sequence[str] = ("text", "tool_use", "thinking")


def create_content_item(
    item_data: dict[str, Any],
    type_filter: Sequence[str] | None = None,
) -> ContentItem:
    """Create a ContentItem from raw data using the registry."""
    try:
        content_type = item_data.get("type", "")
        if type_filter is None or content_type in type_filter:
            model_class = CONTENT_ITEM_CREATORS.get(content_type)
            if model_class is not None:
                return cast(ContentItem, model_class.model_validate(item_data))
        return TextContent(type="text", text=str(item_data))
    except Exception:
        return TextContent(type="text", text=str(item_data))


def create_message_content(
    content_data: Any,
    type_filter: Sequence[str] | None = None,
) -> list[ContentItem]:
    """Normalize message content (string or list) into list[ContentItem]."""
    if isinstance(content_data, str):
        return [TextContent(type="text", text=content_data)]
    elif isinstance(content_data, list):
        result: list[ContentItem] = []
        for item in content_data:
            if isinstance(item, dict):
                result.append(create_content_item(item, type_filter))
            else:
                result.append(TextContent(type="text", text=str(item)))
        return result
    else:
        return [TextContent(type="text", text=str(content_data))]


# =============================================================================
# Transcript Entry Creation
# =============================================================================


def _create_user_entry(data: dict[str, Any]) -> Union[HumanEntry, ToolResultEntry, MetaEntry]:
    """Create the appropriate user entry type based on flags.

    Split logic:
    - toolUseResult present → ToolResultEntry
    - isMeta=True → MetaEntry
    - otherwise → HumanEntry

    Special case: raw string content containing <task-notification> is
    classified as ToolResultEntry (subagent results delivered as raw strings
    without the toolUseResult flag).
    """
    data_copy = data.copy()

    # Normalize message content
    if "message" in data_copy and "content" in data_copy["message"]:
        data_copy["message"] = data_copy["message"].copy()
        data_copy["message"]["content"] = create_message_content(
            data_copy["message"]["content"],
            USER_CONTENT_TYPES,
        )

    # Parse list-type toolUseResult (MCP tool results)
    if "toolUseResult" in data_copy and isinstance(data_copy["toolUseResult"], list):
        tool_use_result = data_copy["toolUseResult"]
        if (
            tool_use_result
            and isinstance(tool_use_result[0], dict)
            and "type" in tool_use_result[0]
        ):
            data_copy["toolUseResult"] = [
                create_content_item(item)
                for item in tool_use_result
                if isinstance(item, dict)
            ]

    # Dispatch to the right entry type
    if data_copy.get("toolUseResult") is not None:
        return ToolResultEntry.model_validate(data_copy)

    if data_copy.get("isMeta"):
        return MetaEntry.model_validate(data_copy)

    # Check for task-notification in raw string content (subagent results
    # that arrive without the toolUseResult flag)
    raw_content = data_copy.get("message", {}).get("content", "")
    if isinstance(raw_content, str) and "<task-notification>" in raw_content:
        # Treat as tool result even though it lacks the flag
        data_copy["toolUseResult"] = raw_content
        return ToolResultEntry.model_validate(data_copy)

    return HumanEntry.model_validate(data_copy)


def _create_assistant_entry(data: dict[str, Any]) -> AssistantTranscriptEntry:
    """Create an AssistantTranscriptEntry from raw data."""
    data_copy = data.copy()
    if "message" in data_copy and "content" in data_copy["message"]:
        message_copy = data_copy["message"].copy()
        message_copy["content"] = create_message_content(
            message_copy["content"],
            ASSISTANT_CONTENT_TYPES,
        )
        data_copy["message"] = message_copy
    return AssistantTranscriptEntry.model_validate(data_copy)


def _create_queue_operation_entry(data: dict[str, Any]) -> QueueOperationTranscriptEntry:
    data_copy = data.copy()
    if "content" in data_copy and isinstance(data_copy["content"], list):
        data_copy["content"] = create_message_content(data_copy["content"])
    return QueueOperationTranscriptEntry.model_validate(data_copy)


def create_transcript_entry(data: dict[str, Any]) -> TranscriptEntry:
    """Create a typed TranscriptEntry from a JSON dictionary.

    Dispatches on the 'type' field. Unknown types raise ValueError.
    """
    entry_type = data.get("type")
    if entry_type == "user":
        return _create_user_entry(data)
    elif entry_type == "assistant":
        return _create_assistant_entry(data)
    elif entry_type == "summary":
        return SummaryTranscriptEntry.model_validate(data)
    elif entry_type == "system":
        return SystemTranscriptEntry.model_validate(data)
    elif entry_type == "queue-operation":
        return _create_queue_operation_entry(data)
    elif entry_type == "progress":
        return ProgressEntry.model_validate(data)
    elif entry_type == "file-history-snapshot":
        return FileSnapshotEntry.model_validate(data)
    else:
        raise ValueError(f"Unknown transcript entry type: {entry_type}")


# =============================================================================
# High-level API
# =============================================================================


@dataclass
class CachedTranscript:
    """Parsed transcript entries with the file mtime at parse time."""

    mtime: float
    entries: list[TranscriptEntry]


_cache: dict[Path, CachedTranscript] = {}


def load_transcript(path: Path) -> list[TranscriptEntry]:
    """Load a JSONL file into typed transcript entries.

    Caches results by (path, mtime) — the MCP server is a persistent process,
    so the cache lives across tool calls. Re-parses only when the file changes.

    Skips malformed lines and unknown entry types. Returns all entry types.
    """
    resolved = path.resolve()
    mtime = resolved.stat().st_mtime

    cached = _cache.get(resolved)
    if cached is not None and cached.mtime == mtime:
        return cached.entries

    entries: list[TranscriptEntry] = []
    with open(resolved, "r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                entry = create_transcript_entry(data)
                entries.append(entry)
            except (json.JSONDecodeError, ValueError, Exception):
                continue

    _cache[resolved] = CachedTranscript(mtime=mtime, entries=entries)
    return entries


def load_conversations(project_path: str) -> dict[str, Path]:
    """Find all JSONL conversation files for a project.

    Returns {session_id: path} mapping where session_id is the UUID
    from the filename (without .jsonl extension).

    Uses the Claude Agent SDK for path resolution — handles long paths
    with hash suffixes, Bun/Node hash mismatches, CLAUDE_CONFIG_DIR,
    and git worktree scanning.
    """
    from claude_agent_sdk._internal.sessions import _canonicalize_path, _find_project_dir

    project_path_resolved = str(Path(project_path).expanduser().resolve())
    canonical = _canonicalize_path(project_path_resolved)
    claude_dir = _find_project_dir(canonical)

    if claude_dir is None or not claude_dir.exists():
        return {}

    result: dict[PrefixId, Path] = {}
    for jsonl_path in claude_dir.glob("*.jsonl"):
        session_id = PrefixId(jsonl_path.stem)
        result[session_id] = jsonl_path
    return result


# extract_text and _strip_system_xml moved to models.py
from .models import extract_text  # noqa: F401 — re-export for external consumers
