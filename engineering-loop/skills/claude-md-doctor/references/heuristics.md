# Heuristics

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
