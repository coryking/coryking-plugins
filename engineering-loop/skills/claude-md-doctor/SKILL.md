---
name: el:claude-md-doctor
description: "Diagnose CLAUDE.md / .claude/rules / nested instruction files for bloat, wrong-mechanism content, and scar tissue — then apply the high-confidence fixes in the same run. Writes an evidence bundle first, edits second; the uncommitted git diff is the review gate. Use when CLAUDE.md feels heavy, an auto-loaded file has grown past a screenful, or before adding a new rule."
argument-hint: "[blank = diagnose + apply | 'diagnose' = map only, no edits]"
---

# claude-md-doctor

Diagnoses the repo's instruction surface with evidence, then presses the button: moves the evidence supports unambiguously are applied in the same run; judgment calls are left as a checklist. Git is the undo — the run requires a clean tree going in and ends with an uncommitted diff for review.

## What this skill does

1. Triages the instruction surface (line counts, smells, duplication, marker freshness). Cheap.
2. If the surface is clean → emits a Tier-0 verdict and exits. Target: <10s.
3. Otherwise → decomposes each non-clean file using `references/decompose-technique.md` (PRESENT / MISSING / DROP CANDIDATES with citations).
4. Grounds DROP candidates with cc-explorer + git evidence (N≥2 utilization gate for proposing new rules in MISSING).
5. Writes the artifact bundle, with **every proposed move gated `apply: auto` or `apply: ask`** (gating table in `references/heuristics.md`).
6. **Applies the `auto` moves** — unless invoked with the `diagnose` argument — then validates the result and writes `execution-report.md`. Nothing is committed.

## What this skill does NOT do

- Commit, push, or otherwise change git state (reading history is fine). The uncommitted diff IS the review; `git stash` shelves it, `git checkout -- . && git clean -fd` discards it.
- Apply any `ask`-gated move. Deleting unique content on judgment, creating new rules, and compactions where reasonable people differ stay human decisions, checklist-only.
- Touch `~/.claude/*`. User-global findings go in `user-global-proposals.md`.
- Preserve false content. This skill explicitly rejects zero-information-loss dogma — see "Truth before relocation."

## Doctrines

**Evidence before deletion.** A unit is deleted (rather than relocated) only when the scar-tissue rubric or the wrong-mechanism table in `references/heuristics.md` confirms it: pure ADR history, discoverable-from-filesystem content, generic knowledge with zero repo-specific residue, or verified duplication. Conservation of mass otherwise: real signal gets a relocation target, not a delete.

**Truth before relocation.** Any unit that asserts checkable facts — filenames, paths, hostnames, ports, config values — gets those facts verified against the repo and live sources before it is kept or moved. A stale fact is never relocated verbatim: fix it at the authoritative source, or drop it and cite the drift as evidence for the drop. Relocating a lie preserves the lie, with a fresh timestamp on it.

**Rich abstracts, not bare links.** When a move leaves a pointer behind, the stub is 2–4 sentences carrying the concrete values — names, thresholds, the one command — followed by the link, so a fresh session answers common questions without opening the target. A bare "see X" link forces a file-read for every question; the synopsis is where the context savings actually happen.

**Loader semantics, verified live.** `@import` resolves recursively and auto-loads at session start — it is inline-equivalent, not progressive disclosure. `.claude/rules/*.md` with `paths:` frontmatter loads conditionally on matching files. A plain markdown link is the only true read-on-demand trapdoor. These semantics are version-dependent: when a routing decision hinges on how Claude Code loads a file, verify against current documentation (the `claude-code-guide` agent) instead of trusting this paragraph.

## The instruction surface (four scopes)

| Scope | Path | Owned by repo? | Editable here? |
|---|---|---|---|
| managed | `~/CLAUDE.md` if `<!-- Managed by chezmoi -->` marker present | no | no |
| user-global | `~/.claude/CLAUDE.md`, `~/.claude/rules/*.md` | no | no (read-only; surface findings in `user-global-proposals.md`) |
| project | `<repo>/CLAUDE.md`, `<repo>/**/CLAUDE.md` (nested) | yes | yes — `auto` moves and the HTML marker |
| project-local | `<repo>/.claude/rules/*.md` if present | yes | yes — `auto` moves only |

