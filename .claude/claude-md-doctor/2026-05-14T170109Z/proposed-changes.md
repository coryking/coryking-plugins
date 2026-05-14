# Proposed CLAUDE.md cleanup — tier 2 (claude-md-doctor wet-run)

## Summary

First-ever `claude-md-doctor` run on this repo. The root `CLAUDE.md` had grown to 217 lines and was doing three jobs at once: session-shaping behavioral guidance, plugin index, and architectural decision record. This PR keeps job (1) in place, deletes job (2) (which duplicated per-plugin metadata anyway), and moves job (3) to a new `docs/design-decisions.md`. Net change to the auto-loaded surface: **217 → 56 lines (−161)**.

## Diff at a glance

| File | Before | After | Delta |
|------|--------|-------|-------|
| `CLAUDE.md` | 217 | 56 | −161 |
| `docs/design-decisions.md` | 0 (new) | 78 | +78 (not auto-loaded) |

Auto-loaded context shrinks by 161 lines per session.

## What changed and why

### Reroutes

- **Design Decisions section (was L150-213) → `docs/design-decisions.md`.** Five subsections of ADR-class historical reasoning. Useful when editing a related area; pure context tax when auto-loaded every session. The new file is opened on demand.
- **Plugin catalog (was L108-148) → one-line pointers.** The pre-rewrite file enumerated each plugin's components in detail — exactly the information already present in each plugin's `plugin.json` and `NOTICE`. The "one authoritative source" principle (which this file already states) cuts against duplicating it. Now reduced to three one-line orientations that point readers at the per-plugin metadata.
- **Repo-structure ASCII tree (was L42-76) → deleted.** Index content that goes stale every commit; this very run had to update it. Readers who want the layout can `ls` from root or open `.claude-plugin/marketplace.json`.
- **MCP server architecture + plugin internals sections (was L78-106) → deleted.** Wiring details belong in `project-mining/` (where the wiring actually lives); plugin-internals reference content is in the official Anthropic skills docs.

### Adds

- **"CLAUDE.md hygiene" principle** (new in the Principles section). Codifies the cleanup criterion: this file is for session-shaping behavioral guidance only; structural information goes to plugin metadata, ADR-class reasoning goes to `docs/design-decisions.md`, scoped rules go to `.claude/rules/<topic>.md` with `paths:`. When the file exceeds ~200 lines, run `claude-md-doctor`.

  **Evidence tier: hypothesis (N=1).** This is the first observed instance of CLAUDE.md bloat on this repo. The rule will earn full status if/when a second instance of the same drift pattern occurs. Documented in `metadata.json`.

- **"When to run claude-md-doctor" section.** Self-referential but earned — the skill exists in `engineering-loop/`, the `<!-- last-decomposed -->` marker is now stamped, and the section gives the session a concrete trigger condition.

### Drops

None. Every commitment in the pre-rewrite file was either kept in CLAUDE.md or relocated. No content was deleted outright.

## Verifiability

Every retained rule in the rewritten CLAUDE.md passes the concrete-verifiability test:

- "Use cc-explorer MCP tools, don't shell out" — verifiable in tool calls.
- "Public repo — never include conversation content in artifacts" — verifiable in diffs.
- "Summary info at the top of output" — verifiable in tool output structure.
- "Skills are self-contained" — verifiable in skill file references.
- "Wait for three variants before abstracting" — verifiable in PR review.
- "One authoritative source" — verifiable: there should be exactly one place documenting each thing.
- "Toolbox not a product — prefer clean rewrites" — stance; verifiable in change patterns over time.
- "CLAUDE.md hygiene" — verifiable (`wc -l CLAUDE.md`, presence of `<!-- last-decomposed -->`).
- "When to run claude-md-doctor" — verifiable from marker date.

The "Engineering ownership" section is partly stance and partly concrete ("read existing models before adding functionality" — verifiable in code-review). Kept as-is; it's the highest-value content in the file.

## Calibration

**Over-claims:** None added. The CLAUDE.md hygiene rule is tagged hypothesis-tier; it asserts itself as a process commitment but acknowledges single-instance evidence.

**Under-claims:** Two candidates the rewrite did not add:
- A concrete commitment about *when* per-plugin `AGENTS.md` files should exist vs not. The repo has zero `AGENTS.md` files today; adding a rule before an actual need exists would be defensive. Defer to next pass if/when a plugin grows one.
- A rule about which `.claude/rules/` files exist at the root level. Today there are none; rule unnecessary. Defer.

## Self-reference flag (informational)

Both "Skills are self-contained" and "One authoritative source" were present in the pre-rewrite file and cut against the duplication the same file contained. The cleanup honors those rules retroactively. The decompose pass surfaced this contradiction explicitly (see `decompose.md` § Self-reference flags).

## Locked sections

None present in the pre-rewrite file. Future runs that introduce `<!-- locked -->` markers will be honored verbatim.

## Marker stamp

The rewritten `CLAUDE.md` opens with:

```html
<!-- last-decomposed: d96be83 @ 2026-05-14 @ tier 2 → see .claude/claude-md-doctor/2026-05-14T170109Z/ -->
```

Next run will read this and scale effort to interval churn.

## How to review

- **Merge** → approved; next session auto-loads the slimmer file.
- **Close without merge** → rejected; no harm done. The bundle stays on the branch for receipts.
- **Partial concerns** → comment on the PR or open a follow-up issue. The bundle directory (`.claude/claude-md-doctor/2026-05-14T170109Z/`) is gitignored locally but committed on this branch, so reviewers can see the decompose map alongside the diff.

## Public-repo guardrail check

- No chat content, session UUIDs, personal data, or export files appear in the bundle or this PR body.
- Evidence is cited by shape only (line ranges, commit shas of public commits, file paths).
