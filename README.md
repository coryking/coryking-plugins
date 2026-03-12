# coryking-plugins

A Claude Code plugin marketplace. Install plugins with a single command inside Claude Code.

## What's included

### project-mining

Mines project histories -- Claude Code chat logs, git history, IDE artifacts -- for behavioral evidence. Give it a lens ("find where I demonstrated technical leadership" or "find struggles that became breakthroughs") and it produces source-backed narratives with claims, evidence, and citations. Useful for resume bullets, interview stories, performance review evidence, and similar downstream artifacts.

The plugin uses an orchestrator/researcher architecture: an Opus orchestrator conducts an alignment conversation to understand what you're looking for, then dispatches Sonnet researchers to mine specific projects.

### cc-explorer

An MCP server for exploring Claude Code chat history. It parses the JSONL conversation logs stored in `~/.claude/projects/` and exposes tools for searching, quoting, and inspecting them:

- **search** -- full-text search across conversations with auto-triage (few hits show content, many show counts)
- **quote** -- pull a full conversation moment by turn ID
- **agents** -- inspect subagent dispatch (manifest, session, and detail views)
- **list** -- browse sessions with filters

cc-explorer runs as a FastMCP server over stdio. When the plugin is enabled, Claude Code starts it automatically via `.mcp.json` -- the tools appear natively in the agent's tool palette with no shell commands needed.

## Install

Inside Claude Code:

```
/install https://github.com/coryking/coryking-plugins
```

That's it. Claude Code reads the marketplace manifest, discovers available plugins, and installs them. The MCP server dependencies are managed by `uv` and resolve automatically.

See [INSTALLATION.md](INSTALLATION.md) for manual installation, local development setup, and troubleshooting.

## Requirements

- Claude Code CLI
- `uv` (for Python dependency management)

## License

MIT
