"""Small provider contract for transcript harnesses."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, Sequence, TYPE_CHECKING

from ..models import TranscriptEntry
from ..utils import PrefixId

if TYPE_CHECKING:
    from ..usage_models import UsageDiscovery, SessionUsage
    from ..usage_sources import SnapshotReader


class Harness(str, Enum):
    claude = "claude"
    codex = "codex"


@dataclass(frozen=True)
class ProviderSession:
    session_id: PrefixId
    paths: tuple[Path, ...]
    project_path: str
    harness: Harness
    worktree: str | None = None
    # Accounting preserves copies and execution branches; browsing may choose a
    # single head. These fields never alter the existing browsing projection.
    parent_id: str | None = None
    role: str | None = None
    relationship: str = "independent"
    source_roots: tuple[Path, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict, compare=False)

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

    def discover_usage(self) -> UsageDiscovery: ...

    def load_usage(self, session: ProviderSession, snapshots: SnapshotReader | None = None) -> SessionUsage: ...


def project_identity(cwd: str) -> tuple[str, str | None]:
    """Shared project/worktree identity for browsing and accounting discovery."""
    from .._claude_paths import _canonicalize_path, _get_worktree_paths
    from ..corpus import _repo_root_from_worktree_path

    canonical = _canonicalize_path(cwd)
    worktrees = _get_worktree_paths(canonical)
    if worktrees:
        main = _canonicalize_path(worktrees[0])
        return main, None if canonical == main else Path(canonical).name
    recovered = _repo_root_from_worktree_path(canonical)
    return (recovered, Path(canonical).name) if recovered else (canonical, None)
