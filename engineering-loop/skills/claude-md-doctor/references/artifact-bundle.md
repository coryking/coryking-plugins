# Artifact Bundle Shape

Every run writes a bundle to `.claude/claude-md-doctor/<timestamp>/`. Timestamp format: `YYYY-MM-DDTHHMMSSZ` (UTC, filesystem-safe).

## Files

| File | Tier | Purpose |
|------|------|---------|
| `metadata.json` | all | Run metadata (see schema below) |
| `triage.md` | all | Quick scan results, line counts, tier decision, evidence pointers |
| `decompose.md` | 2+ | PRESENT / MISSING / DROP-CANDIDATES map per target file |
| `proposed-changes.md` | 2+ | The "why" narrative — what changed, why, what evidence supported each change. **This is the PR body.** |
| `pre/<filename>` | 2+ | Pre-rewrite snapshot of each target file |
| `post/<filename>` | 2+ | Post-rewrite snapshot of each target file |

Tier 0 writes only `metadata.json` + `triage.md`. Tier 1 may write a tiny `proposed-changes.md` summarising the mechanical fixes.

## `metadata.json` schema

```json
{
  "run_id": "2026-05-14T093000Z",
  "tier": 2,
  "branch_at_start": "main",
  "head_sha_at_start": "d96be83",
  "targets": [
    "CLAUDE.md",
    "AGENTS.md",
    ".claude/rules/azure-postgres.md"
  ],
  "scope": "project",
  "repo_public": true,
  "previous_marker": {
    "sha": "abc1234",
    "date": "2026-04-12",
    "tier": 1
  },
  "outcomes": {
    "branch_created": "claude-md-doctor/2026-05-14T093000Z",
    "pr_url": "https://github.com/coryking/coryking-plugins/pull/42",
    "lines_before": 210,
    "lines_after": 168,
    "rules_added": 1,
    "rules_dropped": 4,
    "rules_rerouted": 2
  }
}
```

`outcomes.pr_url` is null if no PR was opened (no remote, gh missing, or dry-run).

## Public-repo guardrail

**Never** write to the artifact bundle:

- Chat content or transcript excerpts.
- Session UUIDs.
- Personal data (emails, paths under `~/`, machine names).
- Export files from cc-explorer or other mining tools.

Refer to chat evidence by shape and count only: *"4 commits in the last 30 commits touched this rule"*, *"chat-history shows the workflow recurring across 3 sessions"*. Not the contents.

## Gitignore

The bundle directory should be gitignored. The skill's first run on a repo where `.claude/claude-md-doctor/` is *not* gitignored should add the entry as a Tier 1 mechanical fix.

```
.claude/claude-md-doctor/
```

## The `<!-- last-decomposed -->` marker

Stamped at the top of the project CLAUDE.md on the cleanup branch, after rewrite:

```html
<!-- last-decomposed: <head-sha-at-start> @ <iso-date> @ tier <N> → see .claude/claude-md-doctor/<ts>/ -->
```

Next run reads this and `git log <sha>..HEAD --oneline -- CLAUDE.md AGENTS.md .claude/rules/` to gauge churn and scale effort. No marker = first run; treat as Tier 2 unless triage says otherwise.
