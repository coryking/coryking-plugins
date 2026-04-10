# docs/

Reference documents for skill design and development. These inform how skills are built, not how they're executed — execution docs live inside each skill.

**If you add, remove, or rename a document in this directory, update the table of contents below.**

## Table of Contents

| Document | Purpose |
|----------|---------|
| `agent-researcher-orchestration.md` | Patterns for fan-out research: subagent prompts, work division, synthesis, model tiering, anti-patterns. Reference for any skill that delegates to researcher subagents. |
| `cc-explorer-tool-split-braindump.md` | Design braindump for the tool split from single auto-triage `search_chat_history` into progressive zoom tools (`search_project`, `grep_session`, `read_turn`). Observations from session `4471fb60`. |
| `chat-mining-methodology.md` | Mining methodology: three-corpora topology, research loop, vocabulary expansion spectrum, field test findings (scout bias, planning phase quality, lens quality as key lever), observed failure modes, open design questions. Updated April 2026 after six-project field test. |
| `claude-code-jsonl-format.md` | Claude Code JSONL conversation format reference. Schema for raw chat log files. |
| `plugin-python-patterns.md` | Survey of Claude Code plugin ecosystem (March 2026): Python invocation patterns (PEP 723, sys.path.insert, uv run --project), repo layout (monorepo dominant), versioning (manual plugin.json bumps, cache invalidation), marketplace.json structure, hooks patterns. |
| `anthropic-reference/` | Authoritative upstream docs from Anthropic for writing agent prompts and skills. Prompting best practices (22KB, context-friendly), system cards (PDFs + text), Opus 4.5 soul document, Claude Code system prompts (269 files — agent prompts, skills, system reminders, tool descriptions). |
