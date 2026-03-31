"""Tests for triage/triage_multi with scope=tools.

The bug: when scope=tools, matches are found via extract_tool_text() but
examples are extracted from extract_text() — which may not contain the match
at all. Examples should come from whichever text the match was found in.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    HumanEntry,
    TextContent,
    ToolUseContent,
    TranscriptStats,
    UserMessageModel,
)
from cc_explorer.search import ScopeType, SessionInfo, triage, triage_multi
from cc_explorer.utils import PrefixId


SESSION_ID = "aaaaaaaa-1111-2222-3333-444444444444"
TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)


def _session(path: str = "a.jsonl") -> SessionInfo:
    return SessionInfo(
        session_id=SESSION_ID,
        path=Path(path),
        title="test",
        first_timestamp=TS,
        message_count=5,
        stats=TranscriptStats(),
    )


def _assistant_with_bash(command: str, text: str = "") -> AssistantTranscriptEntry:
    """Assistant entry with a Bash tool call and optional text."""
    content = []
    if text:
        content.append(TextContent(type="text", text=text))
    content.append(
        ToolUseContent(
            type="tool_use",
            id=PrefixId("tool-0001-2222-3333-444444444444"),
            name="Bash",
            input={"command": command, "description": "run command"},
        )
    )
    return AssistantTranscriptEntry(
        uuid="11111111-aaaa-bbbb-cccc-dddddddddddd",
        timestamp=TS,
        sessionId=SESSION_ID,
        type="assistant",
        message=AssistantMessageModel(
            id="m1",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=content,
        ),
    )


def _assistant_with_grep(pattern: str, path: str) -> AssistantTranscriptEntry:
    """Assistant entry with a Grep tool call (no message text)."""
    return AssistantTranscriptEntry(
        uuid="22222222-aaaa-bbbb-cccc-dddddddddddd",
        timestamp=TS,
        sessionId=SESSION_ID,
        type="assistant",
        message=AssistantMessageModel(
            id="m2",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=[
                ToolUseContent(
                    type="tool_use",
                    id=PrefixId("tool-0002-2222-3333-444444444444"),
                    name="Grep",
                    input={"pattern": pattern, "path": path},
                )
            ],
        ),
    )


def _human(text: str) -> HumanEntry:
    return HumanEntry(
        uuid="33333333-aaaa-bbbb-cccc-dddddddddddd",
        timestamp=TS,
        sessionId=SESSION_ID,
        type="user",
        message=UserMessageModel(role="user", content=[TextContent(type="text", text=text)]),
    )


def _patch_entries(entries):
    return patch(
        "cc_explorer.search.load_transcript",
        side_effect=lambda path: entries if str(path) == "a.jsonl" else [],
    )


class TestToolScopeExamples:
    """Examples from scope=tools searches should contain the matched tool text."""

    def test_bash_command_in_example(self):
        """When a Bash command matches, the example should contain the command text."""
        entries = [
            _assistant_with_bash("rg -oP 'comment_count' /tmp/data.jsonl"),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["comment_count"],
                entry_types=(AssistantTranscriptEntry,),
                scope=ScopeType.tools,
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert "comment_count" in hits[0].first_match_example

    def test_grep_pattern_in_example(self):
        """When a Grep pattern matches, the example should show the pattern."""
        entries = [
            _assistant_with_grep("facebook.*scrape", "/tmp/sessions/"),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["facebook"],
                entry_types=(AssistantTranscriptEntry,),
                scope=ScopeType.tools,
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert "facebook" in hits[0].first_match_example

    def test_tool_only_entry_produces_example(self):
        """An assistant entry with ONLY tool calls (no text) should still produce an example."""
        entries = [
            _assistant_with_bash("uv run python tools/facebook_scrape.py --test", text=""),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["facebook_scrape"],
                entry_types=(AssistantTranscriptEntry,),
                scope=ScopeType.tools,
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert hits[0].first_match_example  # not empty
            assert "facebook_scrape" in hits[0].first_match_example

    def test_scope_all_prefers_message_text_when_both_match(self):
        """With scope=all, if the match is in both message text and tool text, example should contain the match regardless."""
        entries = [
            _assistant_with_bash(
                "rg comment_count /tmp/data",
                text="Searching for comment_count in the data files.",
            ),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["comment_count"],
                entry_types=(AssistantTranscriptEntry,),
                scope=ScopeType.all,
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert "comment_count" in hits[0].first_match_example

    def test_triage_single_also_affected(self):
        """The same bug exists in triage() — not just triage_multi."""
        entries = [
            _assistant_with_bash("rg -oP 'comment_count' /tmp/data.jsonl"),
        ]
        with _patch_entries(entries):
            hits = triage(
                [_session()],
                "comment_count",
                entry_types=(AssistantTranscriptEntry,),
                scope=ScopeType.tools,
            )
            assert len(hits) == 1
            assert "comment_count" in hits[0].first_match_example
