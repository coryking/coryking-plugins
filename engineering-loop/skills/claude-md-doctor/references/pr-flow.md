# PR Flow — Variant B's distinctive mechanism

The "PR-as-dialogue" idea: instead of stopping mid-session to AskUserQuestion, the skill commits its proposal to a branch and (when possible) opens a PR. The user reviews async — on their phone, between meetings, whenever. Merge / close / ignore are the three signals.

## Step-by-step (Tier 2+)

1. **Stash any uncommitted work.** If the working tree isn't clean, `git stash push -u -m "claude-md-doctor: pre-run stash"`. Restore after creating the branch.

2. **Create the branch off current HEAD.**
   ```bash
   git checkout -b claude-md-doctor/<timestamp>
   ```
   Timestamp matches the bundle directory.

3. **Commit the artifact bundle first** (separate commit — preserves the analysis even if the rewrites get amended later).
   ```bash
   git add .claude/claude-md-doctor/<timestamp>/
   git commit -m "claude-md-doctor: bundle for tier-<N> run <timestamp>"
   ```

4. **Commit the rewrites.** Each rewritten file (`CLAUDE.md`, `AGENTS.md`, any rules files) gets the new content. The marker line is part of this commit.
   ```bash
   git add CLAUDE.md AGENTS.md .claude/rules/
   git commit -m "claude-md-doctor: tier-<N> rewrite — <one-line summary>"
   ```

5. **Push and open PR (if remote available).**
   ```bash
   git push -u origin claude-md-doctor/<timestamp>
   gh pr create --title "claude-md-doctor (tier <N>): <one-line scope>" --body-file .claude/claude-md-doctor/<timestamp>/proposed-changes.md
   ```

   The PR body **is** `proposed-changes.md`. That file must be written so it stands alone as a PR description — context-free for the reviewer.

6. **If `gh` is missing or push fails:** print clearly:
   ```
   claude-md-doctor: branch ready locally — claude-md-doctor/<timestamp>
   Switch to it with:  git switch claude-md-doctor/<timestamp>
   Bundle:             .claude/claude-md-doctor/<timestamp>/
   ```

7. **Return to the original branch.** `git switch -` (or the captured branch name from `metadata.json`). Pop the stash if one was made.

## PR title conventions

- `claude-md-doctor (tier 2): trim CLAUDE.md, move GitHub workflow to rules/`
- `claude-md-doctor (tier 3): 4 drops, 1 add, mark locked sections stale`
- `claude-md-doctor (tier 1): mechanical fixes (gitignore, broken refs)` — only if you decided to PR a tier-1 instead of inlining.

Keep titles under 70 chars. The body carries the detail.

## `proposed-changes.md` body shape

```markdown
# Proposed CLAUDE.md cleanup — tier <N>

## Summary
<2-3 sentences: scope, net mass change, headline insight>

## Diff at a glance
- `CLAUDE.md`: 210 → 168 lines (−42)
- `.claude/rules/github-workflow.md`: new (28 lines, `paths:` scoped)
- `AGENTS.md`: untouched

## What changed and why
### Drops
- `<phrase>` — reason: scar tissue, no recurrence in last 30 commits.
- `<phrase>` — reason: off-key (belongs in settings.json, not behavioral guidance).

### Reroutes
- "GitHub workflow" section → `.claude/rules/github-workflow.md` with `paths: ['.github/**', 'CHANGELOG.md']`. Saves ~30 lines from every-session load.

### Adds
- `<phrase>` — N=3 commits exhibit this pattern (without quoting commit messages here; see bundle for shape).

## Verifiability
Every retained rule passes the concrete-verifiability test. Notable: <any rule that was a judgement call>.

## Calibration
Over-claims: <list any rules the rewrite added that the project doesn't yet actually follow>.
Under-claims: <obvious commitments the file still omits — defer to next pass>.

## Locked sections
Honored: <list lock anchors>. None edited. <If tier 3:> Stale-flag notes follow.

## How to review
- Merge → approved, harness will use the new files next session.
- Close → rejected, no harm done. Bundle stays on the branch for reference.
- Comment with concerns → open a follow-up issue; the bundle is gitignored locally but committed on this branch for receipts.
```

## Async signal interpretation (for documentation)

- **PR merged.** User approved. Skill considers this a positive eval data point.
- **PR closed without merge.** User rejected. The decompose map and `proposed-changes.md` are still on the branch; they're the post-mortem material.
- **PR sits open for a long time.** User hasn't gotten to it. Skill should not nag. The next run reads `<!-- last-decomposed -->` from the **merged** state, not the open PR, so an unmerged PR doesn't suppress the next run.

## Safety

- Never force-push.
- Never run destructive git commands (no `reset --hard`, no `clean -f`).
- Never amend an existing commit on the cleanup branch — make new ones.
- If the working tree had uncommitted work and the stash-pop conflicts, **stop and report**. Don't auto-resolve.
