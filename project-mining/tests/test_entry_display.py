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


class TestSystemTurnTiming:
    """turn_duration timing must survive parsing — it was silently dropped
    before durationMs/messageCount were declared on SystemTranscriptEntry."""

    def _raw(self, **extra):
        base = {
            "type": "system",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
        }
        base.update(extra)
        return base

    def test_turn_duration_parsed_from_raw(self):
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(
            self._raw(subtype="turn_duration", durationMs=103774, messageCount=19)
        )
        assert isinstance(e, SystemTranscriptEntry)
        assert e.durationMs == 103774
        assert e.messageCount == 19
        assert e.turn_duration_ms == 103774

    def test_turn_duration_ms_none_for_other_subtypes(self):
        e = SystemTranscriptEntry(
            uuid=FULL_UUID,
            timestamp=TS,
            sessionId="bbbbbbbb-1111-2222-3333-444444444444",
            type="system",
            subtype="away_summary",
            content="you were idle",
            durationMs=None,
        )
        assert e.turn_duration_ms is None


class TestEntrypointAndPromptSource:
    """entrypoint/promptSource must survive parsing, and is_headless must read
    from entrypoint. Closes the gap from commit f06266f (which declared the
    fields but had no parse test), mirroring TestSystemTurnTiming above."""

    def _human_raw(self, **extra):
        base = {
            "type": "user",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "message": {"role": "user", "content": [{"type": "text", "text": "hi"}]},
        }
        base.update(extra)
        return base

    def test_entrypoint_and_prompt_source_parsed(self):
        from cc_explorer.models import HumanEntry
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(
            self._human_raw(entrypoint="sdk-cli", promptSource="sdk")
        )
        assert isinstance(e, HumanEntry)
        assert e.entrypoint == "sdk-cli"
        assert e.promptSource == "sdk"

    def test_is_headless_true_for_sdk_cli(self):
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(self._human_raw(entrypoint="sdk-cli"))
        assert e.is_headless is True

    def test_is_headless_false_for_cli(self):
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(self._human_raw(entrypoint="cli"))
        assert e.is_headless is False

    def test_is_headless_false_when_absent(self):
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(self._human_raw())
        assert e.entrypoint is None
        assert e.promptSource is None
        assert e.is_headless is False

    def test_entrypoint_on_assistant_entry(self):
        from cc_explorer.models import AssistantTranscriptEntry
        from cc_explorer.parser import create_transcript_entry

        raw = {
            "type": "assistant",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "entrypoint": "sdk-cli",
            "message": {
                "id": "m1",
                "type": "message",
                "role": "assistant",
                "model": "claude-test",
                "content": [{"type": "text", "text": "ok"}],
            },
        }
        e = create_transcript_entry(raw)
        assert isinstance(e, AssistantTranscriptEntry)
        assert e.entrypoint == "sdk-cli"
        assert e.is_headless is True


