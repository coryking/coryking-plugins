"""Claude Code filesystem provider."""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

from ..models import TranscriptEntry
from ..parser import load_conversations, load_transcript
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
                    session_id=session_id,
                    paths=(conversation.path,),
                    project_path=project,
                    worktree=conversation.worktree,
                    harness=self.harness,
                ))
        return refs

    def load_transcript(self, paths: Sequence[Path]) -> list[TranscriptEntry]:
        return load_transcript(paths[-1]) if paths else []