Public-repo signal: if `<repo>` is public (check `gh repo view --json visibility`), flag rules that contain personal-data / private-host names as **gitignore-or-extract** candidates — always `apply: ask`, never auto.

## Sectioning protocol

Treat every Markdown H2 (`## …`) as a unit. Bulleted lists under no heading are individual units. A unit wrapped in `<!-- locked -->` … `<!-- /locked -->` is **read-only input** — it can appear in PRESENT (with a "locked" note) but never in DROP CANDIDATES, never referenced for relocation, and never edited by Stage 5.

## Execution

### Stage 0 — Preconditions

`git status --porcelain` must be clean. If dirty: downgrade the whole run to diagnose-only, say so up front, and note it in the bundle. The `diagnose` argument forces map-only regardless of tree state.

### Stage 1 — Triage (always)

Announce: `claude-md-doctor: triage`.

For each file in the instruction surface (excluding managed):
- line count
- has HTML marker `<!-- last-decomposed: <sha> @ <date> -->`?
- regex smells (table in `references/heuristics.md`): code fences over 15 lines, bullet count > 25, repeated H2 names across files, ASCII trees, temporal phrasing
- git: `git log --format="%h %ai" -- <file>` — last touch, total commits
- if marker present: `git log <marker_sha>..HEAD -- <file>` — has anything changed since last run?

Compute a per-file tier:
- **Tier 0** — file is under 50 lines, no smells, marker fresh OR file untouched since last marker. Verdict: "healthy, skip."
- **Tier 1** — one or two smells, or no prior marker. Worth a decompose pass; no deep mining.
- **Tier 2** — three+ smells, or file over 150 lines, or duplicated H2s across files. Decompose + mine.
- **Tier 3** — auto-loaded surface over 300 lines total. Decompose all non-Tier-0 files + mine + cross-file duplication report.

If **all** files are Tier 0: write `triage.md` only, update no marker, edit nothing, exit. Report the time budget consumed.

### Stage 2 — Decompose (Tier 1+)

For each non-Tier-0 file, follow `references/decompose-technique.md` literally. Output the map into `decompose.md` as one section per file.

**Wrong-mechanism heuristics** (flag in DROP CANDIDATES with a "right-mechanism" tag):
- Long procedural how-to with code fences → **skill-shaped** (recommend: `~/.claude/skills/<name>/SKILL.md` or `<plugin>/skills/<name>/`).
- "When X happens, do Y automatically" → **hook-shaped** (recommend: `settings.json` hook).
- Path-scoped knowledge ("the esp-idf library is here", "stealth-research is at …") → **path-scoped-rule shaped** (recommend: `~/.claude/rules/<topic>.md` with a `paths:` field).
- Permissions / env / model preference → **settings-shaped**.
- ADR-style historical narrative ("we later hit the threshold", "this evolved from X to Y") → **docs/-shaped**, not instruction surface.
- Discoverable from filesystem (ASCII trees, file inventories) → **drop, the model can `ls`**.

**Scar-tissue detection.** A unit is scar tissue if:
- it was added once and never edited (`git log -p` on that line range shows one commit),
- AND mining shows zero or one substantive usage hits across the project's chat history,
- AND nothing else in the surface references it.

**Truth check.** For every unit surviving into PRESENT or proposed for relocation, verify its checkable facts (filenames against `ls`, values against the authoritative file, hostnames/ports against configs or live sources when cheap). Record each verified/stale verdict in `decompose.md`; stale facts feed the move's evidence line.

### Stage 3 — Mining (Tier 2+, optional in Tier 1)

For each DROP CANDIDATE, validate with cc-explorer:
- `search_projects` with patterns derived from the unit's distinctive vocabulary (function names, paths, jargon).
- Hit count < 3 across sessions AND no session shows the rule being *acted upon* (vs. just quoted) → **confirmed scar tissue**.
- Hit count ≥ 3 AND visible behavioral pull on at least 2 sessions → **demote from DROP to PRESENT-but-verify**.

