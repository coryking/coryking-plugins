"""Tests for empty session filtering in load_sessions.

Sessions with only zero-length messages (empty human entries, canned
assistant responses) should not inflate message_count. A session where
someone hit enter four times and got "No response requested." is not
a real session.
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
    TranscriptStats,
    UserMessageModel,
)
from cc_explorer.parser import ConversationRef
from cc_explorer.search import load_sessions


TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
SESSION_ID = "a73b9ca7-1111-2222-3333-444444444444"


def _human(text: str = "", uuid: str = "11111111-aaaa-bbbb-cccc-dddddddddddd") -> HumanEntry:
    return HumanEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="user",
        message=UserMessageModel(
            role="user",
            content=[TextContent(type="text", text=text)] if text else [],
        ),
    )


def _assistant(text: str = "", uuid: str = "22222222-aaaa-bbbb-cccc-dddddddddddd") -> AssistantTranscriptEntry:
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
            content=[TextContent(type="text", text=text)] if text else [],
        ),
    )


def _patch_conversations_and_transcripts(sessions: dict[str, list]):
    """Patch both load_conversations and load_transcript."""
    conversations = {
        sid: ConversationRef(path=Path(f"{sid}.jsonl"), worktree=None)
        for sid in sessions
    }

    def _load_transcript(path):
        sid = path.stem
        return sessions.get(sid, [])

    return [
        patch("cc_explorer.search.load_conversations", return_value=conversations),
        patch("cc_explorer.search.load_transcript", side_effect=_load_transcript),
    ]


class TestEmptySessionFiltering:

    def test_all_empty_messages_not_counted(self):
        """A session with only zero-length messages should have message_count=0 and be skipped."""
        entries = [
            _human(""),
            _human("", uuid="33333333-aaaa-bbbb-cccc-dddddddddddd"),
            _human("", uuid="44444444-aaaa-bbbb-cccc-dddddddddddd"),
            _human("", uuid="55555555-aaaa-bbbb-cccc-dddddddddddd"),
            _assistant(""),
        ]
        patches = _patch_conversations_and_transcripts({SESSION_ID: entries})
        with patches[0], patches[1]:
            sessions = load_sessions("/fake/project")
        assert len(sessions) == 0

    def test_canned_response_still_counts(self):
        """'No response requested.' is non-empty — it counts as a message."""
        entries = [
            _human(""),
            _assistant("No response requested."),
        ]
        patches = _patch_conversations_and_transcripts({SESSION_ID: entries})
        with patches[0], patches[1]:
            sessions = load_sessions("/fake/project")
        assert len(sessions) == 1
        assert sessions[0].message_count == 1

    def test_real_session_unaffected(self):
        """A normal session with real messages counts correctly."""
        entries = [
            _human("hey lets bootstrap"),
            _assistant("Let me read the plan and current state."),
            _human("okay go"),
        ]
        patches = _patch_conversations_and_transcripts({SESSION_ID: entries})
        with patches[0], patches[1]:
            sessions = load_sessions("/fake/project")
        assert len(sessions) == 1
        assert sessions[0].message_count == 3

    def test_mixed_empty_and_real(self):
        """Empty messages shouldn't count alongside real ones."""
        entries = [
            _human(""),  # empty - doesn't count
            _human(""),  # empty - doesn't count
            _human("the actual question"),  # counts
            _assistant("the answer"),  # counts
            _human(""),  # empty - doesn't count
        ]
        patches = _patch_conversations_and_transcripts({SESSION_ID: entries})
        with patches[0], patches[1]:
            sessions = load_sessions("/fake/project")
        assert len(sessions) == 1
        assert sessions[0].message_count == 2
