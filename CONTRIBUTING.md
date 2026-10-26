# Develop and maintain the plugins

For installing and using the plugins, see [INSTALLATION.md](INSTALLATION.md). This file covers source changes, testing and releases.

## Work on the source

Use a Git worktree for changes to a shared checkout. Keep private conversations, exports, credentials and machine-specific evidence out of this public repository. Use synthetic records in committed tests and evaluation fixtures.

Each plugin directory owns its manifests, skills, agents and supporting resources. User-facing behaviour belongs in the skill and its plugin-local references. Keep execution instructions self-contained within the plugin; other machines should not need your global instructions or an unrelated checkout to run it.

The root `.claude-plugin/marketplace.json` catalogs plugins by source directory. Claude packages use `<plugin>/.claude-plugin/plugin.json`; native Codex packages use `<plugin>/.codex-plugin/plugin.json`. Project-mining's Python server lives in `project-mining/src/cc_explorer`, with its tests in `project-mining/tests`.

For engineering-loop provenance and the upstream port history, see [NOTICE](engineering-loop/NOTICE). Its [persona catalog](engineering-loop/skills/el-review/references/persona-catalog.md#project-custom-conditional) defines how a consuming project adds a domain reviewer; keep that contract there rather than duplicating it here.

## Check changes

Run the relevant tests for server changes from the repository root:

```sh
uv run --project project-mining pytest project-mining/tests
```

For instruction-shim compatibility and whitespace checks:

```sh
bash scripts/check-agent-compat.sh
git diff HEAD --check
```

For prompt changes, exercise realistic tasks independently and inspect the actual output and source handoff. Include corrections, unavailable evidence and relevance ambiguity when those are material. Keep private historical evaluation evidence outside the repository. A prose edit needs link, command and behaviour consistency checks; it does not need a new test asserting the prose's wording.

## Local plugin loading

Use the host's managed plugin path. Do not edit frozen cache copies or installation metadata to make source changes appear live.

### Claude Code

For an isolated development session against the current checkout:

```sh
claude --plugin-dir /absolute/path/to/coryking-plugins/project-mining
```

For a persistent local marketplace, register a stable checkout with `claude plugin marketplace add /absolute/path/to/coryking-plugins`, then install the desired entry through the plugin manager. A disposable worktree is suitable for a session-only test, not a persistent marketplace registration.

### Codex

Register a stable development checkout:

```sh
codex plugin marketplace add /absolute/path/to/coryking-plugins
codex plugin marketplace list
```

Restart the ChatGPT desktop app, select the custom source in **Plugins**, and install the entry you are developing. Local catalogs are also available in the CLI's `/plugins` browser. After changes, update the directory the catalog points to and restart the app. Current source and cache behaviour is described in [OpenAI's local-marketplace guide](https://developers.openai.com/plugins/build/plugins#how-local-marketplaces-work).

If a headless CLI installation still reports an older version after the checkout changes, inspect its managed state:

```sh
codex plugin list --marketplace coryking-plugins --json
```

If the installed cache is stale, rerun `codex plugin add project-mining@coryking-plugins`, check the reported version and start a fresh session. This recovery step is specific to the CLI cache; it is not required for every desktop update.

Keep persistent marketplace sources outside worktrees that will be removed. Do not assume the source path, installed snapshot and already-running server are the same copy.

## Verify the MCP entrypoint

Start the development server from the repository root with:

```sh
uv run --project project-mining cc-explorer
```

It speaks MCP over stdio and waits for a client. A successful launch alone is not an operative test: use an MCP client to initialize, list tools and make a representative call. Check the advertised defaults and a successful empty result where relevant. For release verification, launch from the actual installed plugin configuration as well as the checkout; an existing process can retain old imports and tool definitions.

## Release and verify

For a changed plugin package, update its Claude and Codex manifest versions together where both exist. For project-mining server changes, also update `project-mining/pyproject.toml` and refresh the lockfile:

```sh
uv lock --project project-mining
```

The root marketplace points to source directories and does not duplicate plugin versions. Documentation-only changes do not need a plugin version bump.

Run the checks appropriate to the change, review the complete diff including added files, then commit, push and merge through the repository's GitHub workflow. Publish the source before refreshing installed snapshots.

Refresh a Claude installation with its marketplace/plugin update commands in [INSTALLATION.md](INSTALLATION.md#updates). For Codex, refresh the configured Git marketplace or local development source as appropriate, then manage the installed entry through its host. Verify the enabled version and a fresh installed-path process. State which hosts and installation scopes were actually checked; publishing a commit does not prove every machine updated.

Remove the task worktree and merged branches after the source is pushed and the intended installation is verified. Leave unrelated changes and worktrees alone.
