---
name: el:claude-md-doctor
description: "Diagnose CLAUDE.md / .claude/rules / nested instruction files for bloat, wrong-mechanism content, and scar tissue. Produces a decomposition map and proposed-changes checklist. Does NOT edit instruction files. Use when CLAUDE.md feels heavy, an auto-loaded file has grown past a screenful, or before adding a new rule."
argument-hint: "[blank — scope is the current repo's instruction surface]"
---

# claude-md-doctor (Variant D — diagnose only)

**Thesis being tested.** From the decompose-prompt gist: *"The decomposition pass is mostly forcing the model to spend tokens narrating something it already does internally. The value of externalizing the model's intermediate reasoning is intervenability, not capability."* So this skill stops at the map. **Cory (or a downstream agent) does the actual edits.** If the bundle is good enough that he can act on it in 10 minutes, a rewriting variant is over-engineering.

## What this skill does

1. Triages the instruction surface (line counts, smells, duplication, marker freshness). Cheap.
2. If the surface is clean → emits a Tier-0 verdict and exits. Target: <10s.
3. Otherwise → decomposes each non-clean file using the technique in `references/decompose-technique.md` (PRESENT / MISSING / DROP CANDIDATES with citations).
4. Grounds DROP candidates with cc-explorer + git evidence (N≥2 utilization gate for proposing new rules in MISSING).
5. Writes an artifact bundle. Updates one HTML marker at the top of the project CLAUDE.md. **Exits without editing anything else.**

## What this skill does NOT do

- Rewrite, restructure, condense, or "clean up" any instruction file.
- Move content between files. (It proposes moves; Cory executes them.)
- Touch `~/.claude/*` from this skill. User-global findings go in the bundle.
- Ask follow-up questions. The bundle is the conversation.

If you feel the urge to fix something you find — resist, and add an entry to `design-flaws.md` in the bundle. That entry is evidence for/against Variant D's thesis.

## The instruction surface (four scopes)

| Scope | Path | Owned by repo? | Editable here? |
|---|---|---|---|
| managed | `~/CLAUDE.md` if `<!-- Managed by chezmoi -->` marker present | no | no |
| user-global | `~/.claude/CLAUDE.md`, `~/.claude/rules/*.md` | no | no (read-only; surface findings in `user-global-proposals.md`) |
| project | `<repo>/CLAUDE.md`, `<repo>/**/CLAUDE.md` (nested) | yes | **only the HTML marker on the root project CLAUDE.md** |
| project-local | `<repo>/.claude/rules/*.md` if present | yes | no edits — propose only |

Public-repo signal: if `<repo>` is public (check `gh repo view --json visibility`), flag rules that contain personal-data / private-host names as **gitignore-or-extract** candidates in `proposed-changes.md`.

## Sectioning protocol

Treat every Markdown H2 (`## …`) as a unit. Bulleted lists under no heading are individual units. A unit wrapped in `<!-- locked -->` … `<!-- /locked -->` is **read-only input** — it can appear in PRESENT (with a "locked" note) but **never** in DROP CANDIDATES or be referenced for relocation.

## Execution

### Stage 1 — Triage (always)

Announce: `claude-md-doctor: triage`.

For each file in the instruction surface (excluding managed):
- line count
- has HTML marker `<!-- last-decomposed: <sha> @ <date> -->`?
- regex smells: code fences over 15 lines (likely skill-shaped), bullet count > 25 (likely fragmented), repeated H2 names across files (likely duplication)
- git: `git log --format="%h %ai" -- <file>` — last touch, total commits
- if marker present: `git log <marker_sha>..HEAD -- <file>` — has anything changed since last run?

Compute a per-file tier:
- **Tier 0** — file is under 50 lines, no smells, marker fresh OR file untouched since last marker. Verdict: "healthy, skip."
- **Tier 1** — one or two smells, or no prior marker. Worth a decompose pass; no deep mining.
- **Tier 2** — three+ smells, or file over 150 lines, or duplicated H2s across files. Decompose + mine.
- **Tier 3** — auto-loaded surface over 300 lines total. Decompose all non-Tier-0 files + mine + cross-file duplication report.

