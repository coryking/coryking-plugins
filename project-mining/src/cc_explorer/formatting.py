"""Formatting and display helpers for cc-explorer output.

All functions return strings rather than printing directly.
"""

import csv
import io
from typing import Optional

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    TextContent,
    ToolUseContent,
    TranscriptEntry,
)
from .parser import extract_text
from .search import SessionInfo, TriageResult, SearchResult
from .subagents import SubagentInfo
from .utils import format_duration, format_timestamp, format_tokens, short_uuid


STATUS_LABELS = {
    "async_launched": "running (async)",
    "completed": "completed",
    "rejected": "rejected",
    "unknown": "unknown",
}


# =============================================================================
# Entry-level formatting (from cli.py)
# =============================================================================


def role_label(entry: TranscriptEntry) -> str:
    """Return a human-readable role label for a transcript entry."""
    if isinstance(entry, HumanEntry):
        return "USER"
    elif isinstance(entry, AssistantTranscriptEntry):
        return "ASSISTANT"
    return "UNKNOWN"


def format_tool_call(item: ToolUseContent, max_val_len: int = 80) -> str:
    """Format a single tool_use block as a compact one-liner.

    -> ToolName(key="value", key2="value2")
    """
    parts: list[str] = []
    for key, val in item.input.items():
        s = str(val)
        if len(s) > max_val_len:
            s = s[: max_val_len - 3] + "..."
        if isinstance(val, str):
            s = f'"{s}"'
        parts.append(f"{key}={s}")
    return f"→ {item.name}({', '.join(parts)})"


def extract_tool_calls(entry: TranscriptEntry) -> list[str]:
    """Extract formatted tool call summaries from an assistant entry."""
    if not isinstance(entry, AssistantTranscriptEntry):
        return []
    return [
        format_tool_call(item)
        for item in entry.message.content
        if isinstance(item, ToolUseContent)
    ]


def format_entry_line(
    entry: TranscriptEntry, is_match: bool = False, truncate: int = 500
) -> str:
    """Format a single entry for display, including tool calls."""
    role = role_label(entry)
    uuid = (
        short_uuid(entry.uuid)
        if isinstance(entry, BaseTranscriptEntry)
        else "--------"
    )
    text = ""
    if isinstance(entry, (HumanEntry, AssistantTranscriptEntry)):
        text = extract_text(entry)

    tool_calls = extract_tool_calls(entry)
    if tool_calls:
        tool_text = "  ".join(tool_calls)
        if text:
            text = f"{text}  {tool_text}"
        else:
            text = tool_text

    if truncate and len(text) > truncate:
        text = text[: truncate - 3] + "..."
    text = text.replace("\n", "\\n")
    marker = "  ← match" if is_match else ""
    return f"[{role} turn:{uuid}] {text}{marker}"


# =============================================================================
# Search result formatting (from cli.py)
# =============================================================================


def format_triage_results(all_results: list[tuple[str, TriageResult]]) -> str:
    """Format triage/count results as CSV text."""
    lines: list[str] = []
    total = sum(r.count for _, r in all_results)
    lines.append(f"{total} matches across {len(all_results)} pattern/session pairs")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["count", "pattern", "session", "date", "snippet"])
    for pat, r in all_results:
        sid = short_uuid(r.session.session_id)
        date = format_timestamp(r.session.first_timestamp)
        snippet = r.first_match_snippet or r.session.title
        writer.writerow([r.count, pat, sid, date, snippet])
    lines.append(buf.getvalue().rstrip("\n"))
    return "\n".join(lines)


def format_search_results(result: SearchResult, pattern: str) -> str:
    """Format search results with context.

    Summary info always comes first -- tools truncate from the bottom.
    """
    lines: list[str] = []

    if result.overflow:
        lines.append(
            f"Found {result.total_matches} matches across "
            f"{len(result.per_session)} sessions. "
            f"Showing {len(result.matches)} samples. "
            f"Narrow your pattern or use --session to target a specific conversation."
        )
        lines.append("")
        lines.append("Per-session counts:")
        for r in result.per_session:
            sid = short_uuid(r.session.session_id)
            date = format_timestamp(r.session.first_timestamp)
            lines.append(
                f'  {r.count:>4}  session:{sid}  {date}  "{r.session.title}"'
            )
        lines.append("")
        lines.append("Sample hits:")
    else:
        lines.append(f"{result.total_matches} matches")
        lines.append("")

    for i, match in enumerate(result.matches):
        sid = short_uuid(match.session_id)
        tid = short_uuid(match.turn_uuid)
        lines.append(f"--- match {i + 1} [session:{sid} turn:{tid}] ---")
        for entry in match.context_before:
            lines.append(format_entry_line(entry))
        lines.append(format_entry_line(match.entry, is_match=True))
        for entry in match.context_after:
            lines.append(format_entry_line(entry))
        lines.append("")

    return "\n".join(lines)


