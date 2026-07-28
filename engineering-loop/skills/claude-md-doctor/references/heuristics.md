# Heuristics

## Apply-gating table

Every move in `proposed-changes.md` carries exactly one gate. When in doubt, gate `ask` — a wrongly-gated `auto` deletes something a human wanted; a wrongly-gated `ask` costs one checkbox.

| Gate | Criteria (any one suffices) |
|---|---|
| `auto` | Verified verbatim duplicate of an existing doc (compared, not assumed) · discoverable-from-filesystem content (ASCII trees, file inventories) · generic tool knowledge with zero repo-specific residue (git tutorials, stock troubleshooting) · fact verified stale by the truth check · relocation whose target file already exists and whose content passed the truth check |
| `ask` | Deletes unique content on judgment · creates a new file · MISSING additions (new rules) · "suspected" evidence rather than "confirmed" · public-repo personal-data flags · compactions where a defensible case exists both ways (e.g. a hardware cheat-sheet) · anything adjacent to a `<!-- locked -->` unit |

### Memory-scope gating

Memory lives outside the repo and has no git undo, so Stage 0's snapshot is its review
gate. Within that, the editable unit is narrow: **`MEMORY.md` gets edited; topic files
get deleted or left alone.** Rewriting a topic file's body is always a judgment.

| Gate | Memory-scope criteria |
|---|---|
| `auto` | Demoting an over-long index entry's detail into the topic file it already links · removing an index pointer whose target file does not exist · deleting a memory whose content is a verified duplicate of a live CLAUDE.md/rule unit · deleting a memory whose checkable facts are verified stale *and* whose subject is authoritative elsewhere in the repo |
| `ask` | Promoting a memory into CLAUDE.md / a rule / a skill · demoting a CLAUDE.md unit into memory · merging memories (writes a new file) · resolving a contradiction between two memories · rewriting any topic-file body · deleting on suspicion rather than verified duplication or verified staleness · anything under `team/` |

## Truth-check procedure

For each unit kept or relocated, extract its checkable claims and verify the cheap ones:

1. **Filenames / paths** → `ls` / `test -e` against the repo. A tree or inventory naming a file that doesn't exist is a lying unit.
2. **Values duplicated from an authoritative file** (config values, pin assignments, thresholds) → diff against that file. The instruction file never wins a disagreement.
3. **Hostnames / ports / URLs** → check configs first; hit the live source only when one cheap command answers it.
4. **Memory files** get the same three checks. Their `modified:` frontmatter is a prior for *where to look first*, not evidence of correctness — a fact stamped today can already be wrong, and an old stamp on a still-true fact is not a defect. Order the memory truth check by staleness, then judge on the fact.

Verdicts land in `decompose.md` as `(facts: verified | stale — <what drifted>)`. Stale → the fact is corrected at its authoritative source or dropped with the drift cited as evidence. Never relocate a stale fact verbatim; never silently patch it either — the fix must appear in the bundle.

## Rich-abstract stub spec

When a move replaces a section with a pointer, the stub must carry, in 2–4 sentences:
- the name of the thing (tool, framework, subsystem) so no read is needed to learn what's there,
- the one constraint or threshold that determines correctness,
- the single most-used command, if one exists,
- what *additional* detail the linked file holds — so a session can judge whether to follow the link.

A bare "See `<file>`" is a validation failure in Stage 6. Exception: pointers to content that is pure reference (external doc URLs, ADR history) may be one line.

**This spec does not apply to `MEMORY.md`.** The index has the opposite contract — one
line, ~150 characters, hook not synopsis (below). Applying the rich-abstract rule to an
index entry is how the index becomes a dump.

## Memory-surface checks

`MEMORY.md` is auto-loaded; topic files are not. They fail differently and are checked
differently. Loading mechanics and the routing rationale live in `model-guidance.md`.

### `MEMORY.md` index contract

Every line is `- [Title](file.md) — one-line hook`. ~150 characters is the target the
harness's own consolidation pass writes to; ~200 is where a line is unambiguously
carrying content and becomes a move. Lines between the two are fine — do not generate a
move per long-ish line.

| Check | Test | Verdict |
|---|---|---|
| Content in the index | entry over ~200 chars | detail belongs in the topic file — demote, keep the hook (`auto`) |
| Orphan entry | entry has no `](…md)` link | it is content, not a pointer — the memory it describes may not exist at all |
| Dead pointer | linked file absent from the directory | remove the entry (`auto`) |
| Unindexed memory | topic file with no entry in the index | it can still be recalled by `description`, but nothing orients to it — QUESTIONS |
| Over the load cap | >200 lines or >25KB after stripping frontmatter and block HTML comments | everything past the cap silently never loads — report the cut point by name |
| Index-as-dump | more than a third of entries breach the length rule | the file has stopped being an index; the finding is the pattern, not each line |

### Topic-file checks

| Check | Test | Verdict |
|---|---|---|
| Stale fact | truth check contradicts the repo | delete, don't patch — official guidance is deletion |
| Duplicated by the instruction surface | content restates a live CLAUDE.md / rule unit | CLAUDE.md wins; delete the memory (`auto` when verified) |
| Contradicts another memory | two memories give opposing guidance | the arbitrary-pick failure — surface both, gate `ask` |
| Weak `description` | frontmatter description is a title restatement, or absent | it is the retrieval surface; a memory that never gets recalled is inert |
| No frontmatter at all | file has no YAML block | Claude Code never adds frontmatter, so the file also never gets a `modified:` stamp — its age is unauditable |
| Wrong mechanism | see routing rows below | route it; do not just delete |

