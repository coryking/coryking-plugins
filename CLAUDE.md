<!-- last-decomposed: d96be83 @ 2026-05-14 @ tier 2 → see .claude/claude-md-doctor/2026-05-14T170109Z/ -->

# coryking-plugins

Claude Code plugins for mining project histories and extracting evidence of behaviors, skills, and patterns.

**This file is for session-shaping behavioral guidance only.** Structural information (directory layouts, plugin internals) lives in each plugin's own metadata. Historical reasoning (why we made past choices) lives in `docs/design-decisions.md`. The session that's auto-loading this file is working *on* the plugins — authoring, refactoring, debugging skill definitions — not executing them. If something belongs only when a specific area is being edited, prefer `.claude/rules/<topic>.md` with `paths:` frontmatter.

## Engineering ownership

You own this codebase. No one else will refactor it, fix technical debt, or improve the architecture — if you don't do it when you notice it, it doesn't get done. When you encounter a code smell while working on a feature (a type that's wrong at the source, duplicated logic, a workaround that shouldn't need to exist), fix it at the root rather than adding another adapter on top.

Act as the engineer, not just the implementer. The user describes what to build; you are responsible for how it's built. Push back when an approach will create debt, ask forward-looking questions about edge cases, and defend the code's architecture. When you're about to write a bridge function or adapter, ask why the bridge is necessary — if the answer is "because a type or model is wrong upstream," fix the upstream problem.

Before adding functionality, read the existing models and type hierarchy first — they are the architecture. New behavior belongs on existing types when it fits. If a model stores data in a weaker type than you need, evolve the model.

## GitHub workflow

The backlog is GitHub Issues at `coryking/coryking-plugins`. **This is a public repo** — never include conversation content, personal data, or export files in issues, PRs, or any artifact committed to this repo.

Every session:
1. **Start:** `gh issue list --state open` for context on open work.
2. **While working:** see tech debt, a bug, or a design question? `gh issue create` immediately. Don't batch.
3. **Before starting a ticket:** scan the issue queue — if multiple issues would benefit from the same foundational change, file a foundational issue and label it `needs-cory`.

Labels: `cc-explorer`, `project-mining`, `tech-debt`, `enhancement`, `spike`, `needs-cory`, `process`.

## Dogfooding cc-explorer

We own cc-explorer in this repo. Use the MCP tools directly when exploring chat history — don't shell out to grep JSONL files. If something doesn't do what you want (a missing parameter, confusing output, a bug you see another agent encounter), propose a fix to the user immediately. Every session working on this repo is a field test of the tools we ship.

## Principles

- **Summary info at the top of output.** Tools (Claude Code, other LLMs, shell pipelines) truncate from the bottom. Any tool we build should put match counts, overflow hints, and actionable metadata in the first lines, not the last.
- **Skills are self-contained.** Everything the executor needs lives inside the skill (or its parent plugin). No references to external files, global CLAUDE.md, or other skills. If you need something, inline it.
- **Wait for three variants before abstracting.** Don't generalize a skill until you've built it for at least three different use cases. Ship the specific version.
- **One authoritative source.** Operational docs (script usage, data formats, mining workflows) live inside the skill. This CLAUDE.md describes *what each skill is for*, not how to use it. Per-plugin descriptions live in each plugin's `plugin.json` and `NOTICE`.
- **This is a toolbox, not a product.** Code here produces ephemeral output consumed by LLMs — no human users, no API contracts, no backwards compatibility obligations. When we learn a better approach, rip out the old one entirely rather than retrofitting. The default posture is "what should this tool do given what we know now?" not "how do we change the least?"
- **CLAUDE.md hygiene.** This file is for session-shaping behavioral guidance only. Structural information goes in plugin metadata; ADR-class reasoning goes in `docs/design-decisions.md`; scoped rules go in `.claude/rules/<topic>.md` with `paths:` frontmatter. When this file grows past ~200 lines, run `claude-md-doctor`.

## Plugins (one-line orientation)

- **`project-mining/`** — mines project histories for behavioral evidence. User-supplied lens, gather/analyze/synthesize methodology, Opus orchestrator + Sonnet researchers. See `project-mining/.claude-plugin/plugin.json` and the per-skill `SKILL.md` files for details.
- **`mcp-authoring/`** — reference guidance for writing MCP tool descriptions, server instructions, parameter schemas, and annotations. See its `plugin.json`.
- **`engineering-loop/`** — slim parallel-review orchestrator (`/el:review`) plus the `claude-md-doctor` cleanup skill. Forked from compound-engineering v3.8.1; provenance in `engineering-loop/NOTICE`. See its `plugin.json`.

For why each plugin is shaped the way it is, open `docs/design-decisions.md`. For repo-tree layout, `ls` from the root or open `.claude-plugin/marketplace.json`.

## When to run claude-md-doctor

This very file is the kind of file `engineering-loop`'s `claude-md-doctor` skill cleans up. Run it when:
- This CLAUDE.md grows past ~200 lines.
- You spot index-class or ADR-class content accumulating here that belongs elsewhere.
- The `<!-- last-decomposed -->` marker is many commits stale.

The skill ships proposals as a branch + PR (Variant B). Merge to approve, close to reject; no in-session gating.