def format_quote(
    session_info: Optional[SessionInfo],
    turn: str,
    context: int,
    entries: list[TranscriptEntry],
) -> str:
    """Format a quote view centered on a specific turn."""
    lines: list[str] = []
    sid = short_uuid(session_info.session_id) if session_info else "--------"
    tid = short_uuid(turn)
    lines.append(f"session:{sid}  turn:{tid} (± {context} messages)")
    lines.append("")

    for entry in entries:
        is_target = isinstance(entry, BaseTranscriptEntry) and entry.uuid == turn
        lines.append(format_entry_line(entry, is_match=is_target, truncate=0))

    return "\n".join(lines)


# =============================================================================
# Agent manifest / session / detail formatting (from cli_agents.py)
# =============================================================================


def format_manifest_view(agent_sessions: list[SessionInfo]) -> str:
    """Format the manifest view: all sessions that spawned agents."""
    lines: list[str] = []
    lines.append(f"{len(agent_sessions)} sessions with subagents")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["agents", "tools", "date", "session", "title"])
    for s in agent_sessions:
        writer.writerow(
            [
                s.stats.agent_count,
                s.stats.tool_use_count,
                format_timestamp(s.first_timestamp),
                short_uuid(s.session_id),
                s.title,
            ]
        )
    lines.append(buf.getvalue().rstrip("\n"))
    return "\n".join(lines)


def format_session_view(
    target: SessionInfo,
    agents: list[SubagentInfo],
    compaction: bool = False,
) -> str:
    """Format the session view: list all agents spawned in a session.

    Returns session metadata and agent CSV data combined.
    """
    lines: list[str] = []

    date = format_timestamp(target.first_timestamp)
    sid = short_uuid(target.session_id)
    lines.append(f'Session: {sid}  {date}  "{target.title}"')
    lines.append(f"{len(agents)} subagent(s) spawned")

    if not agents:
        return "\n".join(lines)

    # CSV table
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "#",
            "agent_id",
            "started",
            "type",
            "status",
            "input_tokens",
            "output_tokens",
            "tools",
            "duration_ms",
            "description",
        ]
    )
    for i, sa in enumerate(agents, 1):
        writer.writerow(
            [
                i,
                short_uuid(sa.agent_id),
                format_timestamp(sa.timestamp),
                sa.subagent_type or "",
                STATUS_LABELS.get(sa.status, sa.status),
                sa.total_input_tokens or "",
                sa.output_tokens or "",
                sa.total_tool_use_count or "",
                sa.total_duration_ms or "",
                sa.description or "",
            ]
        )
    lines.append(buf.getvalue().rstrip("\n"))

    # Output files section
    output_files_text = format_output_files(agents)
    if output_files_text:
        lines.append(output_files_text)

    # Compaction detail
    if compaction:
        compaction_text = format_compaction(agents)
        if compaction_text:
            lines.append(compaction_text)

    lines.append("")
    lines.append(
        "Tip: use get_agent_detail with an agent ID for full prompt, result, and details."
    )
    return "\n".join(lines)


