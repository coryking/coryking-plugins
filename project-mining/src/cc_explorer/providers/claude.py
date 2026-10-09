"""Claude Code filesystem provider."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Mapping, Sequence

from ..models import TranscriptEntry
from ..parser import first_timestamp, load_conversations, load_entries_at, load_transcript
from ..utils import PrefixId
from .base import Harness, ProviderSession


class ClaudeProvider:
    harness = Harness.claude

    def discover_sessions(
        self, projects: Sequence[str] | None = None
    ) -> list[ProviderSession]:
        refs: list[ProviderSession] = []
        for project in projects or ():
            for session_id, conversation in load_conversations(project).items():
                refs.append(ProviderSession(
                    session_id=(session_id if isinstance(session_id, PrefixId)
                                else PrefixId(session_id)),
                    paths=(conversation.path,),
                    project_path=project,
                    worktree=conversation.worktree,
                    harness=self.harness,
                ))
        return refs

    def load_transcript(self, paths: Sequence[Path]) -> list[TranscriptEntry]:
        return load_transcript(paths[-1]) if paths else []

    def load_entries_at(
        self, paths: Sequence[Path], offsets: Mapping[Path, Sequence[int]]
    ) -> list[TranscriptEntry]:
        if not paths or not offsets.get(paths[-1]):
            return []
        return load_entries_at(paths[-1], offsets[paths[-1]])

    def first_timestamp(self, paths: Sequence[Path]) -> datetime | None:
        return first_timestamp(paths[-1]) if paths else None


    def discover_usage(self, selectors=None):
        from .claude_usage import discover
        return discover(selectors)

    def load_usage(self, session: ProviderSession, snapshots=None):
        from .claude_usage import load
        return load(session, snapshots)
