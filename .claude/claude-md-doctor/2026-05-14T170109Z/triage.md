# Triage — coryking-plugins/CLAUDE.md @ wet-run 2026-05-14T170109Z

## Scope
- `./CLAUDE.md` — 217 lines (over 200-line target by 17).
- `./AGENTS.md` — not present.
- `./.claude/rules/` — not present.
- `~/.claude/CLAUDE.md` — out of scope this run (scope=project).

## Marker
No `<!-- last-decomposed -->` marker present. First-run posture: assume Tier 2 unless triage downgrades.

## Repo posture
- Remote: `https://github.com/coryking/coryking-plugins.git`.
- LICENSE present (MIT-style — public repo).
- **Public-repo guardrail applies.** No chat content, session UUIDs, or personal data in the bundle or PR body.

## Mechanical-fix candidates (Tier 1 territory if alone)
- `.claude/` already in `.gitignore`, so `.claude/claude-md-doctor/` is covered transitively. No gitignore edit needed.
- The file has zero HTML maintainer comments. Not a problem by itself, but a stamp slot is missing — will add the `<!-- last-decomposed -->` marker as part of the Tier-2 rewrite.

## Drift signals
- File grew an entry per design decision over the project's history (commits `66f6a1a`, `3edc47e`, `4de4bbc`, `fc72664`, `2093179`, `d96be83`). Each entry was correct at the time; cumulatively, the "Design Decisions" section is now ~70 lines and dominates the file.
- "Plugins" section duplicates structural information that already lives in each plugin's own `plugin.json`, `NOTICE`, and skill `SKILL.md`. Pattern: this CLAUDE.md is being used as a plugin index, not as session-shaping guidance.
- "Repo structure" ASCII tree (L42-76) is the kind of map that goes stale within a few commits — this very run had to update it.

## Mechanism misfits to investigate
- "Design Decisions" subsections (L150-217) are essentially architectural-decision-record content. They shape future *editing* decisions but aren't always-on session guidance. Candidate to move to `docs/` (already exists per L75) or to per-plugin NOTICE/AGENTS.
- The repo-structure ASCII tree could move to a maintainer-comment block or to a `docs/repo-structure.md` referenced by HTML comment.

## Verifiability spot-check
- L9-13 "Engineering ownership" — narrative principles. Mostly vibes; some are concrete ("read existing models before adding functionality"). Mixed.
- L17-24 GitHub workflow — concretely verifiable (commands, label names).
- L28 "Dogfooding cc-explorer" — concretely verifiable (use MCP tools, file fixes).
- L32-36 Principles — five bullets. "Summary info at the top of output" is verifiable. "Skills are self-contained" is verifiable. "Wait for three variants" is a meta-rule about future edits — verifiable in PR review. "One authoritative source" is verifiable. "Toolbox not a product" is a stance, less directly verifiable.

## Tier decision
**Tier 2.** File over budget, no prior decomposition, multiple unrelated concerns (session guidance + plugin index + ADRs), mechanism-misfit signal on the Design Decisions section. Full decompose pass + rewrite + PR.

## Estimated outcome shape
- Net mass change: -30 to -50 lines from CLAUDE.md.
- New file: `docs/design-decisions.md` (move existing Design Decisions section there; CLAUDE.md keeps a one-line pointer). The `docs/` directory is already referenced as existing per the repo-structure tree.
- New file: `.claude/rules/repo-conventions.md` likely **not** needed — the repo doesn't have a `paths:` boundary where these conventions stop applying. CLAUDE.md is the right home for the session-shaping content; the cleanup is removing the index/ADR content, not splitting the session-shaping content.

## Time-to-Tier-0 note
Triage took well under 10 seconds of model wall-clock once the file was read. Tier-0 baseline assumption holds.
