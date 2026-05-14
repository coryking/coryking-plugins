---
name: claude-md-doctor
description: "Cleans up CLAUDE.md, AGENTS.md, and .claude/rules/*.md — the auto-loaded instruction surfaces — when they have drifted, bloated, or accumulated wrong-mechanism content. Use when the user says the harness is complaining about CLAUDE.md, instruction files feel bloated, rules feel stale, or you want a periodic auto-loaded-context audit. Variant B: PR-as-dialogue (fire-and-forget; review happens async on a branch/PR)."
argument-hint: "[blank for auto-tier, or tier:0|1|2|3, or scope:project|user|rules|all]"
---

# claude-md-doctor (Variant B: PR-as-dialogue)

Audits and rewrites the **auto-loaded instruction surfaces** of a project — CLAUDE.md, AGENTS.md, `.claude/rules/*.md`, and (when in scope) `~/.claude/CLAUDE.md` + `~/.claude/rules/*.md`. Detects bloat and wrong-mechanism content, applies the **decompose-prompt** technique, and ships proposed rewrites as a **branch + pull request** instead of an in-session AskUserQuestion loop.

The user's framing: *"PRs are slow-rolling dialogue, not bureaucracy. The lightest channel for anything outside the workspace sandbox. He reads them on his phone."* This variant treats merge/close as the interactive signal.

## When to Use

- User mutters "the harness is complaining about my CLAUDE.md again, wtf."
- After a string of commits, before opening a real PR, when CLAUDE.md feels long.
- Periodic hygiene pass — run on any repo with a `<!-- last-decomposed -->` marker older than N commits.

## Out of Scope

- Multi-committer coordination (no team workflow assumptions).
- Continuous/daemon operation.
- General documentation cleanup. **Only** the auto-loaded instruction surfaces.

## Stance

- **Be bold.** These files are ephemeral prompts, versioned, no backwards-compat. Wholesale rewrites encouraged when the file has rotted.
- **Conservation of mass.** Every line added has to earn its place; net mass conserved or negative.
- **N≥2 evidence gate.** A proposed new rule needs two independent observations (commits, chat moments, code patterns). Single-instance findings get tagged "hypothesis, one instance".
- **Read intent, not text.** Decompose what the file is *trying to produce*; don't surface-edit.
- **Honor `<!-- locked -->` sections.** First-principles markers are immutable; reference them, don't rewrite them.
- **Public-vs-private.** Default-assume the repo is public. **Never** include chat content, session UUIDs, personal data, or export files in the artifact bundle or PR description. Triage and decompose maps must be safe to publish.

## Tiered Effort (auto-selected)

Run cheap **triage** first — it's always Tier 0 and exits in < 10 seconds on a healthy repo. Triage decides the rest.