If **all** files are Tier 0: write `triage.md` only, update no marker, exit. Report the time budget consumed.

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

### Stage 3 — Mining (Tier 2+, optional in Tier 1)

For each DROP CANDIDATE, validate with cc-explorer:
- `search_projects` with patterns derived from the unit's distinctive vocabulary (function names, paths, jargon).
- Hit count < 3 across sessions AND no session shows the rule being *acted upon* (vs. just quoted) → **confirmed scar tissue**.
- Hit count ≥ 3 AND visible behavioral pull on at least 2 sessions → **demote from DROP to PRESENT-but-verify**.

For each MISSING entry where the recommendation is "add a new rule":
- Require **N≥2 evidence**: at least two distinct sessions where the absent rule would have changed behavior. Cite both. If only one, soften to a question in `decompose.md` rather than a recommendation in `proposed-changes.md`.

### Stage 4 — Write the bundle

Bundle location: `<repo>/.claude/claude-md-doctor/<ISO-timestamp>/` (timestamp like `2026-05-14T1830Z`).

Files:

1. **`triage.md`** — one-row-per-file table: path, lines, tier, smells, last-touched-sha, since-marker-commits, verdict. Summary line at the very top: `N files, X total auto-loaded lines, Y Tier-0, Z need attention.`
2. **`decompose.md`** — per file: `## <path>` → PRESENT / MISSING / DROP CANDIDATES / ALTERNATIVE READING / QUESTIONS. Use the verbatim output shape from the decompose technique. Each PRESENT entry carries a **verifiability assessment**: `(verifiable: yes | partial | no — <reason>)`. Locked sections marked `[locked]`.
3. **`proposed-changes.md`** — **the checklist Cory works through.** Top-of-file: at most 10 numbered moves, each with: source file + section name, target mechanism, the "why" (2 sentences max), and a one-line how. Format below. The bottom of the file holds longer "why" narratives keyed by move number.
4. **`user-global-proposals.md`** — same shape but scoped to `~/.claude/*`. Header: "This skill cannot edit user-global files. Apply manually via chezmoi if managed."
5. **`metadata.json`** — `{repo_sha, branch, tier_by_file, files_analyzed, started_at, finished_at, marker_sha}`.
6. **`design-flaws.md`** — only if the agent felt the urge to do more than diagnose. Each entry: what you wanted to do, why diagnose-only blocked it, whether the bundle is actually sufficient. **This is Variant D's calibration data.** If empty, write a one-line confirmation that the map alone felt sufficient.

**`proposed-changes.md` row format:**

```
### N. <verb> <unit-name> from <source> → <target>
- **Why:** <2 sentences, behavioral, not aesthetic>
- **How:** <one-line action: create file X with content Y; or relocate paragraph Z>
- **Evidence:** <chat hits / git-untouched-since / N≥2 citation>
```

Keep the bundle readable end-to-end on a phone. Summary info at the top of every file.

### Stage 5 — Marker + exit

Write at top of `<repo>/CLAUDE.md` (and only that file), inserting before line 1:

```
<!-- last-decomposed: <head-sha> @ <ISO-date> → see .claude/claude-md-doctor/<ts>/ -->
```

If a prior marker exists, replace it. **Do not touch any other file.** Print bundle path. Exit.

## Calibration check (before exit)

Ask yourself, then add a one-line answer to the end of `design-flaws.md`:
- *If Cory reads `proposed-changes.md` on his phone, does he know what to do without re-opening the source files?*
- *Did mining produce evidence that genuinely changed a recommendation, or was it ceremonial?*

Be honest. The skill's job is to test the thesis, not to validate it.

## References

- `references/decompose-technique.md` — verbatim decompose-prompt gist; the methodology core.
- `references/heuristics.md` — smell regex table, wrong-mechanism routing table, scar-tissue rubric.
- `references/bundle-template.md` — example bundle skeleton for shape reference.

@./references/decompose-technique.md
@./references/heuristics.md
@./references/bundle-template.md
