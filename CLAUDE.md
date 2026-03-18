# coryking-plugins

Claude Code plugins for mining project histories and extracting evidence of behaviors, skills, and patterns.

**This repo builds and maintains plugins.** The audience for this CLAUDE.md is a session working on the plugins themselves — authoring, refactoring, debugging skill definitions. Not executing them. Operational details (how to run scripts, what flags to pass, what the output looks like) belong inside each skill's own files, not here.

## Engineering ownership

You own this codebase. No one else will refactor it, fix technical debt, or improve the architecture — if you don't do it when you notice it, it doesn't get done. When you encounter a code smell while working on a feature (a type that's wrong at the source, duplicated logic, a workaround that shouldn't need to exist), fix it at the root rather than adding another adapter on top.

Act as the engineer, not just the implementer. The user describes what to build; you are responsible for how it's built. That means: push back when an approach will create debt, ask forward-looking questions about edge cases and future use, and defend the code's architecture. When you're about to write a bridge function or adapter, ask yourself why the bridge is necessary — if the answer is "because a type or model is wrong upstream," fix the upstream problem.

Before adding functionality, read the existing models and type hierarchy first — they are the architecture. New behavior belongs on existing types when it fits. If a model stores data in a weaker type than you need, evolve the model.

## Principles

- **Summary info at the top of output.** Tools (Claude Code, other LLMs, shell pipelines) truncate from the bottom — `head`, context window limits, UI collapsing. Any tool we build should put match counts, overflow hints, and actionable metadata in the first lines, not the last. The stuff at the bottom gets cut; the stuff at the top survives.
- **Skills are self-contained.** Everything the executor needs lives inside the skill (or its parent plugin). No references to external files, global CLAUDE.md, or other skills. If you need something, inline it.
- **Wait for three variants before abstracting.** Don't generalize a skill until you've built it for at least three different use cases. Ship the specific version. (See the architecture astronaut discussion below.)
- **One authoritative source.** Operational docs (script usage, data formats, mining workflows) live inside the skill. This CLAUDE.md describes *what each skill is and why it's shaped that way* — not how to use it.
- **This is a toolbox, not a product.** The code here produces ephemeral output consumed by LLMs — no human users, no API contracts, no backwards compatibility obligations. When we learn a better approach, we rip out the old one entirely rather than retrofitting. Prefer clean rewrites over incremental patches when the scope is small enough to hold in context. Don't preserve existing structure, output format, or logic out of habit — preserve it only when there's a reason. The default posture is "what should this tool do given what we know now?" not "how do we change the least?"

## Repo structure

This repo is a Claude Code plugin marketplace. The root `.claude-plugin/marketplace.json` registers available plugins. Each plugin is a subdirectory with its own `.claude-plugin/plugin.json`, skills, agents, and tooling.

```
coryking-plugins/
├── .claude-plugin/marketplace.json   # marketplace manifest
├── CLAUDE.md
├── INSTALLATION.md
├── LICENSE
├── project-mining/                   # plugin directory
│   ├── .claude-plugin/plugin.json    # plugin metadata
│   ├── .mcp.json                     # MCP server wiring
│   ├── agents/
│   │   └── mining-researcher.md      # named agent for research subagents
│   ├── pyproject.toml                # package config (pydantic, fastmcp deps)
│   ├── skills/
│   │   ├── cc-explorer/
│   │   │   └── SKILL.md              # skill teaching agents to use cc-explorer tools
│   │   └── project-mining/
│   │       └── SKILL.md              # orchestrator skill
│   ├── scripts/
│   │   └── cursor_*.py               # Cursor SQLite scripts for IDE chat mining
│   └── src/cc_explorer/              # MCP server + typed JSONL toolkit
└── docs/                             # design docs and reference material
```

### MCP server architecture

cc-explorer runs as an MCP server using FastMCP over stdio transport. When the plugin is enabled, Claude Code reads `.mcp.json` and starts the server automatically. Tools are discovered and callable natively — no Bash invocation needed.

The `.mcp.json` wires the server:
```json
{
  "mcpServers": {
    "cc-explorer": {
      "command": "uv",
      "args": ["run", "--project", "${CLAUDE_PLUGIN_ROOT}", "cc-explorer"]
    }
  }
}
```

This means:
- Tools (`search`, `quote`, `agents`, `list`) appear as MCP tools in the agent's tool palette
- The skill (`skills/cc-explorer/SKILL.md`) teaches the agent *when and why* to use each tool, not *how* to invoke them
- No shell commands, no `uv run` in prompts — the MCP layer handles invocation

### Plugin internals

Plugins use the Claude Code plugin structure (`.claude-plugin/plugin.json`, `agents/`, `skills/`).

- **Named agents** (`agents/<name>.md`) — YAML frontmatter (`name`, `description`, `model`) + markdown body. Consistent prompt, model pinning, single source of truth for researcher methodology.
- **Skills** (`skills/<skill-name>/SKILL.md`) — frontmatter (`name`, `description`) + instructions for the executing agent.
- **Scripts** live at the plugin root (`<plugin>/scripts/`). `{baseDir}` resolves to the skill directory, not the plugin root.
- **`${CLAUDE_PLUGIN_ROOT}`** — available in `.mcp.json` and hooks for referencing the plugin root at runtime.

## Plugins

### project-mining

Mines project histories (docs, git, chat logs, artifacts) for evidence of specific behaviors, values, skills, or patterns. Accepts a user-supplied lens (direct query, value list, or reference document) and produces rich source-of-truth narratives. Downstream cuts include resume bullets, interview stories, LinkedIn posts, performance review evidence, etc.