| Tier | Trigger | What happens | Output |
|------|---------|--------------|--------|
| **0** | Files under 200 lines, no obvious wrong-mechanism content, `<!-- last-decomposed -->` recent or absent and the repo is small | Print triage summary; exit | `triage.md` only, **no branch created** |
| **1** | A few mechanical fixes (stray HTML, broken refs, duplicated rule under N=2) | Apply fixes inline on a commit to the working branch; no PR | direct commit |
| **2** | Bloat detected, mechanism misfits, drift signals | Full decompose pass → propose rewrites → **branch + PR** | branch `claude-md-doctor/<ts>` with PR |
| **3** | Tier 2 conditions **plus** locked sections appear stale, or N≥2 contradictions, or > 400 lines on a single file | Full decompose pass + flag locked-section staleness in PR body (don't edit locked content) | branch `claude-md-doctor/<ts>` with PR, plus a `needs-cory` callout |

Tier 0 is a feature. Most runs should land there. If `argument-hint` supplied `tier:N`, honor it but still run triage first.

## Workflow

1. **Triage (always).** Read the target files. Measure: line counts, ratio of verifiable rules, HTML-comment hygiene, presence of `<!-- last-decomposed -->`, repo public/private signal (`git remote -v` + LICENSE), `git log` since the marker. Write `triage.md`. Pick the tier.

2. **Public-vs-private detection.** Treat repo as public unless `git remote get-url origin` clearly resolves to a private host AND the user has explicitly told the skill otherwise. Public posture wins ties. Ensure `.claude/claude-md-doctor/` is gitignored if not already (add it if missing — that's a Tier 1 mechanical fix).

3. **Decompose-prompt pass (Tier 2+).** See `@./references/decompose-prompt.md`. Produce `decompose.md` — the PRESENT / MISSING / DROP-CANDIDATES map. Cite line numbers and units. **The map is the deliverable, not the rewrite.** The rewrite is downstream of the map.

4. **Mine evidence (Tier 2+).** For each proposed new rule or each "drop because obsolete" claim, find evidence:
   - `git log --oneline -S "<phrase>" -- CLAUDE.md` for rule churn.
   - cc-explorer MCP tools (`search_project`, `grep_session`, `read_turn`) for chat-history evidence of *how the rule actually plays out*. Project is the repo CWD.
   - `git log --since` walk to spot patterns that should be a rule (N≥2 commits exhibiting the same fix).
   - **Never quote the evidence in the artifact bundle.** Cite counts and shapes only. (Public-repo guardrail.)

5. **Mechanism re-routing.** For each retained line, confirm the mechanism is right. Cheat-sheet inlined below — full table in `@./references/mechanism-routing.md`:
   - Multi-step procedure → skill
   - Scoped to part of tree → `.claude/rules/<topic>.md` with `paths:` frontmatter
   - "Must run at X point" → hook in `settings.json`
   - Permission / env / model → `settings.json`
   - Maintainer note for humans → HTML block comment (stripped before injection)
   - Always-needed fact → CLAUDE.md

6. **Verifiability test.** Every retained rule must be concretely verifiable — a reader can tell from looking at code/output whether the rule was followed. Vibes-rules get dropped or rewritten.

7. **Calibration check.** Read the proposed rewrite and ask: does it over-claim (asserting rules the project doesn't actually follow) or under-claim (omitting things that are obviously true)? Flag in `proposed-changes.md`.

8. **Write the artifact bundle.** Path: `.claude/claude-md-doctor/<timestamp>/`. See `@./references/artifact-bundle.md` for the exact shape. Always includes `triage.md`, `metadata.json`. Tier 2+ adds `decompose.md`, `proposed-changes.md`, `pre/`, `post/`.

9. **Ship the PR (Tier 2+).** See `@./references/pr-flow.md`. Create branch `claude-md-doctor/<timestamp>` off current HEAD, commit the artifact bundle + the rewrites, push, and `gh pr create` if remote is available. If no `gh` or no remote, leave the branch local and print a clear "switch to this branch and review" message.

10. **Stamp the marker.** Add or update at the top of the project CLAUDE.md (post-rewrite, on the cleanup branch):
    ```
    <!-- last-decomposed: <sha> @ <iso-date> @ tier <N> → see .claude/claude-md-doctor/<ts>/ -->
    ```
    Next run reads this marker + `git log <sha>..HEAD` to scale effort.

11. **Terminate fast.** No in-session waiting on user input. The interactivity model is async:
    - PR merged → approved.
    - PR closed unmerged → rejected.
    - Branch dangling → user hasn't looked yet.

## Decompose-prompt (the technique this skill applies)

See `@./references/decompose-prompt.md` for the full method. In brief:

1. Read the target as specification.
2. Name each commitment as a short English phrase.
3. Cite the units it was extracted from.
4. Sort into **PRESENT** / **MISSING** / **DROP-CANDIDATES** (with reasons).
5. Note clusters, tensions, mutual dependencies.
6. Briefly name an alternative decomposition if one is similarly plausible.
7. Flag self-reference — guardrails that may be summoning the failure mode they were written to prevent.

Stance: reason from the target. The output is a **map**, not a redraft. Spending tokens externalizing the model's intermediate reasoning matters for **intervenability, not capability**.

## Anti-Patterns

- Producing a redraft before the map is written.
- Adding rules to plug a gap without N≥2 evidence.
- Editing inside `<!-- locked -->` markers.
- Quoting chat content or personal data into the artifact bundle.
- Asking the user a synchronous question. (That's Variant A's job. This variant ships a PR.)
- Skipping triage and jumping straight to a decompose pass.

## Argument Parsing

| Token | Effect |
|-------|--------|
| `tier:0\|1\|2\|3` | Force tier; triage still runs |
| `scope:project\|user\|rules\|all` | Restrict targets (default: project — `./CLAUDE.md`, `./AGENTS.md`, `./.claude/rules/`) |
| `dry-run` | Write the bundle but don't create a branch or PR |

## Dogfooding

This skill targets the kind of file CLAUDE.md is for this very repo. Run it on `./CLAUDE.md` to dogfood. If you find that the skill produces a bad map or a bad rewrite on a file you understand, that's a bug — file a GitHub Issue tagged `engineering-loop` + `tech-debt` and fix.
