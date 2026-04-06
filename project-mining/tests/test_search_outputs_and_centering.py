"""Tests for exhaustive search (tool outputs, thinking) and centered match excerpts.

Two new behaviors introduced with the hide vocabulary:

1. Search reads tool outputs. A pattern matching only in ToolResultEntry
   content is found — previously required scope=tools or similar, now
   automatic.

2. GrepSessionResponse match lines are excerpted around the pattern hit,
   not front-truncated. A match at char 3000 of a 5000-char tool output
   is visible in a 500-char rendered match line.
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
    ThinkingContent,
    ToolResultContent,
    ToolResultEntry,
    ToolUseContent,
    TranscriptStats,
    UserMessageModel,
)
from cc_explorer.responses import GrepSessionResponse
from cc_explorer.search import (
    MatchHit,
    SessionInfo,
    _entry_matches,
    search,
    triage_multi,
)
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


def _assistant_text(text: str, uuid: str = "11111111-aaaa-bbbb-cccc-dddddddddddd") -> AssistantTranscriptEntry:
    return AssistantTranscriptEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="assistant",
        message=AssistantMessageModel(
            id="m1",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=[TextContent(type="text", text=text)],
        ),
    )


def _assistant_with_thinking(thinking: str, uuid: str = "22222222-aaaa-bbbb-cccc-dddddddddddd") -> AssistantTranscriptEntry:
    return AssistantTranscriptEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="assistant",
        message=AssistantMessageModel(
            id="m2",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=[ThinkingContent(type="thinking", thinking=thinking)],
        ),
    )


def _tool_result_entry(text: str, uuid: str = "33333333-aaaa-bbbb-cccc-dddddddddddd") -> ToolResultEntry:
    return ToolResultEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="user",
        message=UserMessageModel(
            role="user",
            content=[
                ToolResultContent(
                    type="tool_result",
                    tool_use_id=PrefixId("tool-0001-2222-3333-444444444444"),
                    content=text,
                ),
            ],
        ),
    )


def _patch_entries(entries):
    return patch(
        "cc_explorer.search.load_transcript",
        side_effect=lambda path: entries if str(path) == "a.jsonl" else [],
    )


class TestSearchReadsToolOutputs:
    """Patterns should match content inside ToolResultEntry.message.content."""

    def test_entry_matches_finds_output(self):
        import re
        entry = _tool_result_entry("error: connection refused on port 8080")
        pattern = re.compile("connection refused", re.IGNORECASE)
        assert _entry_matches(entry, pattern) is True

    def test_entry_matches_suppressed_when_outputs_hidden(self):
        import re
        entry = _tool_result_entry("error: connection refused on port 8080")
        pattern = re.compile("connection refused", re.IGNORECASE)
        assert _entry_matches(entry, pattern, hide=frozenset({"outputs"})) is False

    def test_triage_finds_match_in_tool_output(self):
        entries = [
            _assistant_text("Running the test suite"),
            _tool_result_entry("FAILED: assertion on line 42"),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["FAILED"],
                base_types=(AssistantTranscriptEntry,),
            )
            _, hits = results[0]
            assert len(hits) == 1
            assert hits[0].count == 1
            assert "FAILED" in hits[0].first_match_example

    def test_hiding_outputs_suppresses_output_match(self):
        entries = [
            _tool_result_entry("FAILED: assertion on line 42"),
        ]
        with _patch_entries(entries):
            results = triage_multi(
                [_session()],
                ["FAILED"],
                base_types=(AssistantTranscriptEntry,),
                hide=frozenset({"outputs"}),
            )
            _, hits = results[0]
            assert len(hits) == 0


class TestSearchReadsThinking:
    """Patterns should match inside ThinkingContent blocks on assistant turns."""

    def test_entry_matches_finds_thinking(self):
        import re
        entry = _assistant_with_thinking("Let me reconsider the database approach.")
        pattern = re.compile("database approach", re.IGNORECASE)
        assert _entry_matches(entry, pattern) is True

    def test_entry_matches_suppressed_when_thinking_hidden(self):
        import re
        entry = _assistant_with_thinking("Let me reconsider the database approach.")
        pattern = re.compile("database approach", re.IGNORECASE)
        assert _entry_matches(entry, pattern, hide=frozenset({"thinking"})) is False


class TestMatchLineCentered:
    """grep_session's match line excerpt should be centered on the pattern hit."""

    def test_midentry_match_visible_with_small_truncate(self):
        """A match at char ~2800 in a 5000-char entry should be visible when truncate=500."""
        # Build a tool result with the match buried in the middle
        prefix = "filler text " * 250  # ~3000 chars of leading filler
        match_zone = "HERE_IS_THE_MATCH some surrounding words "
        suffix = "more filler " * 200  # trailing filler
        full_text = prefix + match_zone + suffix

        output_entry = _tool_result_entry(full_text)

        hit = MatchHit(
            session_id=SESSION_ID,
            turn_uuid=output_entry.uuid,
            entry=output_entry,
            context_before=[],
            context_after=[],
        )

        resp = GrepSessionResponse.from_matches(
            session_id=SESSION_ID,
            matches=[hit],
            total=1,
            limit=30,
            truncate=500,
            pattern="HERE_IS_THE_MATCH",
        )

        assert len(resp.matches) == 1
        block = resp.matches[0]
        assert "HERE_IS_THE_MATCH" in block.match, (
            f"expected match token visible in centered excerpt, got: {block.match[:200]}..."
        )

    def test_front_match_still_works(self):
        """Match at start of entry should still render without ellipsis prefix."""
        text = "HERE_IS_THE_MATCH at the very beginning " + ("x " * 500)
        output_entry = _tool_result_entry(text)

        hit = MatchHit(
            session_id=SESSION_ID,
            turn_uuid=output_entry.uuid,
            entry=output_entry,
            context_before=[],
            context_after=[],
        )

        resp = GrepSessionResponse.from_matches(
            session_id=SESSION_ID,
            matches=[hit],
            total=1,
            limit=30,
            truncate=300,
            pattern="HERE_IS_THE_MATCH",
        )
        block = resp.matches[0]
        assert "HERE_IS_THE_MATCH" in block.match


class TestMatchBlockShape:
    """MatchBlock should expose before/match/after as three fields, not flat chats."""

    def test_block_has_three_fields(self):
        entries = [
            _assistant_text("context before the match", uuid="aa111111-aaaa-bbbb-cccc-dddddddddddd"),
            _assistant_text("this contains MATCHTOKEN inside it", uuid="bb222222-aaaa-bbbb-cccc-dddddddddddd"),
            _assistant_text("context after the match", uuid="cc333333-aaaa-bbbb-cccc-dddddddddddd"),
        ]
        with _patch_entries(entries):
            result = search(
                [_session()],
                "MATCHTOKEN",
                base_types=(AssistantTranscriptEntry,),
                context=1,
            )
            assert len(result.matches) == 1

            resp = GrepSessionResponse.from_matches(
                session_id=SESSION_ID,
                matches=result.matches,
                total=1,
                limit=30,
                truncate=200,
                pattern="MATCHTOKEN",
            )
            block = resp.matches[0]
            assert isinstance(block.before, list)
            assert isinstance(block.match, str)
            assert isinstance(block.after, list)
            assert len(block.before) == 1
            assert len(block.after) == 1
            assert "MATCHTOKEN" in block.match