**The lens is user-supplied.** The skill conducts an alignment conversation to translate abstract concepts into observable, searchable behaviors before mining begins. The methodology (gather/analyze/synthesize), tooling (cc-explorer MCP tools, git, Cursor scripts), and orchestration pattern (Opus orchestrator, Sonnet researchers, structured return format) are fixed; the analytical questions and search themes come from the lens.

**Components:**
- `.claude-plugin/plugin.json` — plugin metadata
- `.mcp.json` — MCP server configuration for cc-explorer
- `agents/mining-researcher.md` — named agent for research subagents. Inlines analytical stance, the search→grep→read methodology for chat mining, return format (claim/evidence/source/relevance), and IDE mining tiers. The orchestrator passes only the delta (objective, vocabulary, boundaries, paths). Tool mechanics are documented in MCP tool descriptions; the agent prompt focuses on research workflow.
- `skills/project-mining/SKILL.md` — orchestrator instructions: alignment protocol, gather/analyze/synthesize workflow, researcher dispatch via `project-mining:mining-researcher`, output structure, anti-patterns
- `skills/cc-explorer/SKILL.md` — skill teaching agents how to use cc-explorer MCP tools to explore chat logs. Describes workflow (when to use what); tool mechanics live in the MCP tool descriptions (Python docstrings), not the skill.
- `src/cc_explorer/` — typed JSONL toolkit (Pydantic models, search/filter/triage, FastMCP server). Tools follow a progressive zoom: `list_project_sessions` (orient), `search_project` (scan), `grep_session` (examine), `read_turn` (read), plus agent inspection tools. `project` defaults to CWD.
- `pyproject.toml` — package config with pydantic and fastmcp deps, `cc-explorer` entry point
- `scripts/cursor_*.py` — Cursor SQLite scripts for IDE chat mining

**Output:** user-confirmed during alignment.

## Design Decisions

### The "architecture astronaut" scoping discussion

During initial development, we noticed the mining methodology could be generalized beyond AI jobs. We explicitly chose not to, citing "wait for three variants before abstracting."

We later hit the three-variant threshold: AI-job lens, company-values-document lens (e.g., "read a company safety doc and find where I demonstrate these values"), specific-behavior-search lens ("find where I struggled with X"), and emotional-signal mining. The abstraction was earned.

**What we generalized:** The lens is now user-supplied. The methodology (gather/analyze/synthesize), tooling (cc-explorer, git, Cursor scripts), structural analytical questions (struggles, abandoned approaches, constraint-driven decisions), and orchestration pattern (Opus orchestrator, Sonnet researchers) are fixed infrastructure. The search themes and analytical specifics come from the lens via an alignment conversation.

**Where the boundary still holds:** The skill's output structure and downstream cuts (resume bullets, interview stories, LinkedIn posts, story seeds) remain oriented toward job-search / resume / content-creation use cases. Going fully generic (troubleshooting, performance evals unrelated to self-presentation, general knowledge retrieval) would require rethinking the output structure and philosophy. That's the next abstraction boundary — hold it until there are three variants that need it.

### Plugin conversion: from standalone skill to plugin with named agent

The standalone skill dispatched researcher subagents via inline Task prompts — the orchestrator wrote a giant prompt each time, duplicating tool instructions, return format, and analytical stance. Researchers also booted up inside the target project and got its CLAUDE.md/AGENTS.md injected as behavioral directives, which conflicted with the researcher's analytical stance.

The plugin conversion solves both problems:
- **Named agent (`mining-researcher`)** — consistent prompt, model pinning (`sonnet`), and a single place to maintain researcher instructions. The orchestrator passes only the delta (objective, vocabulary, boundaries, paths).
- **Inlined reference material** — chat mining methodology and IDE mining tiers are inlined into the agent definition. No external file dependencies for researchers.
- **Script access** — scripts live at plugin root, accessible via `{baseDir}/scripts/` resolution.

### IDE chat mining: generic framework over Cursor-specific brain dump

The Cursor mining support started as a raw brain dump — hardcoded workspace details and not integrated with the skill's gather/analyze/synthesize workflow.

We reorganized it with two layers (now inlined in `mining-researcher.md`):
1. **Generic framework** — tiered progressive mining (metadata -> user prompts -> full conversations -> aggregate stats). This applies to any IDE that stores chat history.
2. **Cursor implementation reference** — SQLite schema, field documentation, script usage. This is the only IDE we have scripts for so far.

The tiered strategy (triage cheap metadata first, go deep only where it's worth it) is the actual insight; Cursor's SQLite layout is just the first implementation of it.

### Why cc-explorer replaced strip_chat.py

The evolution: `extract_chat_evidence.py` -> `strip_chat.py` -> `cc-explorer`. Each replacement addressed real failures observed in production mining sessions.

`strip_chat.py` solved the right problem (raw JSONL is ~95% tool results and plumbing) but created workflow overhead: batch-strip upfront, write intermediates, then grep with shell tools. Shell variables didn't persist between Bash calls, broad grep results got externalized and silently lost, and researchers needed multiple passes to get conversation context around hits.

cc-explorer wraps typed Pydantic models (adapted from `claude-code-log`, MIT) around the JSONL structure and exposes tools for progressive chat exploration and agent inspection. Four conversation tools follow a zoom pattern: `list_project_sessions` (orient), `search_project` (scan across sessions), `grep_session` (examine within one session), `read_turn` (read a moment at full fidelity). Plus agent inspection tools for tracing subagent execution. Each tool has one output shape — no mode switching. The corpus is treated as one pool of data identified by session UUIDs and turn UUIDs — no filenames, no intermediates, no batch-strip step.

The MCP server architecture eliminated the final friction: researchers no longer need to invoke shell commands at all. Tools appear natively in the agent's tool palette.

See `docs/` for the JSONL format reference and other design documentation.