Length is **not** a topic-file smell. It costs no launch context.

## Smell regex table

| Smell | Regex / test | Implication |
|---|---|---|
| Long code fence | fence with >15 inner lines | skill-shaped, extract |
| Many bullets under one H2 | >12 bullets in one section | section is a list, not a principle — likely fragment of a skill or rule |
| ASCII tree | `^[│├└─]` line at start | discoverable from filesystem, drop |
| Path-anchored fact | line contains a `~/projects/` or absolute path *and* is one of N>=3 sibling bullets | path-scoped-rule shaped |
| Temporal phrase | present-drift: "now uses", "recently", "currently"; past-tense origin narration: "we later", "used to", "back when", "predates", "was rewritten", "no longer", "at the time" | ADR / scar tissue suspect |
| Self-reference | file mentions its own path | recursion — flag in QUESTIONS |
| Duplicate H2 across scopes | same H2 in two CLAUDE.mds | duplication, candidate for one-authoritative-source consolidation |
| Emphasis density | >5 ALL-CAPS imperative tokens (MUST/NEVER/ALWAYS/IMPORTANT/CRITICAL) in one file | over-trigger risk on current models (see model-guidance primer); propose de-escalation as `ask` |

## Wrong-mechanism routing table

| If the unit looks like… | Right mechanism |
|---|---|
| "How to do <multi-step procedure>" with code | `skills/<name>/SKILL.md` |
| "When X, automatically do Y" | hook in `.claude/settings.json` |
| Per-path knowledge ("the X library is here") | `~/.claude/rules/<topic>.md` with `paths: [...]` |
| Tool permission / env var / model pin | `.claude/settings.json` |
| Origin story / decision narrative | `docs/decisions/<adr>.md` or commit message |
| File inventory / directory map | drop — `ls` exists |
| API reference / schema | `docs/` |
| **Memory** holding a convention a teammate or fresh clone needs | promote to `CLAUDE.md` or a rule — memory is machine-local, ungitted, and invisible to subagents |
| **Memory** holding a multi-step procedure | promote to a skill — memory has no progressive disclosure of its own |
| **Memory** that must hold inside a dispatched subagent | promote to `CLAUDE.md` / rule / skill; memory does not reach subagents |
| **Memory** that is per-path knowledge | promote to a rule with `paths:` — it then loads exactly when relevant |
| Committed instruction unit holding a machine-local or personal fact (host names, local paths, one operator's preference) — especially in a public repo | demote to auto memory: machine-local by construction, never committed |

The last row is the only routing that runs *toward* memory, and it is the better answer
to a public-repo personal-data flag than "gitignore it": a gitignored file is still a
file someone can commit by accident.

## Scar-tissue rubric

A unit is **confirmed scar tissue** when ALL hold:
1. `git log -p` on the line range shows one commit (added, never edited).
2. cc-explorer `search_projects` on the unit's distinctive vocabulary returns <3 hits across sessions OR all hits are the rule quoting itself, not being acted on.
3. No other instruction file references it.

A unit is **suspected scar tissue** when 2 of the 3 hold. Surface as DROP CANDIDATE with a "scar (suspected)" tag and an evidence line.

Memory files are ungitted, so criterion 1 has no test there and a memory can never reach "confirmed" by this rubric. Deleting a memory needs verified duplication or a verified stale fact instead; on chat evidence alone it stops at "suspected" and gates `ask`.

## Public-repo signals

If `gh repo view --json visibility` is `PUBLIC`:
- Personal data, private hostnames, internal project codenames in any *committed* instruction file → flag in `proposed-changes.md`. Destinations in preference order: **auto memory** (machine-local by construction, cannot be committed by accident), `~/.claude/rules/` (user-global), gitignore (weakest — the file still exists in the tree).
- A `.claude/rules/*.md` checked into a public repo with personal data is the same flag.
- The bundle itself can leak. Never copy memory content into a bundle that is not gitignored, and never quote a personal fact into `proposed-changes.md` — cite it by file and line instead.

## N≥2 evidence gate for MISSING

A MISSING entry that proposes adding a *new* rule must cite ≥2 distinct chat sessions where the absent rule would have changed behavior. Cite session IDs + dates. If only 1 session, downgrade to a QUESTION rather than a recommendation.

A MISSING entry that proposes adding a *verifiability field* (e.g. `paths:` on an existing rule) does not need N≥2 — it's a property of the rule itself, not a new behavioral pull.

## Tier-budget guidance

| Tier | Mining budget | Decompose depth |
|---|---|---|
| 0 | none | none (skip) |
| 1 | optional, 1 cc-explorer search at most | decompose every non-Tier-0 file |
| 2 | 2–3 cc-explorer searches, git log per file | decompose + per-DROP evidence |
| 3 | full mining, cross-file duplication report | everything + ALTERNATIVE READING section per file |

Don't burn budget chasing certainty on cheap calls. Tier 1 should still feel cheap.
