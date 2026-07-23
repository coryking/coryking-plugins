# Model guidance primer

The doctor's judging doctrine for "what works in an instruction file," distilled from
Anthropic's published guidance. **Normal runs judge against this file only — never
against live docs or trained intuition** — so that two runs on the same repo agree.
`refresh` mode is the only thing that changes this file.

```yaml
# provenance stamp — refresh mode updates this; Stage 0 checks it
model_family: claude-5 (fable) / claude-4.8-era docs
cc_version: 2.1.218
refreshed: 2026-07-23
sources:
  - https://code.claude.com/docs/en/best-practices.md          # CLAUDE.md include/exclude, sizing
  - https://code.claude.com/docs/en/memory.md                  # loading mechanics, rules, imports
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
- **Auto memory** `MEMORY.md`: only the first 200 lines / 25KB load; CLAUDE.md has no
  hard cutoff — length costs adherence instead.

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
