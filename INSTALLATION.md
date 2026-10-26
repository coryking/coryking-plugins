# Install and use

Choose a plugin in the [README](README.md#choose-a-plugin). You can install entries from this repository's custom marketplace; these steps do not require the plugins to be published in the universal public directory.

## ChatGPT desktop app / Codex

Open **Plugins** in the ChatGPT desktop app. If `coryking-plugins` is already available in the marketplace source picker, select it, open **project-mining** or **engineering-loop**, and install. Start a new chat after installation.

If the marketplace source is missing, register this GitHub repository once from a terminal with Codex installed:

```sh
codex plugin marketplace add coryking/coryking-plugins
```

Restart the ChatGPT desktop app, open **Plugins**, and choose the new source. This adds a custom catalog; installation still happens through the plugin directory. [OpenAI's marketplace guide](https://developers.openai.com/plugins/build/plugins) describes local and Git-backed sources and their availability by surface.

For a terminal alternative, start `codex` and enter `/plugins`. Select the marketplace, inspect the plugin, and install it there. The [official plugin usage guide](https://learn.chatgpt.com/docs/plugins) covers the desktop directory and CLI browser.

This repository includes native Codex manifests for project-mining and engineering-loop. The other entries currently ship Claude Code packages.

## Claude Code

Inside Claude Code, add the marketplace:

```text
/plugin marketplace add coryking/coryking-plugins
```

Open `/plugin` to browse and install the entries you want. To install the history plugin directly:

```text
/plugin install project-mining@coryking-plugins
```

Start a new Claude Code session after installation.

## Requirements and first use

The history tools in project-mining use `uv` to run a Python 3.11+ MCP server. `uv` must be available in the execution environment. The other plugins primarily provide skills and agent workflows; their required tools depend on the task.

cc-explorer reads the Claude Code and Codex records available on the machine running its server. Installing the plugin does not transfer conversation history between computers. Start with a concrete history question from the [README examples](README.md#read-your-agent-history).

After installation, skills become available in a new chat or session. If tools are unavailable, check that the plugin is installed and enabled, that any required runtime is available, and that you started a new session. Optional session interviews additionally require a compatible resume operation; ordinary history search does not. See the bundled [conversation-copy guide](project-mining/skills/cc-explorer/references/conversation-copies.md).

## Updates

Use your host's plugin manager to manage the installed plugin. In Claude Code, the terminal equivalent is:

```sh
claude plugin marketplace update coryking-plugins
claude plugin update project-mining@coryking-plugins
```

For a Codex Git marketplace added with the command above, refresh its catalog with `codex plugin marketplace upgrade coryking-plugins`, then use **Plugins** or `/plugins` to manage the installed entry. Start a new chat/session after an update. Developer checkouts and stale local CLI caches have a separate procedure in [CONTRIBUTING.md](CONTRIBUTING.md#local-plugin-loading).
