<!-- last-decomposed: f0b28b3 @ 2026-05-14 → .claude/claude-md-doctor/2026-05-14T18-17-07Z/ -->
# coryking-plugins

Claude Code plugins for mining project histories and extracting evidence of behaviors, skills, and patterns.

**This repo builds and maintains plugins.** The audience for this CLAUDE.md is a session working on the plugins themselves — authoring, refactoring, debugging skill definitions. Operational details (how to run a skill, what flags it takes, what its output looks like) belong inside each skill's own files.

## Engineering ownership

You own this codebase. No one else will refactor it or pay down debt — if you don't fix a smell when you see it, it doesn't get fixed. Prefer root-cause fixes to adapter layers. When you're about to write a bridge, ask why the bridge is necessary; if the answer is "a type or model upstream is wrong," fix upstream.

Act as the engineer, not just the implementer. The user describes what to build; you decide how. Push back when an approach will create debt, ask forward-looking questions, defend the architecture. Read existing models and type hierarchy before adding behavior — new behavior belongs on existing types when it fits.

## GitHub workflow

The backlog is GitHub Issues at `coryking/coryking-plugins`. **This is a public repo** — never include conversation content, personal data, or export files in issues or PRs.

Every session:
1. **Start:** `gh issue list --state open` for context on open work.
2. **While working:** see tech debt, a bug, or a design question? `gh issue create` immediately — don't batch.
3. **Before starting a ticket:** scan the queue for foundational issues your task is a symptom of. File a foundational issue with label `needs-cory` if so.

Labels: `cc-explorer`, `project-mining`, `engineering-loop`, `mcp-authoring`, `tech-debt`, `enhancement`, `spike`, `needs-cory`, `process`.

## Dogfooding cc-explorer

We own cc-explorer in this repo. Use its MCP tools directly when exploring chat history — don't shell out to grep JSONL files. If a tool is missing a parameter, returns confusing output, or you see another agent struggle with it, propose a fix immediately. Every session here is a field test.

## Principles

- **Summary info at the top of output.** Truncation happens from the bottom (`head`, context limits, UI collapsing). Match counts, overflow hints, actionable metadata go on the first lines.
- **Skills are self-contained.** Everything the executor needs lives inside the skill (or its parent plugin). No references to external files, global CLAUDE.md, or other skills. If you need something, inline it.
- **Wait for three variants before abstracting.** Ship the specific version. Generalize only after three different use cases have demanded the same shape.
- **One authoritative source.** Operational docs live inside the skill. This CLAUDE.md describes *what each skill is and why it's shaped that way* — not how to use it.
- **This is a toolbox, not a product.** Output is ephemeral, consumed by LLMs. No human users, no API contracts, no backwards-compatibility obligations. When we learn a better approach, rip out the old one rather than retrofitting. Prefer clean rewrites over incremental patches when scope fits in context.

## Plugin infrastructure

Plugins follow the Claude Code plugin structure (`.claude-plugin/plugin.json`, `agents/`, `skills/`). The marketplace manifest at the repo root (`.claude-plugin/marketplace.json`) registers them.

Non-obvious facts:
- **`{baseDir}`** in skill files resolves to the *skill* directory, not the plugin root. Scripts live at plugin root (`<plugin>/scripts/`) and are referenced from skills accordingly.
- **`${CLAUDE_PLUGIN_ROOT}`** is available in `.mcp.json` and hooks for runtime plugin-root resolution.
- **Named agents** (`agents/<name>.md`) take YAML frontmatter (`name`, `description`, optional `model`) plus markdown body — single source of truth for any persona used in multiple skills.
- **Namespacing a slash command** (e.g. `/el:review`): put the literal `:` inside the skill's frontmatter `name:` field. The directory still uses a dash (`el-review`). Without this, the skill collides with same-named built-ins.

## Plugins

### project-mining

Mines project histories (docs, git, chat logs, IDE artifacts) for evidence of behaviors, values, skills, or patterns under a user-supplied lens. The lens is the variable; the methodology (gather → analyze → synthesize), tooling (cc-explorer MCP, git, Cursor SQLite scripts), and orchestration pattern (Opus orchestrator dispatching Sonnet `mining-researcher` subagents) are fixed. Alignment dialogue translates the lens into searchable behaviors before mining.

Includes the `cc-explorer` skill, which teaches agents how to use the bundled MCP server's progressive-zoom tools (`list_project_sessions` → `search_project` → `grep_session` → `read_turn`) plus agent-inspection tools. Tool mechanics live in the MCP tool descriptions, not the skill.

### engineering-loop

Parallel code-review orchestrator (`/el:review`) plus two research agents, sized for a solo operator. Forked from compound-engineering v3.8.1 and slimmed; see `engineering-loop/NOTICE` for upstream provenance and the slimming ledger. Workflow: detect diff scope → dispatch parallel reviewer subagents that each emit JSON findings → merge + dedup → safe autofixes → route residual findings (interactive / file tickets / report-only).

Each reviewer writes a full-fidelity JSON artifact *and* returns a compact merge-tier subset, so the orchestrator's synthesis context stays small while evidence is preserved. Confidence is discrete (`{0, 25, 50, 75, 100}`), enforced by `findings-schema.json`. Findings carry `pre_existing: bool` so reviewers can surface tech debt without gating merge. The canonical P0–P3 severity scale lives in `skills/el-review/references/review-output-template.md`.

Also hosts `claude-md-doctor`: a skill for auditing this exact instruction surface. Dogfood it when this file gets bloated.

### mcp-authoring

Guidance for writing MCP tool descriptions, server `instructions`, parameter schemas, annotations. Trigger by working on `mcp_server.py` or anything importing `fastmcp` / `mcp.server`.

## Reference material

`docs/` holds design references that inform how skills are built (researcher orchestration patterns, mining methodology, JSONL format, plugin Python patterns, upstream Anthropic prompting docs). See `docs/CLAUDE.md` for the index. History of past design decisions is preserved in `git log` and, for engineering-loop specifically, in `engineering-loop/NOTICE`.
