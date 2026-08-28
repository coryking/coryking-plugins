"""Cross-host packaging contracts for the project-mining plugin."""

from __future__ import annotations

import json
from pathlib import Path


PLUGIN_ROOT = Path(__file__).parents[1]


def test_codex_plugin_launches_cc_explorer_from_plugin_root():
    """Codex gets portable paths instead of Claude's host-specific env var."""
    manifest = json.loads(
        (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text()
    )
    mcp_config = json.loads((PLUGIN_ROOT / manifest["mcpServers"]).read_text())

    server = mcp_config["mcpServers"]["cc-explorer"]
    assert manifest["skills"] == "./skills/"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert server == {
        "command": "uv",
        "args": ["run", "--project", ".", "cc-explorer"],
        "cwd": ".",
        "env": {
            "UV_CACHE_DIR": "${CLAUDE_PLUGIN_DATA}/uv-cache",
            "UV_PROJECT_ENVIRONMENT": "${CLAUDE_PLUGIN_DATA}/.venv",
        },
        "startup_timeout_sec": 30,
    }
