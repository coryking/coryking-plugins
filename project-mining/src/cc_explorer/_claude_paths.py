"""Claude Code project-directory & worktree path resolution — VENDORED.

This module is a verbatim copy of the path-resolution logic that maps a working
directory to its Claude Code log folder under ``~/.claude/projects/<encoded>/``.
We vendor it instead of importing it so cc-explorer carries no dependency on the
``claude-agent-sdk`` package (whose entire value to us was these ~115 lines).

WHY VENDOR: cc-explorer used the SDK for exactly these functions and nothing
else. The encoding they implement is a CLI-parity contract Anthropic maintains
deliberately ("matches the CLI's directory naming" recurs in their commits), and
the executable logic has not changed a single line since it first shipped. The
only risk that would force a change here is a *wholesale* rework of how the CLI
names project dirs — and that would force a local update whether we vendored or
not. So the dependency bought us nothing. See the conversation that produced
this file for the full investigation.

═══════════════════════════════════════════════════════════════════════════════
PROVENANCE — read this before editing.
═══════════════════════════════════════════════════════════════════════════════
Source repo : anthropics/claude-agent-sdk-python (GitHub)
Source file : src/claude_agent_sdk/_internal/sessions.py
Vendored at : v0.2.88   (PyPI: claude-agent-sdk==0.2.88)
Born in     : commit 2c418f7 ("feat: add list_sessions…", PR #622), released
              v0.1.46 (2026-03-03). The encoding logic below has been
              byte-identical since that commit; the only post-birth edits were a
              docstring rewrite of ``_simple_hash`` (dec0ecb/#744) and the
              additive ``env_override`` param on ``_get_projects_dir``
              (6e3d54f/#837), both already incorporated here.

The functions below are COPIED VERBATIM (names, bodies, comments) so that a
future diff against upstream is a clean, mechanical comparison. Do not "improve"
or rename them — keep them byte-for-byte with the source so the update check
below stays trivial. Any cc-explorer-specific wrappers belong in parser.py, not
here.

═══════════════════════════════════════════════════════════════════════════════
HOW TO CHECK FOR UPDATES — when Cory says "go look for updates", do this:
═══════════════════════════════════════════════════════════════════════════════
1. Find the latest released version:
     curl -s https://pypi.org/pypi/claude-agent-sdk/json | \
       python3 -c "import sys,json;print(json.load(sys.stdin)['info']['version'])"
2. Fetch upstream's current copy of the source file at HEAD (or a release tag):
     gh api repos/anthropics/claude-agent-sdk-python/contents/src/claude_agent_sdk/_internal/sessions.py \
       --jq '.content' | base64 -d > /tmp/upstream_sessions.py
3. Diff the SPECIFIC symbols we vendor (listed in __all__ below) against the
   upstream file. Only these matter — ignore churn elsewhere in that 1900-line
   module (session stores, transcript readers, subagent enumeration, etc. — we
   reimplement all of that ourselves; see subagents.py / parser.py).
4. If the executable bodies are unchanged → nothing to do; bump the "Vendored at"
   version above for accuracy. If they changed → assess WHY (the investigation
   pattern: was it a bug fix to an edge case, or a wholesale change to the
   directory-naming scheme?), port the change, and update PROVENANCE above.

KEY KNOWN CAVEAT (do not "fix" it — it is correct): for paths >200 chars the
CLI (Bun.hash) and this code (simpleHash) produce different hash suffixes, so
``_find_project_dir`` deliberately falls back to prefix-scanning instead of
trusting the hash. That divergence is upstream's design, not a bug.
"""

from __future__ import annotations

import os
import re
import subprocess
import unicodedata
from pathlib import Path

__all__ = [
    "_canonicalize_path",
    "_find_project_dir",
    "_get_worktree_paths",
]

# ─── BEGIN VERBATIM COPY from claude_agent_sdk/_internal/sessions.py ──────────

MAX_SANITIZED_LENGTH = 200

_SANITIZE_RE = re.compile(r"[^a-zA-Z0-9]")


