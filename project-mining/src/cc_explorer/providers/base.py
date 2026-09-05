"""Small provider contract for transcript harnesses."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Protocol, Sequence

from ..models import TranscriptEntry


class Harness(str, Enum):
    claude = "claude"
    codex = "codex"


@dataclass(frozen=True)
class ProviderSession:
    session_id: str
    paths: tuple[Path, ...]
    project_path: str
    harness: Harness
    worktree: str | None = None

    @property
    def path(self) -> Path:
        return self.paths[-1]

    def transcript_files(self) -> list[Path]:
        return list(self.paths)


class TranscriptProvider(Protocol):
    harness: Harness

    def discover_sessions(
        self, projects: Sequence[str] | None = None
    ) -> list[ProviderSession]: ...

    def load_transcript(self, paths: Sequence[Path]) -> list[TranscriptEntry]: ...

