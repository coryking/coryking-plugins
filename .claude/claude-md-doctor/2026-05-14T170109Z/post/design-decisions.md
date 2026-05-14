# Design Decisions

Architectural decision record for choices the codebase has made. Reference material — not auto-loaded into every session. Open this file when editing a related area or when curious about a "why is it shaped this way?" question.

Moved out of root `CLAUDE.md` by `claude-md-doctor` on 2026-05-14 to keep the auto-loaded surface focused on session-shaping behavioral guidance.

---

## The "architecture astronaut" scoping discussion

During initial development, we noticed the mining methodology could be generalized beyond AI jobs. We explicitly chose not to, citing "wait for three variants before abstracting."

We later hit the three-variant threshold: AI-job lens, company-values-document lens (e.g., "read a company safety doc and find where I demonstrate these values"), specific-behavior-search lens ("find where I struggled with X"), and emotional-signal mining. The abstraction was earned.

**What we generalized:** The lens is now user-supplied. The methodology (gather/analyze/synthesize), tooling (cc-explorer, git, Cursor scripts), structural analytical questions (struggles, abandoned approaches, constraint-driven decisions), and orchestration pattern (Opus orchestrator, Sonnet researchers) are fixed infrastructure. The search themes and analytical specifics come from the lens via an alignment conversation.

**Where the boundary still holds:** The skill's output structure and downstream cuts (resume bullets, interview stories, LinkedIn posts, story seeds) remain oriented toward job-search / resume / content-creation use cases. Going fully generic (troubleshooting, performance evals unrelated to self-presentation, general knowledge retrieval) would require rethinking the output structure and philosophy. That's the next abstraction boundary — hold it until there are three variants that need it.

## Plugin conversion: from standalone skill to plugin with named agent

The standalone skill dispatched researcher subagents via inline Task prompts — the orchestrator wrote a giant prompt each time, duplicating tool instructions, return format, and analytical stance. Researchers also booted up inside the target project and got its CLAUDE.md/AGENTS.md injected as behavioral directives, which conflicted with the researcher's analytical stance.

The plugin conversion solves both problems:
- **Named agent (`mining-researcher`)** — consistent prompt, model pinning (`sonnet`), and a single place to maintain researcher instructions. The orchestrator passes only the delta (objective, vocabulary, boundaries, paths).
- **Inlined reference material** — chat mining methodology and IDE mining tiers are inlined into the agent definition. No external file dependencies for researchers.
- **Script access** — scripts live at plugin root, accessible via `{baseDir}/scripts/` resolution.

## IDE chat mining: generic framework over Cursor-specific brain dump

The Cursor mining support started as a raw brain dump — hardcoded workspace details and not integrated with the skill's gather/analyze/synthesize workflow.

We reorganized it with two layers (now inlined in `mining-researcher.md`):
1. **Generic framework** — tiered progressive mining (metadata -> user prompts -> full conversations -> aggregate stats). This applies to any IDE that stores chat history.
2. **Cursor implementation reference** — SQLite schema, field documentation, script usage. This is the only IDE we have scripts for so far.

The tiered strategy (triage cheap metadata first, go deep only where it's worth it) is the actual insight; Cursor's SQLite layout is just the first implementation of it.

## Why cc-explorer replaced strip_chat.py

The evolution: `extract_chat_evidence.py` -> `strip_chat.py` -> `cc-explorer`. Each replacement addressed real failures observed in production mining sessions.

`strip_chat.py` solved the right problem (raw JSONL is ~95% tool results and plumbing) but created workflow overhead: batch-strip upfront, write intermediates, then grep with shell tools. Shell variables didn't persist between Bash calls, broad grep results got externalized and silently lost, and researchers needed multiple passes to get conversation context around hits.

cc-explorer wraps typed Pydantic models (adapted from `claude-code-log`, MIT) around the JSONL structure and exposes tools for progressive chat exploration and agent inspection. Four conversation tools follow a zoom pattern: `list_project_sessions` (orient), `search_project` (scan across sessions), `grep_session` (examine within one session), `read_turn` (read a moment at full fidelity). Plus agent inspection tools for tracing subagent execution. Each tool has one output shape — no mode switching. The corpus is treated as one pool of data identified by session UUIDs and turn UUIDs — no filenames, no intermediates, no batch-strip step.

The MCP server architecture eliminated the final friction: researchers no longer need to invoke shell commands at all. Tools appear natively in the agent's tool palette.

## The `agent_content` display parameter

Display tools (`grep_session`, `read_turn`, `browse_session`) accepted `truncate` to control content length, but had no way to toggle what *categories* of content to show. Tool inputs were always on; tool outputs and thinking blocks were always off.

`agent_content` is a comma-separated set of atoms (`thinking`, `inputs`, `outputs`) controlling what's shown for assistant turns beyond the always-present text. Default `"inputs"` preserves backward compatibility. The parameter is orthogonal to existing controls: `truncate` governs length of whatever's visible, `role` filters which entries appear, `scope` (on search tools) controls what's searched.

Key design choices:
- **Text is always shown** — no atom for it. The param controls extras.
- **Tool outputs are separate entries** — `ToolResultEntry` gets role marker `"T"` in pipe-delimited output, interleaved positionally after the assistant turn that triggered them. No tool name on output lines (positional pairing is sufficient).
- **`message.content` ToolResultContent is the display source** for outputs (human-readable text Claude saw), not `toolUseResult` (raw structured metadata).
- **ThinkingContent** was already parsed but silently dropped — `thinking` atom surfaces it with `[thinking]` prefix.

See the `docs/` directory for the JSONL format reference and other design documentation.

## engineering-loop: forked from compound-engineering, slimmed for solo dev

We adopted compound-engineering v3.8.1 wholesale, then carved away the team-coordination layer. The plugin landed without our conventions baked in — versioning rules, the `el:`-prefix trick, the audit against this repo's standards all happened *after* the merge. Future work on this plugin should treat the upstream as a reference, not a template: when we pull updates, port them through our conventions rather than copying as-is.

**Fork deltas worth knowing:**
- Skill was renamed `review` → `el:review` to avoid colliding with Claude Code's built-in `/review`. The `:` lives inside the frontmatter `name:` field, the directory uses `el-review`. Same trick upstream uses for `ce:review`.
- `web-researcher` and `best-practices-researcher` had their `tools:` restrictions removed (upstream limited them to `WebSearch, WebFetch`). Intentional: solo researchers benefit from the full palette.
- `agent-native-reviewer` was demoted from always-on to conditional because most projects don't ship LLM/MCP features and the persona is a measurable token tax.
- Several upstream agents/sections were dropped entirely. See `engineering-loop/NOTICE` for the slimming ledger.

## claude-md-doctor: PR-as-dialogue vs in-session gating

New in `engineering-loop` 0.4.0. Cleans up auto-loaded instruction surfaces (CLAUDE.md, AGENTS.md, `.claude/rules/*.md`) by applying the decompose-prompt technique: name each commitment, sort into PRESENT / MISSING / DROP-CANDIDATES, then propose rewrites.

The skill ships as Variant B of a four-way bake-off. Variant B's distinctive choice is **fire-and-forget**: the skill commits proposals to a `claude-md-doctor/<timestamp>` branch and opens a PR rather than gating mid-session on AskUserQuestion. The rationale, taken directly from the Mozicode dream-routine framing: PRs are slow-rolling dialogue, the lightest channel for anything outside the workspace sandbox, easy to review on a phone. Merge / close / ignore are the three signals.

The bake-off should compare no-wtf rate, time-to-Tier-0 (target < 10 s on healthy repo), and drift recovery over repeated runs.