For each MISSING entry where the recommendation is "add a new rule":
- Require **N≥2 evidence**: at least two distinct sessions where the absent rule would have changed behavior. Cite both. If only one, soften to a question in `decompose.md` rather than a recommendation in `proposed-changes.md`. MISSING entries are always `apply: ask`.

A thin corpus (few or zero sessions) caps confidence: without behavioral evidence, "confirmed scar tissue" downgrades to "suspected," which gates the move `ask` unless a structural rule (duplication, discoverability, staleness) independently confirms it.

### Stage 4 — Write the bundle

Bundle location: `<repo>/.claude/claude-md-doctor/<ISO-timestamp>/` (timestamp like `2026-05-14T1830Z`).

Files (skeletons in `references/bundle-template.md`):

1. **`triage.md`** — one-row-per-file table: path, lines, tier, smells, last-touched-sha, since-marker-commits, verdict. Summary line at the very top.
2. **`decompose.md`** — per file: PRESENT / MISSING / DROP CANDIDATES / ALTERNATIVE READING / QUESTIONS, with verifiability and truth-check verdicts. Locked sections marked `[locked]`.
3. **`proposed-changes.md`** — the move checklist. Each move carries its `apply: auto | ask` gate and the evidence that earned it. At most 10 moves; longer "why" narratives keyed by number at the bottom.
4. **`user-global-proposals.md`** — same shape, scoped to `~/.claude/*`. Header: "This skill cannot edit user-global files. Apply manually via chezmoi if managed."
5. **`metadata.json`** — `{skill_version, repo_sha, branch, mode, tier_by_file, files_analyzed, started_at, finished_at, marker_sha}`.
6. **`execution-report.md`** — written by Stage 6 (absent in diagnose-only runs).

Then write the marker at the top of `<repo>/CLAUDE.md` (and only that file), replacing any prior marker:

```
<!-- last-decomposed: <head-sha> @ <ISO-date> → see .claude/claude-md-doctor/<ts>/ -->
```

Keep the bundle readable end-to-end on a phone. Summary info at the top of every file.

**Diagnose-only runs stop here.** Print the bundle path and exit.

### Stage 5 — Apply

Apply every `apply: auto` move exactly as its "How" line specifies — no improvising beyond the map. If execution surfaces a judgment the map didn't make, stop that move, regrade it `ask`, and record why in `execution-report.md`.

- Replacement stubs follow the rich-abstract rule.
- Stale facts encountered mid-move are fixed at the authoritative source or dropped, per the truth-check verdicts — never copied forward.
- Check off applied moves in `proposed-changes.md`; `ask` moves stay unchecked.
- Do not commit anything.

### Stage 6 — Validate + report

- Re-run the Stage-1 smell regexes on every edited file; any surviving smell must be explained (e.g., locked unit).
- Every markdown link and `@import` in edited files resolves to an existing file.
- Confirm no `ask` move was applied and no locked unit changed.
- Write `execution-report.md`: before/after line counts per file, moves applied vs. left, validation results, and one honest line — did any applied move require judgment the bundle hadn't already made?
- Print: bundle path, `git diff --stat`, and the undo commands (`git stash` to shelve; `git checkout -- . && git clean -fd` to discard, listing any new files created).

## Self-test

`evals/` holds synthetic fixture surfaces and assertion lists (`evals/evals.json`). After changing this skill's heuristics, gating, or stages, run the evals: copy a fixture to a temp dir, `git init` + commit it, run the skill against it, check every assertion. The fixtures are seeded with known defects (a lying ASCII tree, a stale hostname, a verbatim docs duplicate) — never "fix" a fixture to make an assertion pass.

## References

- `references/decompose-technique.md` — verbatim decompose-prompt gist; the methodology core.
- `references/heuristics.md` — smell regex table, wrong-mechanism routing, scar-tissue rubric, apply-gating table, truth-check procedure, rich-abstract stub spec.
- `references/bundle-template.md` — bundle skeletons.

@./references/decompose-technique.md
@./references/heuristics.md
@./references/bundle-template.md
