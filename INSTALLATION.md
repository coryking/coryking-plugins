# Installing coryking-plugins

## From GitHub (recommended)

```bash
/install https://github.com/coryking/coryking-plugins
```

Run this inside Claude Code. It reads `.claude-plugin/marketplace.json` from the repo, discovers available plugins, and installs them.

## Manual installation

If you prefer to install manually or need a local development setup:

### Prerequisites

- Claude Code CLI (`claude`) installed
- `uv` available for Python package management
- This repo cloned locally

### 1. Register as a local marketplace

```bash
claude plugin marketplace add /path/to/coryking-plugins
```

This reads `.claude-plugin/marketplace.json` from the repo root to discover available plugins.

### 2. Install the plugin

```bash
claude plugin install project-mining@coryking-plugins --scope user
```

This copies the plugin to `~/.claude/plugins/cache/coryking-plugins/project-mining/<version>/` and registers it in `~/.claude/plugins/installed_plugins.json`. At this point, the install works but points at a **frozen cache copy** — edits to the repo are invisible.

### 3. Patch installed_plugins.json for live development

Edit `~/.claude/plugins/installed_plugins.json`. Find the `"project-mining@coryking-plugins"` entry and:

1. Change `installPath` from the cache path to your live repo:
   ```json
   "installPath": "/path/to/coryking-plugins/project-mining"
   ```
2. Remove the `gitCommitSha` field (it pins to a frozen snapshot)
3. Ensure `version` matches both `plugin.json` and `marketplace.json`

The entry should look like:
```json
"project-mining@coryking-plugins": [{
  "scope": "user",
  "installPath": "/path/to/coryking-plugins/project-mining",
  "version": "1.1.0",
  "installedAt": "2026-02-26T19:42:49.581Z",
  "lastUpdated": "2026-03-03T00:00:00.000Z"
}]
```

### 4. Restart Claude Code

Plugin content is read at session start. Restart to pick up changes.

## After making changes to skill/agent content

Bump the version in **both** files and update `installed_plugins.json` to match:

| File | Field |
|------|-------|
| `project-mining/.claude-plugin/plugin.json` | `version` |
| `.claude-plugin/marketplace.json` | `plugins[].version` |
| `~/.claude/plugins/installed_plugins.json` | `version` |

All three must agree. A mismatch causes "Plugin not found in marketplace" errors even though the plugin appears installed. Then restart Claude Code.

## Gotchas

- **`@local` is not a keyword.** Using `project-mining@local` in `installed_plugins.json` makes Claude Code look for a marketplace named "local" — which doesn't exist.
- **Cache symlinks get overwritten.** Replacing `~/.claude/plugins/cache/...` with a symlink to the repo works temporarily, but Claude Code may re-create the directory as a real copy.
- **The cache directory may linger.** An `.orphaned_at` file marks stale cache entries. The `installPath` in `installed_plugins.json` is authoritative — ignore the cache.
- **No hot reload.** Content changes require a Claude Code restart. Version bumps may also be required if Claude Code is caching based on version string.
- **`--plugin-dir` is session-only.** `claude --plugin-dir /path/to/plugin` loads a plugin for one session without installing it. Useful for quick testing but not persistent.

## Verifying the installation

```bash
# Should show the plugin as enabled
claude plugin list

# Inside Claude Code, check for plugin errors
/plugins
```

If the plugin shows as installed but disabled, run `claude plugin enable project-mining@coryking-plugins`.

## MCP server dependencies

The cc-explorer MCP server requires Python dependencies managed by `uv`. When the plugin is enabled, Claude Code starts the server via the `.mcp.json` configuration using `uv run`, which handles dependency resolution automatically. No manual `pip install` or venv setup is needed.

## Known upstream issues

There is no native "editable install" for Claude Code plugins. Relevant GitHub issues: #17361, #13799, #14061, #29074.