class TestTeamMembership:
    """teamName/agentName must survive parsing on every entry type (they are
    stamped per-entry like gitBranch/entrypoint), and is_teammate_injected must
    fire only on string-content user turns opening with <teammate-message."""

    def _human_raw(self, content, **extra):
        base = {
            "type": "user",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "message": {"role": "user", "content": content},
        }
        base.update(extra)
        return base

    def test_team_fields_parsed_on_human(self):
        from cc_explorer.models import HumanEntry
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(
            self._human_raw("hi", teamName="cef-integration", agentName="reviewer-3")
        )
        assert isinstance(e, HumanEntry)
        assert e.teamName == "cef-integration"
        assert e.agentName == "reviewer-3"

    def test_team_fields_parsed_on_assistant(self):
        from cc_explorer.models import AssistantTranscriptEntry
        from cc_explorer.parser import create_transcript_entry

        raw = {
            "type": "assistant",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "teamName": "cef-integration",
            "agentName": "reviewer-3",
            "message": {
                "id": "m1", "type": "message", "role": "assistant",
                "model": "claude-test", "content": [{"type": "text", "text": "ok"}],
            },
        }
        e = create_transcript_entry(raw)
        assert isinstance(e, AssistantTranscriptEntry)
        assert e.teamName == "cef-integration"
        assert e.agentName == "reviewer-3"

    def test_team_fields_absent_default_none(self):
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(self._human_raw("hi"))
        assert e.teamName is None
        assert e.agentName is None

    def test_is_teammate_injected_true_for_marker_string(self):
        from cc_explorer.models import is_teammate_injected
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(
            self._human_raw('<teammate-message teammate_id="orch" color="blue">do X</teammate-message>')
        )
        assert is_teammate_injected(e) is True

    def test_is_teammate_injected_false_for_human_prose(self):
        from cc_explorer.models import is_teammate_injected
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(self._human_raw("just a normal typed prompt"))
        assert is_teammate_injected(e) is False

    def test_is_teammate_injected_false_when_marker_not_leading(self):
        # The marker must OPEN the content. A turn that merely mentions the string
        # mid-text (or carries a leading non-text block) is not teammate-injected.
        from cc_explorer.models import is_teammate_injected
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry(
            self._human_raw("here is what a <teammate-message> looks like, fyi")
        )
        assert is_teammate_injected(e) is False

    def test_is_teammate_injected_false_for_non_human(self):
        from cc_explorer.models import is_teammate_injected
        from cc_explorer.parser import create_transcript_entry

        e = create_transcript_entry({
            "type": "assistant",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "message": {
                "id": "m1", "type": "message", "role": "assistant",
                "model": "x", "content": [{"type": "text", "text": "ok"}],
            },
        })
        assert is_teammate_injected(e) is False


class TestTeammateMessageParsing:
    """parse_teammate_message / HumanEntry.teammate_message expose the parsed
    markup at the DTO layer with full fidelity (id, color, summary, body)."""

    def _human(self, content, **extra):
        from cc_explorer.parser import create_transcript_entry

        base = {
            "type": "user",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "message": {"role": "user", "content": content},
        }
        base.update(extra)
        return create_transcript_entry(base)

    def test_all_attributes(self):
        e = self._human(
            '<teammate-message teammate_id="lead" color="blue" summary="review PR">'
            "the full body {json: 1}</teammate-message>"
        )
        tm = e.teammate_message
        assert tm is not None
        assert tm.teammate_id == "lead"
        assert tm.color == "blue"
        assert tm.summary == "review PR"
        assert tm.body == "the full body {json: 1}"

    def test_id_only(self):
        e = self._human('<teammate-message teammate_id="orch">do X</teammate-message>')
        tm = e.teammate_message
        assert tm is not None
        assert tm.teammate_id == "orch"
        assert tm.color is None
        assert tm.summary is None
        assert tm.body == "do X"

    def test_id_and_color_no_summary(self):
        e = self._human('<teammate-message teammate_id="peer" color="red">hi</teammate-message>')
        tm = e.teammate_message
        assert tm.teammate_id == "peer"
        assert tm.color == "red"
        assert tm.summary is None

    def test_id_and_summary_no_color(self):
        e = self._human('<teammate-message teammate_id="peer" summary="s">hi</teammate-message>')
        tm = e.teammate_message
        assert tm.color is None
        assert tm.summary == "s"

    def test_attribute_order_independent(self):
        e = self._human('<teammate-message summary="s" color="g" teammate_id="z">b</teammate-message>')
        tm = e.teammate_message
        assert tm.teammate_id == "z"
        assert tm.color == "g"
        assert tm.summary == "s"

    def test_missing_close_tag(self):
        e = self._human('<teammate-message teammate_id="orch">unterminated body')
        tm = e.teammate_message
        assert tm is not None
        assert tm.body == "unterminated body"

    def test_body_json_kept_raw(self):
        e = self._human('<teammate-message teammate_id="o">{"k": [1, 2]}</teammate-message>')
        assert e.teammate_message.body == '{"k": [1, 2]}'

    def test_no_teammate_id_is_not_teammate(self):
        e = self._human("<teammate-message color=\"blue\">body</teammate-message>")
        assert e.teammate_message is None

    def test_not_leading_is_none(self):
        e = self._human("prefix <teammate-message teammate_id=\"o\">x</teammate-message>")
        assert e.teammate_message is None

    def test_plain_prose_is_none(self):
        e = self._human("a normal typed prompt")
        assert e.teammate_message is None


