"""Tests for teammate-awareness in load_sessions and session_title.

All fixtures are SYNTHETIC — no real conversation text, paths, or project names.

Covers:
- load_sessions excludes teammate-injected turns from user_turns (the "single
  prompt fanned out" forensic signal must not count orchestration DMs).
- load_sessions surfaces team / team_role on the SessionInfo row.
- session_title never lands on a teammate message.
"""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    HumanEntry,
    TextContent,
    UserMessageModel,
)
from cc_explorer.parser import ConversationRef
from cc_explorer.search import load_sessions, session_title

TS = datetime(2026, 6, 3, 10, 30, 0, tzinfo=timezone.utc)
SESSION_ID = "a73b9ca7-1111-2222-3333-444444444444"


def _human(text, uuid, team=None, team_role=None, content=None):
    return HumanEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="user",
        teamName=team,
        agentName=team_role,
        message=UserMessageModel(
            role="user",
            content=content if content is not None
            else ([TextContent(type="text", text=text)] if text else []),
        ),
    )


def _teammate(uuid, team="cef-integration", team_role="reviewer-3", body="do X"):
    return _human(
        "", uuid, team=team, team_role=team_role,
        content=[TextContent(
            type="text",
            text=f'<teammate-message teammate_id="orch">{body}</teammate-message>',
        )],
    )


def _assistant(text, uuid, team=None, team_role=None):
    return AssistantTranscriptEntry(
        uuid=uuid,
        timestamp=TS,
        sessionId=SESSION_ID,
        type="assistant",
        teamName=team,
        agentName=team_role,
        message=AssistantMessageModel(
            id="m1", type="message", role="assistant", model="claude-test",
            content=[TextContent(type="text", text=text)],
        ),
    )


def _load(entries):
    conversations = {
        SESSION_ID: ConversationRef(path=Path(f"{SESSION_ID}.jsonl"), worktree=None)
    }
    with patch("cc_explorer.search.load_conversations", return_value=conversations), \
         patch("cc_explorer.search.load_transcript", return_value=entries):
        return load_sessions("/fake/project")


class TestUserTurnsExcludesTeammates:
    def test_teammate_turns_not_counted(self):
        # A worker session: 3 teammate DMs + 1 genuine human turn + replies.
        entries = [
            _teammate("u1", team="cef-integration", team_role="reviewer-3"),
            _assistant("ok", "a1", team="cef-integration", team_role="reviewer-3"),
            _teammate("u2", team="cef-integration", team_role="reviewer-3"),
            _assistant("ok", "a2", team="cef-integration", team_role="reviewer-3"),
            _human("an actual human prompt", "u3",
                   team="cef-integration", team_role="reviewer-3"),
            _assistant("ok", "a3", team="cef-integration", team_role="reviewer-3"),
            _teammate("u4", team="cef-integration", team_role="reviewer-3"),
        ]
        sessions = _load(entries)
        assert len(sessions) == 1
        # Only the one genuine human turn counts; the 3 teammate DMs do not.
        assert sessions[0].user_turns == 1

    def test_pure_worker_session_zero_user_turns(self):
        entries = [
            _teammate("u1"),
            _assistant("ok", "a1"),
            _teammate("u2"),
            _assistant("ok", "a2"),
        ]
        sessions = _load(entries)
        assert sessions[0].user_turns == 0

    def test_normal_session_unaffected(self):
        entries = [
            _human("first", "u1"),
            _assistant("ok", "a1"),
            _human("second", "u2"),
            _assistant("ok", "a2"),
        ]
        sessions = _load(entries)
        assert sessions[0].user_turns == 2


class TestTeamFieldsSurfaced:
    def test_team_fields_present(self):
        entries = [
            _human("hi", "u1", team="cef-integration", team_role="reviewer-3"),
            _assistant("ok", "a1", team="cef-integration", team_role="reviewer-3"),
        ]
        sessions = _load(entries)
        assert sessions[0].team == "cef-integration"
        assert sessions[0].team_role == "reviewer-3"

    def test_team_fields_none_for_normal_session(self):
        entries = [_human("hi", "u1"), _assistant("ok", "a1")]
        sessions = _load(entries)
        assert sessions[0].team is None
        assert sessions[0].team_role is None


class TestTitleNeverTeammate:
    def test_title_skips_leading_teammate_dm(self):
        entries = [
            _teammate("u1", body="orchestrator instructions"),
            _assistant("ok", "a1"),
            _human("the genuine first human prompt", "u2"),
        ]
        assert session_title(entries) == "the genuine first human prompt"

    def test_title_empty_when_only_teammate_turns(self):
        entries = [_teammate("u1"), _assistant("ok", "a1"), _teammate("u2")]
        assert session_title(entries) == "(empty session)"
