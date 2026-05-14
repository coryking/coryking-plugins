# Variant B: PR-as-dialogue

This worktree ships Variant B of the `claude-md-doctor` bake-off. The four variants share the same goal (clean up auto-loaded instruction surfaces) and the same core technique (decompose-prompt). They differ in **how the user interacts with the proposal**.

## The variant's distinctive choice

**Fire-and-forget from the user's perspective.** No AskUserQuestion gates. The skill:

1. Runs triage.
2. If Tier 2+, does the full decompose pass and proposes rewrites.
3. Creates a new branch `claude-md-doctor/<timestamp>`.
4. Commits the artifact bundle (separate commit) and the rewrites.
5. If a remote exists and `gh` is available, opens a PR with `proposed-changes.md` as the body.
6. Returns to the original branch and exits.

The user reviews **async** — merge / close / ignore. Borrowed verbatim from Cory's Mozicode dream-routine framing: *"PRs are slow-rolling dialogue, not bureaucracy. The lightest channel the dream has for anything outside the workspace sandbox. He reads them on his phone."*

## Why this might beat sync

- **Latency vs. attention.** The skill should be runnable from a hook or a low-stakes "uh, the CLAUDE.md is bugging me" mutter. Sync gating turns every run into a context-switch tax. Async lets the run complete while the user keeps doing the thing they were actually doing.
- **Review surface.** A PR diff is the right shape for reviewing a CLAUDE.md rewrite. It's the artifact GitHub already optimizes for; phones already render it well; the bundle is committed alongside the diff so the "why" is one click away.
- **Failure is cheap.** Closed PR ≈ "no thanks." No mid-session friction, no stranded conversation state, no half-applied rewrite.

## Why this might lose to sync

- **Discoverability.** If the user doesn't notice the branch, the work sits forever. Variant A's AskUserQuestion forces a yes/no in-session.
- **Tight loops.** If the user wanted to iterate on the proposal ("no, drop that one, keep this one"), async PR review is slower than an in-session refine loop.
- **Multi-machine workflows.** If the user runs Claude Code on a machine without a GitHub remote configured, the fallback (local branch) leans on them remembering it exists.

## Cutoffs documented

- **Tier 0** — no branch, no PR, no commits. Just a triage report printed to stdout. Bundle written to `.claude/claude-md-doctor/<ts>/` for receipts.
- **Tier 1** — mechanical fixes (gitignore the bundle dir, fix a stray HTML comment, dedupe an exact-duplicate rule). Two strategies depending on judgement:
  - **Inline commit on the current branch** if the fixes are 1-3 lines and obviously correct. No PR.
  - **PR** if there's more than one mechanical fix and the user might want to see them. Choose conservatively; bias toward PR when in doubt.
- **Tier 2** — full decompose + rewrites + PR.
- **Tier 3** — Tier 2 plus locked-section staleness flagging. Same PR mechanism; the body has a `needs-cory` callout.

## What the bake-off should compare

- **No-wtf rate.** Does the user accept the PR wholesale, partially, or close it?
- **Time-to-Tier-0.** All variants should hit < 10 s on a healthy repo. Worth verifying B's overhead (branch creation, optional push) isn't worse than A's.
- **Drift recovery.** After running each variant N times on the same repo, which produced the cleanest final state?

## Files in this variant

- `SKILL.md` — orchestrator instructions, under 200 lines, references inlined via `@./references/<name>`.
- `references/decompose-prompt.md` — full decompose method.
- `references/mechanism-routing.md` — the "right mechanism" cheat-sheet.
- `references/artifact-bundle.md` — bundle shape, metadata schema, gitignore expectations.
- `references/pr-flow.md` — branch creation, commit ordering, PR body shape, fallback when `gh` is missing.
- `VARIANT.md` — this file.
