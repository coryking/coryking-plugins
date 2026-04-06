"""Tests for TranscriptEntry.display() and format_entry_line integration.

display() returns body text only; format_entry_line adds turn_id|timestamp|role|length.
"""

import pytest

from cc_explorer.formatting import format_entry_line
from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    HumanEntry,
    SystemTranscriptEntry,
    TextContent,
    ToolUseContent,
    UserMessageModel,
)
from cc_explorer.utils import PrefixId

from .conftest import FULL_UUID, TS


@pytest.fixture
def assistant_entry():
    return AssistantTranscriptEntry(
        uuid=FULL_UUID,
        timestamp=TS,
        sessionId="bbbbbbbb-1111-2222-3333-444444444444",
        type="assistant",
        message=AssistantMessageModel(
            id="m1",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=[
                TextContent(type="text", text="Exactly. Facebook's initial page load."),
                ToolUseContent(
                    type="tool_use",
                    id=PrefixId("tool-uuid-1111-2222-3333-444444444444"),
                    name="Read",
                    input={"file_path": "/tmp/foo.py"},
                ),
            ],
        ),
    )


class TestHumanEntryDisplay:
    def test_body_only_no_role_prefix(self, human_entry):
        s = human_entry.display(truncate=0)
        assert s == "hello world"
        assert "[U:" not in s

    def test_newlines_preserved_in_display(self):
        """display() returns raw text — newline escaping is format_entry_line's job."""
        e = HumanEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="user",
            message=UserMessageModel(
                role="user",
                content=[TextContent(type="text", text="a\nb")],
            ),
        )
        assert e.display(truncate=0) == "a\nb"

    def test_newlines_escaped_in_format_entry_line(self):
        """format_entry_line escapes newlines for pipe-delimited output."""
        e = HumanEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="user",
            message=UserMessageModel(
                role="user",
                content=[TextContent(type="text", text="a\nb")],
            ),
        )
        line = format_entry_line(e, truncate=0)
        display = line.split("|", 4)[4]
        assert "\\n" in display
        assert "\n" not in display

    def test_truncation(self, human_entry):
        long_text = "x" * 100
        e = HumanEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="user",
            message=UserMessageModel(
                role="user",
                content=[TextContent(type="text", text=long_text)],
            ),
        )
        out = e.display(truncate=20)
        assert len(out) <= 20
        assert out.endswith("...")

    def test_truncation_word_boundary(self):
        """Truncation prefers word boundaries over hard character cuts."""
        e = HumanEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="user",
            message=UserMessageModel(
                role="user",
                content=[TextContent(type="text", text="hello world this is a long message")],
            ),
        )
        out = e.display(truncate=20)
        assert out.endswith("...")
        # Should break at a word boundary, not mid-word
        assert "worl..." not in out


class TestAssistantTranscriptEntryDisplay:
    def test_text_and_tool_summaries_no_prefix(self, assistant_entry):
        s = assistant_entry.display(truncate=0)
        assert "[A:" not in s
        assert "Exactly. Facebook's initial page load." in s
        # truncate=0 shows full JSON input
        assert "→ Read(" in s
        assert '"file_path": "/tmp/foo.py"' in s

    def test_empty_assistant(self):
        e = AssistantTranscriptEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="assistant",
            message=AssistantMessageModel(
                id="m1",
                type="message",
                role="assistant",
                model="x",
                content=[],
            ),
        )
        assert e.display(truncate=0) == ""


class TestBaseTranscriptEntryDisplay:
    def test_system_entry_placeholder(self):
        e = SystemTranscriptEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="system",
            content="warn",
        )
        assert e.display(truncate=0) == "[?]"
        assert str(e.uuid) not in e.display(truncate=0)


class TestFormatEntryLineNoDuplicateIdentity:
    def test_human_pipe_fifth_field_is_body_only(self, human_entry):
        line = format_entry_line(human_entry, truncate=500)
        parts = line.split("|", 4)
        assert len(parts) == 5
        display = parts[4]
        assert display == "hello world"
        assert "[U:" not in display

    def test_assistant_pipe_fifth_field_is_body_only(self, assistant_entry):
        line = format_entry_line(assistant_entry, truncate=0)
        parts = line.split("|", 4)
        display = parts[4]
        assert "[A:" not in display
        assert parts[0] == "a9529cc1"
        assert parts[2] == "A"
