"""Live identity of the Claude Code session that owns this MCP server process.

Claude Code injects CLAUDE_CODE_SESSION_ID into each MCP server it spawns, but
the value is frozen at spawn. A session can change id after its servers start —
remote/bridge sessions (`claude --print --sdk-url ...`) spawn servers under a
provisional id and then continue under a different one — so the env var names a
session with no transcript on disk.

Claude Code also keeps a per-process registry file, ``~/.claude/sessions/<pid>.json``,
whose ``sessionId`` tracks the session the process is running *now*. The MCP
server is a descendant of that process (claude -> uv -> python), so walking up the
process ancestry to the nearest harness process recovers the live id — or finds a
Codex process first, in which case the Claude env vars are inherited, not ours.
"""

from __future__ import annotations

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


def _process_name(pid: int) -> str:
    """Executable name of `pid` via /proc (Linux) or `ps` (macOS); "" if unknown."""
    comm = Path(f"/proc/{pid}/comm")
    if comm.exists():
        try:
            return comm.read_text().strip()
        except OSError:
            return ""
    try:
        out = subprocess.run(
            ["ps", "-o", "comm=", "-p", str(pid)],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout.strip()
        return Path(out).name if out else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _registry_entry(path: Path, pid: int) -> dict | None:
    """The registry file's contents if it describes `pid`, else None.

    Registry files outlive crashed processes, and pids get reused; an entry only
    counts when the pid recorded inside it is the process we walked to.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) and data.get("pid") == pid else None


# The nearest harness ancestor never changes for a running process, so a hit is
# cached; a miss is not, since the registry file may simply not exist yet.
_owner_cache: tuple[str, Path | None] | None = None


def _nearest_harness() -> tuple[str, Path | None] | None:
    """("claude", registry_path) or ("codex", None) for the nearest harness ancestor.

    Nearest wins: Codex launched from inside a Claude session (or the reverse)
    inherits the outer harness's env vars, so only the process tree says which
    harness actually owns this MCP server.
    """
    global _owner_cache
    if _owner_cache is not None:
        return _owner_cache
    sessions = _sessions_dir()
    pid = os.getppid()
    for _ in range(_MAX_ANCESTOR_HOPS):
        if pid is None or pid <= 1:
            return None
        candidate = sessions / f"{pid}.json"
        if candidate.is_file() and _registry_entry(candidate, pid) is not None:
            _owner_cache = ("claude", candidate)
            return _owner_cache
        if _process_name(pid).startswith("codex"):
            _owner_cache = ("codex", None)
            return _owner_cache
        pid = _parent_pid(pid)
    return None


def live_owner() -> tuple[str, str | None] | None:
    """The harness that owns this server and, for Claude, its current session id.

    Returns ("claude", <live session id or None>), ("codex", None), or None when
    no harness ancestor is found. The Claude id is re-read on every call because
    the session a process runs can change.
    """
    owner = _nearest_harness()
    if owner is None:
        return None
    harness, path = owner
    if harness != "claude" or path is None:
        return (harness, None)
    entry = _registry_entry(path, int(path.stem))
    sid = entry.get("sessionId") if entry else None
    return ("claude", sid if isinstance(sid, str) and sid else None)