def format_agent_detail(
    found: SubagentInfo,
    found_session: SessionInfo,
    trace: bool = False,
    no_reasoning: bool = False,
    entries_map: Optional[dict] = None,
    compaction: bool = False,
) -> str:
    """Format full detail for a single agent."""
    lines: list[str] = []

    sid = short_uuid(found_session.session_id)
    date = format_timestamp(found_session.first_timestamp)
    lines.append(f'Session: {sid}  {date}  "{found_session.title}"')
    lines.append(
        f"Agent:   {short_uuid(found.agent_id)}  "
        f"{found.subagent_type or '-'}  "
        f"{found.status}"
    )
    lines.append(f"Started: {format_timestamp(found.timestamp)}")

    if found.total_input_tokens is not None:
        lines.append(f"Input:   {format_tokens(found.total_input_tokens)}")
    if found.output_tokens is not None:
        lines.append(f"Output:  {format_tokens(found.output_tokens)}")

    if found.total_tool_use_count is not None:
        tools_line = f"Tools:   {found.total_tool_use_count}"
        if found.tool_name_counts:
            parts = [
                f"{name}: {count}" for name, count in found.tool_name_counts.items()
            ]
            tools_line += f" ({', '.join(parts)})"
        lines.append(tools_line)

    lines.append(f"Duration: {format_duration(found.total_duration_ms)}")

    # Output file info
    if found.output_file_exists:
        size_kb = found.output_file_size // 1024
        path = found.output_file_resolved
        compactions = len(found.compaction_events)
        comp_str = (
            f", {compactions} compaction(s)" if compactions else ", no compaction"
        )
        lines.append(
            f"File:    {path} ({size_kb}KB, "
            f"{found.output_entry_count} entries{comp_str})"
        )
        if compaction and found.compaction_events:
            for evt in found.compaction_events:
                from_k = evt.from_tokens // 1000
                to_k = evt.to_tokens // 1000
                lines.append(
                    f"         Turn {evt.turn}: {from_k}K -> {to_k}K "
                    f"(-{evt.drop_pct:.1f}%)"
                )
    elif found.output_file:
        lines.append(f"File:    {found.output_file} (missing)")

    # Prompt and result
    lines.append("")
    if found.prompt:
        lines.append("<prompt>")
        lines.append(found.prompt)
        lines.append("</prompt>")
    else:
        lines.append("<prompt />")

    if found.result_text:
        lines.append("")
        lines.append("<result>")
        lines.append(found.result_text)
        lines.append("</result>")

    # Trace
    if trace and entries_map and found.agent_id in entries_map:
        lines.append("")
        lines.append(render_trace(entries_map[found.agent_id], show_reasoning=not no_reasoning))

    return "\n".join(lines)


# =============================================================================
# Shared display helpers (from cli_agents.py)
# =============================================================================


def format_output_files(agents: list[SubagentInfo]) -> str:
    """Format output file section for a list of agents."""
    with_output_path = [
        (i, sa)
        for i, sa in enumerate(agents, 1)
        if sa.output_file or sa.output_file_resolved
    ]
    if not with_output_path:
        return ""

    lines: list[str] = []
    found = [sa for _, sa in with_output_path if sa.output_file_exists]
    missing = [sa for _, sa in with_output_path if not sa.output_file_exists]

    if not found and missing:
        lines.append("")
        lines.append(
            f"Output files: all {len(missing)} missing (temp files cleaned up). "
            f"Use --task-output-dir if saved elsewhere."
        )
    else:
        lines.append("")
        lines.append("Output files:")
        for i, sa in with_output_path:
            path = sa.output_file_resolved or sa.output_file
            if sa.output_file_exists:
                size_kb = sa.output_file_size // 1024
                compactions = len(sa.compaction_events)
                comp_str = (
                    f", {compactions} compaction(s)"
                    if compactions
                    else ", no compaction"
                )
                lines.append(
                    f"  #{i}  {path} ({size_kb}KB, "
                    f"{sa.output_entry_count} entries{comp_str})"
                )
            else:
                lines.append(f"  #{i}  (missing)")
        if missing:
            lines.append(f"  ({len(missing)} of {len(with_output_path)} missing)")

    return "\n".join(lines)


def format_compaction(agents: list[SubagentInfo]) -> str:
    """Format compaction details for agents that have them."""
    has_compaction = [
        (i, sa) for i, sa in enumerate(agents, 1) if sa.compaction_events
    ]
    if not has_compaction:
        return ""

    lines: list[str] = []
    lines.append("")
    lines.append("Compaction detected:")
    for i, sa in has_compaction:
        desc = sa.description or "(no description)"
        lines.append(f"  #{i} ({desc}):")
        for evt in sa.compaction_events:
            from_k = evt.from_tokens // 1000
            to_k = evt.to_tokens // 1000
            lines.append(
                f"     Turn {evt.turn}: {from_k}K -> {to_k}K tokens "
                f"(-{evt.drop_pct:.1f}%)"
            )

    return "\n".join(lines)


