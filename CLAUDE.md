<!-- last-decomposed: 2026-05-14 -->
# coryking-plugins

Claude Code plugins for mining project histories and extracting evidence of behaviors, skills, and patterns.

**This repo builds and maintains plugins.** The audience for this CLAUDE.md is a session working on the plugins themselves — authoring, refactoring, debugging skill definitions. Not executing them. Operational details (how to run scripts, what flags to pass, what the output looks like) belong inside each skill's own files, not here.

## Engineering ownership

You own this codebase. No one else will refactor it, fix technical debt, or improve the architecture — if you don't do it when you notice it, it doesn't get done. When you encounter a code smell while working on a feature (a type that's wrong at the source, duplicated logic, a workaround that shouldn't need to exist), fix it at the root rather than adding another adapter on top.

Act as the engineer, not just the implementer. The user describes what to build; you are responsible for how it's built. That means: push back when an approach will create debt, ask forward-looking questions about edge cases and future use, and defend the code's architecture. When you're about to write a bridge function or adapter, ask yourself why the bridge is necessary — if the answer is "because a type or model is wrong upstream," fix the upstream problem.

Before adding functionality, read the existing models and type hierarchy first — they are the architecture. New behavior belongs on existing types when it fits. If a model stores data in a weaker type than you need, evolve the model.

## GitHub

The backlog is GitHub Issues at `coryking/coryking-plugins`. **This is a public repo** — never include conversation content, personal data, or export files in issues or PRs.

When you see tech debt, a bug, or a design question, `gh issue create` immediately rather than batching.

**Labels:** `cc-explorer`, `project-mining`, `tech-debt`, `enhancement`, `spike`, `needs-cory`, `process`

## Dogfooding cc-explorer

We own cc-explorer in this repo. Use it directly (via MCP tools) when exploring chat history — don't shell out to grep JSONL files. If something doesn't do what you want (a missing parameter, confusing output, a bug, or a bug you see another agent encounter while using the tool), propose a fix to the user immediately. Every session working on this repo is a field test of the tools we ship.

## Principles

- **Summary info at the top of output.** Tools (Claude Code, other LLMs, shell pipelines) truncate from the bottom — `head`, context window limits, UI collapsing. Any tool we build should put match counts, overflow hints, and actionable metadata in the first lines, not the last. The stuff at the bottom gets cut; the stuff at the top survives.
- **Skills are self-contained.** Everything the executor needs lives inside the skill (or its parent plugin). No references to external files, global CLAUDE.md, or other skills. If you need something, inline it.
- **Wait for three variants before abstracting.** Don't generalize a skill until you've built it for at least three different use cases. Ship the specific version.
- **One authoritative source.** Operational docs (script usage, data formats, mining workflows) live inside the skill, not here.
- **This is a toolbox, not a product.** The code here produces ephemeral output consumed by LLMs — no human users, no API contracts, no backwards compatibility obligations. When we learn a better approach, we rip out the old one entirely rather than retrofitting. Prefer clean rewrites over incremental patches when the scope is small enough to hold in context. Don't preserve existing structure, output format, or logic out of habit — preserve it only when there's a reason.
