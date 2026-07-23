# Heuristics

## Apply-gating table

Every move in `proposed-changes.md` carries exactly one gate. When in doubt, gate `ask` — a wrongly-gated `auto` deletes something a human wanted; a wrongly-gated `ask` costs one checkbox.

| Gate | Criteria (any one suffices) |
|---|---|
| `auto` | Verified verbatim duplicate of an existing doc (compared, not assumed) · discoverable-from-filesystem content (ASCII trees, file inventories) · generic tool knowledge with zero repo-specific residue (git tutorials, stock troubleshooting) · fact verified stale by the truth check · relocation whose target file already exists and whose content passed the truth check |
| `ask` | Deletes unique content on judgment · creates a new file · MISSING additions (new rules) · "suspected" evidence rather than "confirmed" · public-repo personal-data flags · compactions where a defensible case exists both ways (e.g. a hardware cheat-sheet) · anything adjacent to a `<!-- locked -->` unit |

## Truth-check procedure

For each unit kept or relocated, extract its checkable claims and verify the cheap ones:

1. **Filenames / paths** → `ls` / `test -e` against the repo. A tree or inventory naming a file that doesn't exist is a lying unit.
2. **Values duplicated from an authoritative file** (config values, pin assignments, thresholds) → diff against that file. The instruction file never wins a disagreement.
3. **Hostnames / ports / URLs** → check configs first; hit the live source only when one cheap command answers it.

Verdicts land in `decompose.md` as `(facts: verified | stale — <what drifted>)`. Stale → the fact is corrected at its authoritative source or dropped with the drift cited as evidence. Never relocate a stale fact verbatim; never silently patch it either — the fix must appear in the bundle.

## Rich-abstract stub spec

When a move replaces a section with a pointer, the stub must carry, in 2–4 sentences:
- the name of the thing (tool, framework, subsystem) so no read is needed to learn what's there,
- the one constraint or threshold that determines correctness,
- the single most-used command, if one exists,
- what *additional* detail the linked file holds — so a session can judge whether to follow the link.

A bare "See `<file>`" is a validation failure in Stage 6. Exception: pointers to content that is pure reference (external doc URLs, ADR history) may be one line.

## Smell regex table

| Smell | Regex / test | Implication |
|---|---|---|
| Long code fence | fence with >15 inner lines | skill-shaped, extract |
| Many bullets under one H2 | >12 bullets in one section | section is a list, not a principle — likely fragment of a skill or rule |
| ASCII tree | `^[│├└─]` line at start | discoverable from filesystem, drop |
| Path-anchored fact | line contains a `~/projects/` or absolute path *and* is one of N>=3 sibling bullets | path-scoped-rule shaped |
| Temporal phrase | "now uses", "recently", "currently", "we later" | ADR / scar tissue suspect |
| Self-reference | file mentions its own path | recursion — flag in QUESTIONS |
| Duplicate H2 across scopes | same H2 in two CLAUDE.mds | duplication, candidate for one-authoritative-source consolidation |

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

## Scar-tissue rubric

A unit is **confirmed scar tissue** when ALL hold:
1. `git log -p` on the line range shows one commit (added, never edited).
2. cc-explorer `search_projects` on the unit's distinctive vocabulary returns <3 hits across sessions OR all hits are the rule quoting itself, not being acted on.
3. No other instruction file references it.

A unit is **suspected scar tissue** when 2 of the 3 hold. Surface as DROP CANDIDATE with a "scar (suspected)" tag and an evidence line.

## Public-repo signals

If `gh repo view --json visibility` is `PUBLIC`:
- Personal data, private hostnames, internal project codenames in any *committed* instruction file → flag in `proposed-changes.md` with mechanism = "move to `~/.claude/rules/` (user-global) or gitignore".
- A `.claude/rules/*.md` checked into a public repo with personal data is the same flag.

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
