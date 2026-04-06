"""Tests for extract_agent_tool_audit — the engine behind session_tool_audit.

Verifies tool_use → tool_result pairing, error detection (both is_error=True
and no-match marker text), and tool_name_filter substring matching.
"""

from datetime import datetime, timezone

from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    ToolResultContent,
    ToolResultEntry,
    ToolUseContent,
    UserMessageModel,
)
from cc_explorer.subagents import extract_agent_tool_audit
from cc_explorer.utils import PrefixId

TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
SESSION_ID = "bbbbbbbb-1111-2222-3333-444444444444"


def _assistant_with_tool_use(tool_name: str, tool_id: str, inp: dict) -> AssistantTranscriptEntry:
    return AssistantTranscriptEntry(
        uuid=PrefixId("aaaaaaaa-1111-2222-3333-444444444444"),
        timestamp=TS,
        sessionId=PrefixId(SESSION_ID),
        type="assistant",
        message=AssistantMessageModel(
            id="m1",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=[
                ToolUseContent(
                    type="tool_use",
                    id=PrefixId(tool_id),
                    name=tool_name,
                    input=inp,
                ),
            ],
        ),
    )


def _tool_result_entry(tool_id: str, text: str, is_error: bool = False) -> ToolResultEntry:
    return ToolResultEntry(
        uuid=PrefixId("cccccccc-1111-2222-3333-444444444444"),
        timestamp=TS,
        sessionId=PrefixId(SESSION_ID),
        type="user",
        message=UserMessageModel(
            role="user",
            content=[
                ToolResultContent(
                    type="tool_result",
                    tool_use_id=PrefixId(tool_id),
                    content=[{"type": "text", "text": text}],
                    is_error=is_error,
                ),
            ],
        ),
    )


class TestExtractAgentToolAudit:
    def test_pairs_tool_use_with_tool_result(self):
        entries = [
            _assistant_with_tool_use("Bash", "t1", {"command": "ls"}),
            _tool_result_entry("t1", "file.txt"),
        ]
        calls, counts, errors = extract_agent_tool_audit(entries)
        assert len(calls) == 1
        assert calls[0]["tool"] == "Bash"
        assert calls[0]["error"] is False
        assert counts == {"Bash": 1}
        assert errors == 0

    def test_strips_mcp_prefix_to_short_name(self):
        entries = [
            _assistant_with_tool_use(
                "mcp__plugin_project-mining_cc-explorer__search_project",
                "t1",
                {"patterns": ["foo"]},
            ),
            _tool_result_entry("t1", "{}"),
        ]
        calls, counts, _ = extract_agent_tool_audit(entries)
        assert calls[0]["tool"] == "search_project"
        assert "search_project" in counts

    def test_detects_is_error_flag(self):
        entries = [
            _assistant_with_tool_use("Bash", "t1", {}),
            _tool_result_entry("t1", "boom", is_error=True),
        ]
        calls, _, errors = extract_agent_tool_audit(entries)
        assert calls[0]["error"] is True
        assert errors == 1

    def test_detects_no_match_marker_text(self):
        entries = [
            _assistant_with_tool_use("grep_session", "t1", {}),
            _tool_result_entry("t1", "No matches for: foo|bar"),
        ]
        calls, _, errors = extract_agent_tool_audit(entries)
        assert calls[0]["error"] is True
        assert errors == 1
        assert calls[0]["error_text"] is not None
        assert "no matches" in calls[0]["error_text"].lower()

    def test_detects_validation_error_text(self):
        entries = [
            _assistant_with_tool_use("search_project", "t1", {}),
            _tool_result_entry("t1", "validation error: patterns Input should be a valid list"),
        ]
        calls, _, errors = extract_agent_tool_audit(entries)
        assert errors == 1

    def test_filter_excludes_non_matching_tools_from_calls(self):
        entries = [
            _assistant_with_tool_use("Bash", "t1", {}),
            _tool_result_entry("t1", "ok"),
            _assistant_with_tool_use(
                "mcp__plugin_project-mining_cc-explorer__grep_session", "t2", {}
            ),
            _tool_result_entry("t2", "{}"),
        ]
        calls, counts, _ = extract_agent_tool_audit(entries, tool_name_filter="cc-explorer")
        # calls list filtered
        assert len(calls) == 1
        assert calls[0]["tool"] == "grep_session"
        # but tool_counts still shows everything (so the audit still tells you what was used)
        assert counts == {"Bash": 1, "grep_session": 1}

    def test_input_summary_truncates(self):
        long_input = {"patterns": ["x" * 200]}
        entries = [
            _assistant_with_tool_use("search_project", "t1", long_input),
            _tool_result_entry("t1", "{}"),
        ]
        calls, _, _ = extract_agent_tool_audit(entries, truncate=50)
        assert len(calls[0]["input_summary"]) <= 50
        assert calls[0]["input_summary"].endswith("…")
