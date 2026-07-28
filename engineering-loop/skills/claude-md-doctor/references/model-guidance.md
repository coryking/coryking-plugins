# Model guidance primer

The doctor's judging doctrine for "what works in an instruction file," distilled from
Anthropic's published guidance. **Normal runs judge against this file only — never
against live docs or trained intuition** — so that two runs on the same repo agree.
`refresh` mode is the only thing that changes this file.

```yaml
# provenance stamp — refresh mode updates this; Stage 0 checks it
model_family: claude-5 (fable) / claude-4.8-era docs
cc_version: 2.1.220
refreshed: 2026-07-23
partial_refresh: 2026-07-28 — memory.md re-fetched to source the auto-memory section; other sources unchanged
sources:
  - https://code.claude.com/docs/en/best-practices.md          # CLAUDE.md include/exclude, sizing
  - https://code.claude.com/docs/en/memory.md                  # loading mechanics, rules, imports, auto memory
  - https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-4-best-practices  # serves current "Prompting best practices" (all current models)
  - https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-fable-5 # model-specific page; swap for the session's model family
```

## Sizing and adherence

- Target **under 200 lines per CLAUDE.md** (official). Longer files reduce adherence:
  "Bloated CLAUDE.md files cause Claude to ignore your actual instructions."
- CLAUDE.md is delivered as a **user message after the system prompt** — context, not
  enforced configuration. Anything that must happen every time with zero exceptions is
  hook-shaped, not prose-shaped.
- The official per-line test (verbatim): *"Would removing this cause Claude to make
  mistakes?" If not, cut it.* The official include/exclude table matches this skill's
  wrong-mechanism heuristics: exclude anything derivable from the code, standard
  conventions, API docs (link instead), frequently-changing info, tutorials,
  file-by-file inventories, and self-evident practices.
- Symptom table (official): rule repeatedly ignored → file too long, rule lost in
  noise. Claude asks questions the file answers → phrasing ambiguous. Two rules
  conflict → Claude picks one arbitrarily.
- Claude Code's built-in `/doctor` (v2.1.206+) proposes CLAUDE.md trims — it cuts
  derivable content and keeps pitfalls/rationale/conventions. This skill's value over
  it: evidence (chat-history mining, truth checks), gated apply, and cross-scope
  analysis.

## Loading mechanics (authoritative snapshot)

- **`@import`**: expanded and loaded **at launch**, inline-equivalent — organization,
  not context savings. Recursive, max depth 4. Relative paths resolve from the
  importing file. Skipped inside code spans/fences (backtick-wrap a path to mention it
  without importing). External imports (resolving outside the working directory)
  trigger a one-time approval dialog in project files.
- **`.claude/rules/*.md`**: no `paths:` frontmatter → loads at launch, same priority
  as `.claude/CLAUDE.md`. With `paths:` globs → loads when Claude reads matching
  files. Discovered recursively; symlinks resolve. User-level `~/.claude/rules/` load
  before project rules (project wins).
- **Nested CLAUDE.md** in subdirectories: load on demand when Claude reads files
  there; ancestor files load in full at launch, ordered root → cwd.
- **A plain markdown link is the only true read-on-demand trapdoor** in the
  instruction surface. Skills are the read-on-demand mechanism for procedures.
- **Block-level HTML comments are stripped before injection** — they cost zero
  context. (This is why the doctor's marker is an HTML comment, and it means a comment
  can never carry instructions.)
- **AGENTS.md**: Claude Code does not read it; the official pattern is a CLAUDE.md
  containing `@AGENTS.md` (optionally with Claude-specific additions below) or a
  symlink. When the doctor sees this shim, the AGENTS.md is the real project
  instruction file.

## Auto memory (authoritative snapshot)

Auto memory is the *second* thing loaded into every session, alongside CLAUDE.md. Claude
writes it; the user writes CLAUDE.md. It is a real mechanism with a precise contract —
the doctor judges it against this section, not against intuition about "memory."

- **One directory per git repository**: `~/.claude/projects/<project>/memory/`, where
  `<project>` derives from the repo, so **every worktree and subdirectory of one repo
  shares one memory directory**. Outside a repo, the project root is used.
  `autoMemoryDirectory` in any settings scope relocates it (absolute or `~/`-prefixed).
  Toggles: `autoMemoryEnabled` setting, `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1`.
- **That path is the only live location.** A `MEMORY.md` or memory-shaped directory
  anywhere else — `~/.claude/MEMORY.md`, `~/.claude/memory/`, a repo-local `memory/` —
  is not loaded by current Claude Code. Files there are inert no matter how good they
  are. `/context` is the human's confirmation; the doctor's test is the path.
- **`MEMORY.md` is an index, and only the index auto-loads**: the first 200 lines *or*
  25KB, whichever comes first. Content past the threshold is silently dropped at session
  start. Frontmatter and block-level HTML comments are stripped before both the load and
  the measurement (v2.1.211+), so they are free.
- **Claude Code enforces the cap at write time** (v2.1.210+): near a limit it reminds
  Claude to shorten the index; over a limit the write succeeds but returns an error
  telling Claude to rewrite it. An index the human hand-edited past the cap gets no such
  warning — it just loses its tail.
- **Topic files never load at startup.** Claude reads them on demand with ordinary file
  tools, or a selector attaches them by matching the query against their `description`.
  So a topic file costs zero launch context: its size is not a problem, and "bloat"
  is the wrong lens for it entirely.
- **`modified:`** is stamped automatically as an ISO 8601 timestamp on any memory file
  that already has frontmatter (v2.1.214+). Claude Code never adds frontmatter to a file
  that lacks it — so an unstamped file is also an unaudited one.
