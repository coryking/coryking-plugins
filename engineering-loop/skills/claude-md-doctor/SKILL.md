---
name: el:claude-md-doctor
description: "Audit and clean up CLAUDE.md, .claude/rules/*, and nested instruction files. Cheap triage first, autonomous from there — delivers the cleanup as a separate branch/PR so review is async on your terms. Use when the auto-loaded instruction surface has grown bloated, contradictory, or unfocused."
argument-hint: "(no args, or 'deep' to force Tier 3, or 'check' to triage-only-and-exit)"
---

# claude-md-doctor — Variant B: auto-tier, async via branch/PR

Diagnoses and cleans Claude Code instruction surfaces. Auto-selects effort tier from cheap triage. **No `AskUserQuestion`.** The dialogue medium is a separate git branch (and optional PR) carrying the artifact bundle and the rewrite as separate commits. The user reviews async — merge = approved, close = rejected, dangling branch = haven't looked yet.

Rationale: PRs are slow-rolling dialogue, not bureaucracy. The user reads them on their phone. Merge means yes, close means no, dangling means hasn't looked.

## When to use
- The harness is "complaining" about CLAUDE.md (long context, ignored rules, contradictions, a recently failed task that a clearer rule would have prevented).
- You want a hands-off audit that lands as a reviewable diff.
- A fresh-context check before sharing the project.

## What it does
1. **Triage** (<10s, no mining) — line counts, smell sweep, duplication check, HTML marker + `git log` since last run. Verdict table with chosen tier. **Tier 0 exits here** with the verdict printed inline (no branch, no bundle).
2. **Detect prior dangling cleanup branches** — `git branch --list 'claude-md-doctor/cleanup-*'`. If one exists and is unmerged, **stop and warn** rather than stacking work.
3. **Create cleanup branch** off current HEAD: `claude-md-doctor/cleanup-<ISO-timestamp>`. All subsequent commits land on this branch.
4. **Decompose** (Tier 2+) — apply the technique in `@./references/decompose-prompt.md` per file, grounded in `cc-explorer` MCP tools + `git log` evidence. Commit bundle: `chore: claude-md-doctor decompose pass for <files>`.
5. **Rewrite** — bold rewrite, applies right-mechanism routing. Updates HTML marker. Commit: `feat: claude-md-doctor cleanup — <one-line summary>` with the body being `proposed-changes.md` content (this is the PR description).
6. **Open PR if possible** — if `gh` is configured and the repo has a remote, `gh pr create --base <original-branch> --head claude-md-doctor/cleanup-<ts> --body @<bundle>/proposed-changes.md`. Otherwise, print branch name + diff command and exit.

Tier 1 skips decompose: mechanical wins (HTML-comment maintainer notes, dedupe duplicate lines, add missing `paths:` to scope-able rules) go straight to the rewrite commit.

`check` argument: triage and exit. No branch created.
`deep` argument: force Tier 3 regardless of triage signal.

## The four scopes
| Scope | Files | Edit policy |
|---|---|---|
| Managed | `/etc/claude-code/CLAUDE.md` etc. | Read-only |
| User-global | `~/.claude/CLAUDE.md`, `~/.claude/rules/*` | Write proposals to `user-global-proposals.md` only — these files are outside the worktree |
| Project | `./CLAUDE.md`, `./.claude/CLAUDE.md`, `./.claude/rules/*` | Edit on the cleanup branch |
| Local | `./CLAUDE.local.md` (gitignored) | Edit; commit warns it's gitignored |
| Nested | Subdir `CLAUDE.md` | Edit on the cleanup branch |

## Triage heuristics
Cheap signals, no mining:

- **Line count vs 200-line target** per file.
- **`git log` since the HTML marker.** Marker exists, no instruction file has changed since → bias toward Tier 0.
- **Wrong-mechanism regex sweep** — runbook patterns (`^[0-9]\.`, fenced code blocks), path-specific phrases in root CLAUDE.md (`"in src/"`, `"under foo/"`), settings-shaped phrases.
- **Duplication across hierarchy** — hash sections, compare across loaded files.
- **HTML-comment presence** — proxy for prior cleanup discipline.
- **Section staleness via `git blame`** — sections untouched > 60 days are scar-tissue candidates.