class TestUserOrigin:
    """origin is the single authoritative classification of a user-role entry."""

    def _entry(self, raw):
        from cc_explorer.parser import create_transcript_entry

        return create_transcript_entry(raw)

    def _user(self, content, **extra):
        base = {
            "type": "user",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "message": {"role": "user", "content": content},
        }
        base.update(extra)
        return self._entry(base)

    def test_human(self):
        from cc_explorer.models import UserOrigin

        assert self._user("a real prompt").origin is UserOrigin.human

    def test_teammate(self):
        from cc_explorer.models import UserOrigin

        e = self._user('<teammate-message teammate_id="o">x</teammate-message>')
        assert e.origin is UserOrigin.teammate

    def test_interrupt_on_human(self):
        from cc_explorer.models import UserOrigin

        assert self._user("[Request interrupted by user]").origin is UserOrigin.interrupt

    def test_command_scaffolding(self):
        from cc_explorer.models import UserOrigin

        e = self._user(
            "<command-name>/clear</command-name> "
            "<command-message>clear</command-message> <command-args></command-args>"
        )
        assert e.origin is UserOrigin.command_scaffolding

    def test_command_with_args_is_human(self):
        from cc_explorer.models import UserOrigin

        e = self._user(
            "<command-name>/wrapup</command-name> "
            "<command-message>wrapup</command-message> "
            "<command-args>real user words here</command-args>"
        )
        assert e.origin is UserOrigin.human

    def test_meta(self):
        from cc_explorer.models import MetaEntry, UserOrigin

        e = self._user("system injected", isMeta=True)
        assert isinstance(e, MetaEntry)
        assert e.origin is UserOrigin.meta

    def test_tool_result(self):
        from cc_explorer.models import ToolResultEntry, UserOrigin

        e = self._user(
            [{"type": "tool_result", "tool_use_id": "toolu_x", "content": "out"}],
            toolUseResult="out",
        )
        assert isinstance(e, ToolResultEntry)
        assert e.origin is UserOrigin.tool_result

    def test_tool_result_interrupt(self):
        from cc_explorer.models import ToolResultEntry, UserOrigin

        e = self._user(
            [{"type": "text", "text": "[Request interrupted by user for tool use]"}],
            toolUseResult="[Request interrupted by user for tool use]",
        )
        assert isinstance(e, ToolResultEntry)
        assert e.origin is UserOrigin.interrupt


class TestTeammateRendering:
    """A teammate turn renders labeled, not as raw <teammate-message> XML."""

    def _human(self, content, **extra):
        from cc_explorer.parser import create_transcript_entry

        base = {
            "type": "user",
            "uuid": FULL_UUID,
            "timestamp": "2026-06-08T15:25:36.183Z",
            "sessionId": "bbbbbbbb-1111-2222-3333-444444444444",
            "message": {"role": "user", "content": content},
        }
        base.update(extra)
        return create_transcript_entry(base)

    def test_display_labeled(self):
        e = self._human(
            '<teammate-message teammate_id="team-lead">please review</teammate-message>',
            agentName="reviewer-3",
        )
        out = e.display(truncate=200)
        assert out.startswith("[teammate: team-lead → reviewer-3]")
        assert "please review" in out
        assert "<teammate-message" not in out

    def test_display_unknown_recipient(self):
        e = self._human('<teammate-message teammate_id="lead">go</teammate-message>')
        assert e.display(truncate=200).startswith("[teammate: lead → ?]")

    def test_format_entry_line_labeled(self):
        e = self._human(
            '<teammate-message teammate_id="lead">do the thing</teammate-message>',
            agentName="worker-1",
        )
        line = format_entry_line(e, truncate=200)
        # role still U (matching semantics unchanged), display labeled.
        parts = line.split("|", 4)
        assert parts[2] == "U"
        assert parts[4].startswith("[teammate: lead → worker-1]")
        assert "<teammate-message" not in parts[4]


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