def summarize_tool_input(name: str, inp: dict) -> str:
    """Summarize a tool's input to ~80 chars for trace display."""
    if name == "Read" and "file_path" in inp:
        return inp["file_path"]
    if name in ("navigate", "WebFetch") and "url" in inp:
        url = inp["url"]
        return url[:80] if len(url) > 80 else url
    if name == "javascript_tool" and "text" in inp:
        text = inp["text"]
        return text[:60] + "..." if len(text) > 60 else text
    if name == "Grep" and "pattern" in inp:
        s = f"/{inp['pattern']}/"
        if "path" in inp:
            s += f" {inp['path']}"
        return s[:80]
    if name == "Glob" and "pattern" in inp:
        s = inp["pattern"]
        if "path" in inp:
            s += f" in {inp['path']}"
        return s[:80]
    if name == "Edit" and "file_path" in inp:
        return inp["file_path"]
    if name == "Write" and "file_path" in inp:
        return inp["file_path"]
    if name == "Bash" and "command" in inp:
        cmd = inp["command"]
        return cmd[:80] if len(cmd) > 80 else cmd
    # Default: stringify and truncate
    s = str(inp)
    return s[:80] if len(s) > 80 else s


def render_trace(
    entries: list[TranscriptEntry], show_reasoning: bool = True
) -> str:
    """Render a chronological trace of tool calls and reasoning."""
    lines: list[str] = []
    lines.append("<trace>")

    for entry in entries:
        if not isinstance(entry, AssistantTranscriptEntry):
            continue

        ts = (
            entry.timestamp.strftime("%H:%M:%S") if entry.timestamp else "        "
        )

        for item in entry.message.content:
            if isinstance(item, ToolUseContent):
                summary = summarize_tool_input(item.name, item.input)
                lines.append(f"{ts}  {item.name:<20s}{summary}")
                ts = "        "
            elif isinstance(item, TextContent) and show_reasoning:
                text = item.text.strip()
                if not text:
                    continue
                text_lines = text.split("\n")
                for line in text_lines[:5]:
                    truncated = line[:100] + "..." if len(line) > 100 else line
                    lines.append(f'          "{truncated}"')
                if len(text_lines) > 5:
                    lines.append(f"          ... ({len(text_lines) - 5} more lines)")
                ts = "        "

    lines.append("</trace>")
    return "\n".join(lines)


# =============================================================================
# List / conversation listing formatting (from cli_list.py)
# =============================================================================


def format_conversation_list(sessions: list[SessionInfo]) -> str:
    """Format conversation listing with usage stats as CSV."""
    lines: list[str] = []
    lines.append(f"{len(sessions)} conversations")

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(
        [
            "msgs",
            "agents",
            "context_tokens",
            "output_tokens",
            "tools",
            "date",
            "session",
            "title",
        ]
    )
    for s in sessions:
        st = s.stats
        writer.writerow(
            [
                s.message_count,
                st.agent_count,
                st.context_tokens,
                st.output_tokens,
                st.tool_use_count,
                format_timestamp(s.first_timestamp),
                short_uuid(s.session_id),
                s.title,
            ]
        )
    lines.append(buf.getvalue().rstrip("\n"))
    return "\n".join(lines)


# =============================================================================
# Dump formatting (from cli.py)
# =============================================================================


def format_dump_entry(
    entry: TranscriptEntry,
    timestamps: bool = False,
) -> Optional[str]:
    """Format a single entry for flat dump output. Returns None if entry should be skipped."""
    if isinstance(entry, HumanEntry):
        label = "USER"
    elif isinstance(entry, AssistantTranscriptEntry):
        label = "ASSISTANT"
    else:
        return None

    text = extract_text(entry)
    if not text:
        return None

    text = text.replace("\\", "\\\\").replace("\n", "\\n")

    if timestamps:
        header = f"[{label} {format_timestamp(entry.timestamp)}]"
    else:
        header = f"[{label}]"

    return f"{header} {text}"


def format_dump(
    entries: list[TranscriptEntry],
    timestamps: bool = False,
    role: str = "both",
) -> str:
    """Format all entries for flat text dump output."""
    lines: list[str] = []
    for entry in entries:
        if role == "assistant" and isinstance(entry, HumanEntry):
            continue
        if role == "user" and isinstance(entry, AssistantTranscriptEntry):
            continue

        line = format_dump_entry(entry, timestamps=timestamps)
        if line is not None:
            lines.append(line)

    return "\n".join(lines)


# =============================================================================
# ID matching helper (from cli_agents.py)
# =============================================================================


def matches_id(sa: SubagentInfo, prefix: str) -> bool:
    """Check if a subagent matches an agent_id or tool_use_id prefix."""
    if sa.agent_id and sa.agent_id.startswith(prefix):
        return True
    if sa.tool_use_id.startswith(prefix):
        return True
    return False