## Tiers
- **Tier 0** — Healthy. Verdict + exit. **<10s.** No branch.
- **Tier 1** — Mechanical wins. Single rewrite commit on cleanup branch.
- **Tier 2** — Targeted. Decompose commit + rewrite commit on cleanup branch.
- **Tier 3** — Deep. Same as Tier 2 but every loaded file, broad mining.

## Conservation of mass
Net auto-loaded surface should not grow. To add a rule:
1. Cite ≥2 independent incidents that show the rule earns its tokens, OR
2. Tag as `hypothesis — one instance` in `proposed-changes.md` and let the user decide via PR comment, OR
3. Remove/merge an existing rule.

Detail decays; abstractions survive.

## Right-mechanism routing
| Smell | Route to |
|---|---|
| Runbook with conditional triggers | Skill (`<plugin>/skills/<name>/SKILL.md`) |
| Path-specific guidance | `.claude/rules/<name>.md` with `paths:` frontmatter |
| "Always run X before commit/tool-use" | **Propose** a hook in `proposed-changes.md` (don't auto-create) |
| Permission / env / sandbox | **Propose** `settings.json` change in `proposed-changes.md` |
| ADR history / completed-decision narrative | `docs/decisions/<date>-<slug>.md`, or trust `git log` |
| Maintainer notes | HTML comment `<!-- ... -->` |
| Locked first-principles content | Wrap in `<!-- locked -->` ... `<!-- /locked -->` |
| Discoverable junk (`ls`-able trees, location lists) | Delete |

## N≥2 evidence gate (for MISSING/new rules)
A proposed new rule needs ≥2 independent incidents in chat-history or git. Use `cc-explorer` with user-side patterns. One instance → tag `hypothesis — one instance` in `proposed-changes.md` and let the user respond via PR comment.

## Locked sections
`<!-- locked -->` ... `<!-- /locked -->` fences are immutable to future runs. Use sparingly.

## Public-repo guardrail
On first run, `gh repo view --json visibility 2>/dev/null`. If `PUBLIC`, add `.claude/claude-md-doctor/` to `.gitignore` — same commit as the bundle, so reviewer sees one coherent change.

## Artifact bundle
`.claude/claude-md-doctor/<ISO-timestamp>/`:
- `triage.md` — verdict table, smells, chosen tier, reasoning
- `decompose.md` — per-file PRESENT/MISSING/DROP map with citations (Tier 2+)
- `proposed-changes.md` — **the PR description.** Per-file "why" narrative, conservation-of-mass accounting, open questions for the reviewer. Make it readable on a phone.
- `metadata.json` — `{commit_sha, tier, files_touched, started_at, ended_at, variant: "B", cleanup_branch}`
- `pre/` and `post/` — snapshots of every rewritten file
- `user-global-proposals.md` — proposals for files the skill couldn't edit

## HTML marker
After any Tier 1+ run **and after the cleanup is merged**, the marker should be updated. Since this variant doesn't merge, the rewrite commit on the cleanup branch sets the marker; if the branch is later merged, the marker comes along. If the branch is closed/abandoned, the marker is not in main and the next triage sees no marker (correct — no cleanup landed).

Marker format at line 1 of project CLAUDE.md:

```
<!-- last-decomposed: <commit-sha> @ <ISO-date> → .claude/claude-md-doctor/<ts>/ -->
```

## PR description template
`proposed-changes.md` body becomes the PR description (`gh pr create --body @<bundle>/proposed-changes.md`). Structure:

```
## Summary
<1 paragraph: tier chosen, files touched, net line delta, conservation-of-mass result>

## Why
<the framing: what triggered this run, what triage found>

## Per-file changes
<for each file: before-state → after-state → why → evidence>

## Hypothesis-only proposals (N=1 evidence)
<list each, with the single incident citation, asking the user to confirm/reject via PR comment>

## Right-mechanism moves
<what got extracted to a skill, path-scoped rule, docs/decisions/, HTML comment>

## Open questions
<things the skill flagged but didn't decide>
```

## Dogfood rule
This skill must pass its own triage. If a future run on this skill produces Tier 2+, the skill itself is bloated.