- **Machine-local and single-player.** Memory is not shared across machines or cloud
  environments, is not in version control, and is **not loaded into subagents** (a fork
  is the exception; a subagent's own `memory` directory is separate).
- Memory records are explicitly treated as *what was true at a point in time*. Official
  guidance is to verify a recalled memory against current files before acting on it, and
  to **delete** a memory that current reality contradicts rather than acting on it.

## What current models need — and no longer need

Current generation = Claude 4.5+ and Claude 5 family. The biggest generation shift is
**instruction-following precision**, which inverts old habits:

- **Emphasis calibration.** Old guidance leaned on "IMPORTANT" / "YOU MUST" to rescue
  adherence. Current models are more responsive to instructions and **overtrigger on
  aggressive language** — official migration guidance is to dial "CRITICAL: You MUST
  use X when…" back to "Use X when…". Dense ALL-CAPS/MUST blocks in an instruction
  file are now a smell (see heuristics), not a virtue. Reserve emphasis for the one or
  two rules that genuinely outrank everything else.
- **Remove compensating over-prompting.** Instructions written to fix old
  under-triggering ("If in doubt, use [tool]", "ALWAYS default to [tool]") now cause
  overtriggering. Replace blanket defaults with targeted conditions.
- **Motivation beats mandate.** Explaining *why* ("the output is read aloud, so never
  use ellipses") outperforms the bare rule — the model generalizes from the reason.
  A rule with no reason attached is both weaker and unverifiable later.
- **Positive framing.** Tell the model what to do instead of what not to do ("write
  flowing prose paragraphs" > "do not use markdown"). DO-NOT lists earn their place
  only as guardrails against specific, known failure modes.
- **No repetition.** Saying it twice doesn't strengthen it; it lengthens the file and
  invites the conflicting-rules arbitrary-pick failure.
- **Structure still helps**: markdown headers/bullets grouping related rules; XML tags
  for genuinely mixed content. Specificity still wins: "Use 2-space indentation" >
  "format code properly"; concrete enough to verify.
- **Model-specific pages exist per model family** (e.g. "Prompting Claude Fable 5" —
  effort levels, instruction following, long-run behavior). A refresh should pull the
  page matching the session's model family, not just the general page.

## Operative judgment rules for the doctor

Derived from the above; these are what Stages 2 and 5 act on:

1. Judge size against the 200-line target per auto-loaded file, and treat "adherence"
   as the budget being spent — an inert section isn't neutral, it taxes every rule
   around it.
2. Flag emphasis-density (many caps/MUST/NEVER tokens) as a smell on current models;
   propose de-escalation (`ask` — tone changes are judgment).
3. A rule with no attached reason is a `PRESENT` entry with `verifiable: partial` —
   propose attaching the why, or question whether it's real.
4. "Must happen every time" prose → hook-shaped. "Sometimes-relevant procedure" →
   skill-shaped. "Only matters for these files" → `paths:`-scoped rule. These come
   straight from the official mechanism table.
5. Never claim `@import` extraction saves context — it doesn't. Only skills, path-
   scoped rules, plain links, and deletion actually shrink the launch footprint.
6. **Route between CLAUDE.md and memory on mechanism, not on topic.** The two differ on
   four testable axes: who authors it (human / Claude), whether it reaches a fresh clone
   or a teammate (git / machine-local), whether a subagent sees it (yes / no), and
   whether a change is reviewable (diff / nothing). A durable convention anyone working
   the repo needs belongs in CLAUDE.md or a rule. A machine-local or personal fact
   sitting in a committed CLAUDE.md — especially in a public repo — has auto memory as a
   legitimate destination: it is machine-local by construction and never committed.
   Behavior that must hold *inside a dispatched subagent* can never live in memory.
7. **Judge `MEMORY.md` as an index, `CLAUDE.md` as a rules file.** The index's unit is
   one line — `- [Title](file.md) — hook`, under ~150 characters. An entry carrying its
   own detail, or carrying no link at all, is content squatting in the auto-loaded
   budget: demote the detail into the topic file and leave the hook. Judge the index by
   entry discipline and by whether it still points at files that exist, not by whether
   it reads well.
8. **Do not judge a topic file by its length.** It costs nothing at launch. Judge it on
   truth (rule 9), on whether its `description` frontmatter would actually get it
   recalled for the queries it should serve, and on whether it contradicts another
   memory or the repo.
9. **Apply the truth check to memory harder than anywhere else.** Memory is explicitly a
   point-in-time record with no review gate, so drift is the expected failure. A memory
   contradicted by the current repo is deleted, not patched around — that is the
   official instruction, not a preference. Merging memories means deleting the sources
   and writing one fresh file (preserving the oldest `created:`), which is how the
   harness's own pruning pass does it.
10. **A memory duplicated by CLAUDE.md loses.** CLAUDE.md is versioned, shared, and
    visible to subagents; the memory is none of those. Delete the memory and keep the
    rule — unless the memory is deliberately the machine-local variant, which is a
    `QUESTIONS` entry, not a move.

## Refresh procedure (`refresh` mode)

1. Read the stamp above. Fetch each stamped source, plus the model-specific prompting
   page for the **session's** model family if it differs from the stamped one.
2. Update this file **as a diff, not a rewrite**: change what the sources changed,
   keep the structure, and keep every statement traceable to a source. The diff is
   reviewed like any other doctor edit — uncommitted, in the plugin repo.
3. Update the stamp (model_family, cc_version from `claude --version`, refreshed date,
   any source URL that moved).
4. Do not fold in community lore or your own prompting intuitions — published Anthropic
   guidance only. If a judgment rule in this file no longer has a source, delete it.
5. After a refresh, run the skill's evals (`evals/evals.json`) — doctrine changes can
   silently change gating behavior.
