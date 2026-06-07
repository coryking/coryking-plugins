"""Tests for cross-project discovery + worktree flattening (issue #21).

discover_projects() enumerates ~/.claude/projects, recovers each encoded dir's
real cwd from a transcript (the dir name is a one-way sanitization), and pools
git worktrees back into their repo so one logical project is one row.

The vendored path helpers (_get_projects_dir / _get_worktree_paths /
_canonicalize_path) are patched so behavior can be verified without a real
~/.claude tree or git repos.
"""

import json
from pathlib import Path
from unittest.mock import patch

import cc_explorer._claude_paths as paths
from cc_explorer.search import discover_projects, resolve_projects


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")


def _patch_paths(projects_dir: Path, worktree_map: dict[str, list[str]]):
    """Patch the helpers discover_projects imports from _claude_paths.

    worktree_map: cwd -> list of worktree paths (first = main). A cwd absent from
    the map resolves to [] (not a git repo → pools under itself).
    """
    return [
        patch.object(paths, "_get_projects_dir", return_value=projects_dir),
        patch.object(paths, "_canonicalize_path", side_effect=lambda p: p),
        patch.object(
            paths, "_get_worktree_paths", side_effect=lambda cwd: worktree_map.get(cwd, [])
        ),
    ]


def test_worktrees_flatten_into_one_repo(tmp_path):
    """Two encoded dirs whose cwds are worktrees of one repo → a single project."""
    projects_dir = tmp_path / "projects"
    # main worktree dir + linked worktree dir, each with its own session file
    _write_jsonl(projects_dir / "encMain" / "s1.jsonl", [{"cwd": "/repo/main"}])
    _write_jsonl(projects_dir / "encWt" / "s2.jsonl", [{"cwd": "/repo/wt"}])

    worktree_map = {
        "/repo/main": ["/repo/main", "/repo/wt"],
        "/repo/wt": ["/repo/main", "/repo/wt"],
    }
    p = _patch_paths(projects_dir, worktree_map)
    with p[0], p[1], p[2]:
        result = discover_projects()

    assert len(result) == 1
    proj = result[0]
    assert proj.path == "/repo/main"  # git's main worktree (first entry)
    assert proj.name == "main"
    assert proj.session_count == 2  # pooled across both encoded dirs
    assert len(proj.encoded_dirs) == 2


def test_non_git_cwd_pools_under_itself(tmp_path):
    """A cwd not inside a git repo (no worktrees) becomes its own project."""
    projects_dir = tmp_path / "projects"
    _write_jsonl(projects_dir / "encRepo" / "s1.jsonl", [{"cwd": "/repo/main"}])
    _write_jsonl(projects_dir / "encLoose" / "s2.jsonl", [{"cwd": "/loose/dir"}])

    worktree_map = {"/repo/main": ["/repo/main"]}  # /loose/dir absent → []
    p = _patch_paths(projects_dir, worktree_map)
    with p[0], p[1], p[2]:
        result = discover_projects()

    by_path = {proj.path: proj for proj in result}
    assert set(by_path) == {"/repo/main", "/loose/dir"}
    assert by_path["/loose/dir"].session_count == 1


def test_cwd_recovered_from_later_line(tmp_path):
    """cwd is read past a leading summary line that lacks one."""
    projects_dir = tmp_path / "projects"
    _write_jsonl(
        projects_dir / "enc" / "s1.jsonl",
        [{"type": "summary"}, {"type": "user", "cwd": "/repo/main"}],
    )
    p = _patch_paths(projects_dir, {"/repo/main": ["/repo/main"]})
    with p[0], p[1], p[2]:
        result = discover_projects()

    assert [proj.path for proj in result] == ["/repo/main"]


def test_dir_with_no_parsable_cwd_is_skipped(tmp_path):
    """An encoded dir whose transcripts carry no cwd is dropped, not crashed on."""
    projects_dir = tmp_path / "projects"
    _write_jsonl(projects_dir / "good" / "s1.jsonl", [{"cwd": "/repo/main"}])
    _write_jsonl(projects_dir / "bad" / "s2.jsonl", [{"type": "summary"}])  # no cwd
    (projects_dir / "empty").mkdir(parents=True)  # no jsonl at all

    p = _patch_paths(projects_dir, {"/repo/main": ["/repo/main"]})
    with p[0], p[1], p[2]:
        result = discover_projects()

    assert [proj.path for proj in result] == ["/repo/main"]


def test_no_projects_dir_returns_empty(tmp_path):
    p = _patch_paths(tmp_path / "does-not-exist", {})
    with p[0], p[1], p[2]:
        assert discover_projects() == []


# --- resolve_projects --------------------------------------------------------


def test_resolve_projects_explicit_list_dedups():
    # Two slash-paths, one repeated → resolved as-is, de-duplicated, order kept.
    assert resolve_projects(["/a/b", "/c/d", "/a/b"]) == ["/a/b", "/c/d"]


def test_resolve_projects_empty_calls_discover():
    from cc_explorer.search import ProjectInfo

    fake = [ProjectInfo(path="/x", name="x"), ProjectInfo(path="/y", name="y")]
    with patch("cc_explorer.search.discover_projects", return_value=fake):
        assert resolve_projects(None) == ["/x", "/y"]
        assert resolve_projects([]) == ["/x", "/y"]