def _simple_hash(s: str) -> str:
    """32-bit integer hash to base36, matching the CLI's directory naming."""
    h = 0
    for ch in s:
        char = ord(ch)
        h = (h << 5) - h + char
        # Emulate JS `hash |= 0` (coerce to 32-bit signed int)
        h = h & 0xFFFFFFFF
        if h >= 0x80000000:
            h -= 0x100000000
    h = abs(h)
    # JS toString(36)
    if h == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    n = h
    while n > 0:
        out.append(digits[n % 36])
        n //= 36
    return "".join(reversed(out))


def _sanitize_path(name: str) -> str:
    """Makes a string safe for use as a directory name.

    Replaces all non-alphanumeric characters with hyphens. For paths
    exceeding MAX_SANITIZED_LENGTH, truncates and appends a hash suffix.
    """
    sanitized = _SANITIZE_RE.sub("-", name)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return sanitized
    h = _simple_hash(name)
    return f"{sanitized[:MAX_SANITIZED_LENGTH]}-{h}"


def _get_claude_config_home_dir() -> Path:
    """Returns the Claude config directory (respects CLAUDE_CONFIG_DIR)."""
    config_dir = os.environ.get("CLAUDE_CONFIG_DIR")
    if config_dir:
        return Path(unicodedata.normalize("NFC", config_dir))
    return Path(unicodedata.normalize("NFC", str(Path.home() / ".claude")))


def _get_projects_dir(env_override: dict[str, str] | None = None) -> Path:
    """Returns the projects directory.

    ``env_override`` is consulted before ``os.environ`` so callers that pass
    ``CLAUDE_CONFIG_DIR`` to the subprocess via ``options.env`` resolve the
    same directory the subprocess will write to.
    """
    if env_override:
        override = env_override.get("CLAUDE_CONFIG_DIR")
        if override:
            return Path(unicodedata.normalize("NFC", override)) / "projects"
    return _get_claude_config_home_dir() / "projects"


def _get_project_dir(project_path: str) -> Path:
    return _get_projects_dir() / _sanitize_path(project_path)


def _canonicalize_path(d: str) -> str:
    """Resolves a directory path to its canonical form using realpath + NFC."""
    try:
        resolved = os.path.realpath(d)
        return unicodedata.normalize("NFC", resolved)
    except OSError:
        return unicodedata.normalize("NFC", d)


def _find_project_dir(project_path: str) -> Path | None:
    """Finds the project directory for a given path.

    Tolerates hash mismatches for long paths (>200 chars). The CLI uses
    Bun.hash while the SDK under Node.js uses simpleHash — for paths that
    exceed MAX_SANITIZED_LENGTH, these produce different directory suffixes.
    This function falls back to prefix-based scanning when the exact match
    doesn't exist.
    """
    exact = _get_project_dir(project_path)
    if exact.is_dir():
        return exact

    # Exact match failed — for short paths this means no sessions exist.
    # For long paths, try prefix matching to handle hash mismatches.
    sanitized = _sanitize_path(project_path)
    if len(sanitized) <= MAX_SANITIZED_LENGTH:
        return None

    prefix = sanitized[:MAX_SANITIZED_LENGTH]
    projects_dir = _get_projects_dir()
    try:
        for entry in projects_dir.iterdir():
            if entry.is_dir() and entry.name.startswith(prefix + "-"):
                return entry
    except OSError:
        pass
    return None


def _get_worktree_paths(cwd: str) -> list[str]:
    """Returns absolute worktree paths for the git repo containing cwd.

    Returns empty list if git is unavailable or cwd is not in a repo.
    """
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []

    if result.returncode != 0 or not result.stdout:
        return []

    paths = []
    for line in result.stdout.split("\n"):
        if line.startswith("worktree "):
            path = unicodedata.normalize("NFC", line[len("worktree ") :])
            paths.append(path)
    return paths


# ─── END VERBATIM COPY ───────────────────────────────────────────────────────
