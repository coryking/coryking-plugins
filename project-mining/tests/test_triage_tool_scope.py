"""Tests that matches in tool inputs surface in triage examples.

The bug: when a match lives inside tool-call content (Bash command, Grep
pattern, etc.), the example rendered for that hit must actually contain the
matched text. With exhaustive search, this applies by default to every
assistant entry — no scope flag needed.
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
from cc_explorer.search import SessionInfo, triage, triage_multi
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


class TestToolContentInExamples:
    """Matches inside tool inputs should surface in triage examples."""

    def test_bash_command_in_example(self):
        """When a Bash command matches, the example should contain the command text."""
        entries = [
            _assistant_with_bash("rg -oP 'comment_count' /tmp/data.jsonl"),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["comment_count"],
                base_types=(AssistantTranscriptEntry,),
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
                base_types=(AssistantTranscriptEntry,),
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
                base_types=(AssistantTranscriptEntry,),
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert hits[0].first_match_example  # not empty
            assert "facebook_scrape" in hits[0].first_match_example

    def test_match_in_text_and_tool_both_surface(self):
        """When the match appears in both message text and tool text, the example should contain it."""
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
                base_types=(AssistantTranscriptEntry,),
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert "comment_count" in hits[0].first_match_example

    def test_triage_single_also_surfaces_tool_match(self):
        """The same behavior on single-pattern triage()."""
        entries = [
            _assistant_with_bash("rg -oP 'comment_count' /tmp/data.jsonl"),
        ]
        with _patch_entries(entries):
            hits = triage(
                [_session()],
                "comment_count",
                base_types=(AssistantTranscriptEntry,),
            )
            assert len(hits) == 1
            assert "comment_count" in hits[0].first_match_example

    def test_hiding_inputs_suppresses_tool_match(self):
        """With hide={'inputs'}, matches only in tool inputs should not surface."""
        entries = [
            _assistant_with_bash("rg -oP 'comment_count' /tmp/data.jsonl", text=""),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["comment_count"],
                base_types=(AssistantTranscriptEntry,),
                hide=frozenset({"inputs"}),
            )
            _, hits = results[0]
            assert len(hits) == 0
