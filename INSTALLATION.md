# Install and update

Both harnesses load the same skill sources. Claude Code registers the plugin's named agents; Codex skills use generic subagents with the plugin-local prompt. The cc-explorer server requires `uv` and Python 3.11 or later; the launch command resolves its dependencies.

## Claude Code

Register the marketplace and install the history plugin:

```sh
claude plugin marketplace add coryking/coryking-plugins
claude plugin install project-mining@coryking-plugins --scope user
```

Refresh the marketplace and installed copy after a release:

```sh
claude plugin marketplace update coryking-plugins
claude plugin update project-mining@coryking-plugins
claude plugin list
```

Start a new Claude Code session to load the new skills, agents and MCP process. The installed cache is a snapshot; editing the source checkout does not change that running snapshot. Use `claude --plugin-dir /absolute/path/to/project-mining` for an isolated session against development sources. Keep persistent installation changes in the CLI's managed path rather than editing cache copies or installation metadata.

## Codex

Register a persistent source checkout and enable the plugin:

```sh
codex plugin marketplace add /absolute/path/to/coryking-plugins
codex plugin add project-mining@coryking-plugins
codex plugin list --marketplace coryking-plugins --json
```

Keep a local marketplace's checkout current through its normal Git workflow, then rerun `codex plugin add project-mining@coryking-plugins` to refresh the installed cache. Updating the checkout alone leaves the installed version unchanged. Do not register a disposable worktree as a persistent source. For a configured Git marketplace snapshot, refresh with:

```sh
codex plugin marketplace upgrade coryking-plugins
```

Start a new thread after changes. The native manifest is `project-mining/.codex-plugin/plugin.json`, and `.mcp.json` launches the server from that installed plugin root. Inspect the plugin list's enabled state and source path when checking which copy is active; an already-running MCP process can retain its previous code even after the source updates.

## Release bookkeeping

Update the affected plugin's Claude and Codex manifest versions together. For server changes, also update `project-mining/pyproject.toml` and refresh its lockfile with `uv lock --project project-mining`. The root marketplace resolves each plugin from its source directory; it does not duplicate plugin versions. Commit and push the source before updating installed snapshots.

Verify a fresh server process, not just a reported version. For development checks, the server can be started with `uv run --project project-mining cc-explorer`; a successful MCP initialize/list/call verifies the actual entrypoint. Tests live in `project-mining/tests` and run with `uv run --project project-mining pytest project-mining/tests`.

## Optional conversation copies

Search and source reads need no conversion. Interviews and continued work use converter-created transcript copies and require a compatible resume operation in the destination harness. Check that capability before creating a copy. Generic Codex messaging cannot resume a Claude conversion merely because it accepts an agent id.

The [conversation-copy reference](project-mining/skills/cc-explorer/references/conversation-copies.md) preserves the headroom, provenance, rewind and cleanup rules. Interviews produce generated testimony; original record reads establish exact historical wording and tool actions.
