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

import json
from pathlib import Path
from unittest.mock import patch

import pytest

import cc_explorer._claude_paths as paths
from cc_explorer.parser import ConversationRef, load_conversations
from cc_explorer._claude_paths import _sanitize_path


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
    """Patch the vendored path helpers that parser.load_conversations imports."""
    import cc_explorer._claude_paths as paths

    return [
        patch.object(paths, "_get_worktree_paths", return_value=worktree_paths),
        patch.object(
            paths, "_find_project_dir", side_effect=lambda p: find_map.get(p)
        ),
        patch.object(
            paths, "_canonicalize_path", side_effect=lambda p: p
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


class TestPrunedWorktreeFolding:
    """Pruned/deleted dispatch worktrees outlive git but leave transcripts behind.

    `git worktree list` only reports live worktrees, so a deleted one's sessions
    are invisible to the git-driven scan and would float as their own fragment
    "project" (labeled with the worktree basename). load_conversations folds them
    back into the repo by the `.claude/worktrees/<name>` path convention, with no
    git and no live worktree dir on disk.

    These tests use a real temp CLAUDE_CONFIG_DIR so the projects-dir scan and
    cwd recovery run for real; only `_get_worktree_paths` is stubbed to model git
    knowing about the main worktree alone.
    """

    REPO = "/work/repos/myrepo"
    WT_CWD = "/work/repos/myrepo/.claude/worktrees/pruned-feature"

    def _make_corpus(self, tmp_path: Path):
        """Lay out encoded project dirs under a temp CLAUDE_CONFIG_DIR."""
        projects = tmp_path / ".claude" / "projects"
        projects.mkdir(parents=True)

        def write(encoded: str, session: str, cwd: str):
            d = projects / encoded
            d.mkdir(parents=True, exist_ok=True)
            entry = {
                "type": "user",
                "uuid": f"u-{session}",
                "timestamp": "2026-06-03T18:00:00Z",
                "cwd": cwd,
                "sessionId": session,
                "message": {"role": "user", "content": "hi"},
            }
            (d / f"{session}.jsonl").write_text(json.dumps(entry), encoding="utf-8")

        write(_sanitize_path(self.REPO), "aaaa1111", self.REPO)
        write(_sanitize_path(self.WT_CWD), "bbbb2222", self.WT_CWD)
        return tmp_path

    def test_pruned_worktree_sessions_folded_into_repo(self, tmp_path, monkeypatch):
        self._make_corpus(tmp_path)
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        # git knows ONLY the live main worktree (the pruned one is gone).
        with patch.object(paths, "_get_worktree_paths", return_value=[self.REPO]):
            result = load_conversations(self.REPO)

        by_stem = {ref.path.stem: ref for ref in result.values()}
        assert set(by_stem) == {"aaaa1111", "bbbb2222"}
        assert by_stem["aaaa1111"].worktree is None          # main
        assert by_stem["bbbb2222"].worktree == "pruned-feature"  # folded orphan

    def test_unrelated_sibling_repo_not_folded(self, tmp_path, monkeypatch):
        # A sibling repo sharing a name prefix must NOT be pulled in by the
        # prefix-gated scan: the path-fold check rejects it.
        self._make_corpus(tmp_path)
        projects = tmp_path / ".claude" / "projects"
        sibling = "/work/repos/myrepo-other"
        d = projects / _sanitize_path(sibling)
        d.mkdir(parents=True)
        entry = {
            "type": "user", "uuid": "u-x", "timestamp": "2026-06-03T18:00:00Z",
            "cwd": sibling, "sessionId": "cccc3333",
            "message": {"role": "user", "content": "hi"},
        }
        (d / "cccc3333.jsonl").write_text(json.dumps(entry), encoding="utf-8")

        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / ".claude"))
        with patch.object(paths, "_get_worktree_paths", return_value=[self.REPO]):
            result = load_conversations(self.REPO)

        stems = {ref.path.stem for ref in result.values()}
        assert stems == {"aaaa1111", "bbbb2222"}  # sibling's cccc3333 excluded
