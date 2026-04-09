"""Tests for git-worktree-aware conversation loading.

Claude Desktop dispatch creates real git worktrees under
`<project>/.claude-worktrees/<name>/`. Every worktree of a project gets
its own sanitized dir in `~/.claude/projects/`, and sessions from
dispatched work would be silently invisible if `load_conversations`
only scanned the main worktree's dir.

These tests patch the Agent-SDK helpers (_get_worktree_paths,
_find_project_dir) so the parser's behavior can be verified without
spinning up a real git repo + worktrees.
"""

from pathlib import Path
from unittest.mock import patch

import pytest

from cc_explorer.parser import ConversationRef, load_conversations


class _FakeDir:
    """Stand-in for a Path that supports .glob() and .exists() only."""

    def __init__(self, jsonls: list[str]):
        self._jsonls = [Path(j) for j in jsonls]

    def exists(self) -> bool:
        return True

    def glob(self, pattern: str):
        assert pattern == "*.jsonl"
        return iter(self._jsonls)


def _patch(worktree_paths, find_map):
    """Patch SDK helpers inside the parser module's local import."""
    import claude_agent_sdk._internal.sessions as sdk

    return [
        patch.object(sdk, "_get_worktree_paths", return_value=worktree_paths),
        patch.object(
            sdk, "_find_project_dir", side_effect=lambda p: find_map.get(p)
        ),
        patch.object(
            sdk, "_canonicalize_path", side_effect=lambda p: p
        ),
    ]


class TestWorktreePooling:

    def test_no_git_falls_back_to_single_dir(self):
        """Empty worktree list → single-dir scan, all sessions get worktree=None."""
        fake = _FakeDir(["/claude/-proj/aaaa1111.jsonl", "/claude/-proj/bbbb2222.jsonl"])
        patches = _patch(
            worktree_paths=[],
            find_map={"/home/me/proj": fake},
        )
        with patches[0], patches[1], patches[2]:
            result = load_conversations("/home/me/proj")

        assert len(result) == 2
        for ref in result.values():
            assert isinstance(ref, ConversationRef)
            assert ref.worktree is None

    def test_main_worktree_only(self):
        """Single worktree returned (main only) → sessions are unlabeled."""
        fake = _FakeDir(["/claude/-proj/aaaa1111.jsonl"])
        patches = _patch(
            worktree_paths=["/home/me/proj"],
            find_map={"/home/me/proj": fake},
        )
        with patches[0], patches[1], patches[2]:
            result = load_conversations("/home/me/proj")

        assert len(result) == 1
        ref = next(iter(result.values()))
        assert ref.worktree is None

    def test_main_plus_linked_worktrees(self):
        """First worktree (main) → None; others → basename label."""
        main_dir = _FakeDir(["/claude/-proj/aaaa1111.jsonl"])
        wt1_dir = _FakeDir(["/claude/-proj--wt-happy/bbbb2222.jsonl"])
        wt2_dir = _FakeDir(["/claude/-proj--wt-brave/cccc3333.jsonl"])

        patches = _patch(
            worktree_paths=[
                "/home/me/proj",
                "/home/me/proj/.claude-worktrees/happy-lehmann",
                "/home/me/proj/.claude-worktrees/brave-borg",
            ],
            find_map={
                "/home/me/proj": main_dir,
                "/home/me/proj/.claude-worktrees/happy-lehmann": wt1_dir,
                "/home/me/proj/.claude-worktrees/brave-borg": wt2_dir,
            },
        )
        with patches[0], patches[1], patches[2]:
            result = load_conversations("/home/me/proj")

        assert len(result) == 3
        by_stem = {ref.path.stem: ref for ref in result.values()}
        assert by_stem["aaaa1111"].worktree is None
        assert by_stem["bbbb2222"].worktree == "happy-lehmann"
        assert by_stem["cccc3333"].worktree == "brave-borg"

    def test_missing_worktree_project_dir_is_skipped(self):
        """A worktree whose project dir doesn't exist yet is silently skipped."""
        main_dir = _FakeDir(["/claude/-proj/aaaa1111.jsonl"])
        patches = _patch(
            worktree_paths=[
                "/home/me/proj",
                "/home/me/proj/.claude-worktrees/never-used",
            ],
            find_map={
                "/home/me/proj": main_dir,
                "/home/me/proj/.claude-worktrees/never-used": None,  # not found
            },
        )
        with patches[0], patches[1], patches[2]:
            result = load_conversations("/home/me/proj")

        assert len(result) == 1
        assert next(iter(result.values())).worktree is None

    def test_duplicate_session_prefers_main_worktree(self):
        """If the same session UUID appears in main + linked, main wins."""
        main_dir = _FakeDir(["/claude/-proj/shared111.jsonl"])
        wt_dir = _FakeDir(["/claude/-proj--wt-x/shared111.jsonl"])

        patches = _patch(
            worktree_paths=[
                "/home/me/proj",
                "/home/me/proj/.claude-worktrees/x",
            ],
            find_map={
                "/home/me/proj": main_dir,
                "/home/me/proj/.claude-worktrees/x": wt_dir,
            },
        )
        with patches[0], patches[1], patches[2]:
            result = load_conversations("/home/me/proj")

        assert len(result) == 1
        ref = next(iter(result.values()))
        assert ref.worktree is None  # main (first-wins)
