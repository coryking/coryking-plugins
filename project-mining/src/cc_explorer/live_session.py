"""Live identity of the Claude Code session that owns this MCP server process.

Claude Code injects CLAUDE_CODE_SESSION_ID into each MCP server it spawns, but
the value is frozen at spawn. A session can change id after its servers start —
remote/bridge sessions (`claude --print --sdk-url ...`) spawn servers under a
provisional id and then continue under a different one — so the env var names a
session with no transcript on disk.

Claude Code also keeps a per-process registry file, ``~/.claude/sessions/<pid>.json``,
whose ``sessionId`` tracks the session the process is running *now*. The MCP
server is a descendant of that process (claude -> uv -> python), so walking up the
process ancestry to the first pid with a registry entry recovers the live id.
"""

from __future__ import annotations

import functools
import json
import os
import subprocess
from pathlib import Path

# claude -> (sh) -> uv -> python is at most a handful of hops; stop well before init.
_MAX_ANCESTOR_HOPS = 8


def _sessions_dir() -> Path:
    base = os.environ.get("CLAUDE_CONFIG_DIR") or str(Path.home() / ".claude")
    return Path(base) / "sessions"


def _parent_pid(pid: int) -> int | None:
    """Parent of `pid` via /proc (Linux) or `ps` (macOS). None if unknown."""
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        try:
            # Field 4 follows the parenthesized comm, which may itself contain spaces.
            return int(stat.read_text().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            return None
    try:
        out = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        return int(out) if out else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


@functools.cache
def _owning_registry_file() -> Path | None:
    """Registry file of the nearest ancestor Claude Code process, found once.

    The ancestry of a running process never changes, so the walk is cached; the
    file's contents are re-read on every lookup because the session id in it can.
    """
    sessions = _sessions_dir()
    pid = os.getppid()
    for _ in range(_MAX_ANCESTOR_HOPS):
        if pid is None or pid <= 1:
            return None
        candidate = sessions / f"{pid}.json"
        if candidate.is_file():
            return candidate
        pid = _parent_pid(pid)
    return None


def registry_session_id() -> str | None:
    """The owning Claude Code process's current session id, or None if unknown."""
    path = _owning_registry_file()
    if path is None:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    sid = data.get("sessionId") if isinstance(data, dict) else None
    return sid if isinstance(sid, str) and sid else None
