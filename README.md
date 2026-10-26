# coryking-plugins

Plugins for searching agent history, gathering project evidence, reviewing code, and examining designs before building them.

## Choose a plugin

| Plugin | What you get |
|---|---|
| **project-mining** | **cc-explorer** for Claude Code and Codex history, project evidence reports, and attention analysis |
| **engineering-loop** | Parallel code review, instruction-file diagnosis and repair, research agents, and session closeout |
| **squabble** | A design-review panel that examines the question, challenges assumptions, and records disagreements |
| **mcp-authoring** | Guidance for writing MCP tool descriptions, schemas, and server instructions |

All four have Claude Code packages. **project-mining** and **engineering-loop** also include native Codex packages.

## Install

In the **ChatGPT desktop app**, open **Plugins**. If `coryking-plugins` is missing from the marketplace sources, [add this repository](INSTALLATION.md#chatgpt-desktop-app--codex) first. Select the source and install the plugin you want, then start a new chat. Codex CLI also offers a plugin browser through `/plugins`.

This repository is a custom marketplace, separate from the public Plugins Directory. [INSTALLATION.md](INSTALLATION.md) explains how to add the source and install in Claude Code.

## Read your agent history

**cc-explorer ships inside project-mining.** It reads conversation records on the machine running its server and gives the assistant search, original-turn reads, and agent inspection tools. Claude Code and Codex history are searchable together; Claude child-agent inspection also exposes dispatch prompts, results, and tool traces.

Try requests such as:

- “Find the conversation where we changed the storage design. Show me my correction and what happened afterward.”
- “What did that background agent actually do? Check its prompt, actions and result.”
- “Find concrete evidence of reliability work in this project, including what shipped.”
- “How much of my time with agents last week was design versus correcting drift? Show examples and explain the limits of the measurements.”

The history workflows ask for original excerpts, source locators, corrections, and gaps. The attention report combines activity proxies with conversation evidence; it does not measure human attention directly.

You can also ask to copy a past conversation for an interview or continued work when the current harness supports resuming it. A generated retelling is separate from the original record. The bundled [conversation-copy guide](project-mining/skills/cc-explorer/references/conversation-copies.md) explains availability and cleanup. Search and original-turn reads need no conversion.

## Review work and improve your tools

Use **engineering-loop** for code review, instruction-file cleanup, research, or closing an individual session. Its [usage guide](engineering-loop/README.md) describes those workflows.

Use **squabble** when you want a design challenged before implementation. The panel returns findings and unresolved disagreements for you to decide; the [skill](squabble/skills/squabble/SKILL.md) covers invocation.

Use **mcp-authoring** when a model overlooks your MCP tool or confuses it with another. Its [tool-description skill](mcp-authoring/skills/tool-descriptions/SKILL.md) covers descriptions, parameter and output schemas, annotations, and recoverable errors.

## Develop these plugins

[CONTRIBUTING.md](CONTRIBUTING.md) covers source changes, tests, local plugin loading, releases, and troubleshooting developer installations.

## License

MIT. Engineering-loop includes material derived from [EveryInc's compound-engineering plugin](https://github.com/EveryInc/compound-engineering-plugin); see [NOTICE](engineering-loop/NOTICE) for attribution.
