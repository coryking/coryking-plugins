## Summary

**Tier 2** cleanup of `./CLAUDE.md`. **Files touched: 1.** Net line delta: **-141 (210 → 69)**. Conservation of mass: net **shrink** — no new auto-loaded surface added.

Cuts target three categories that the skill's right-mechanism table calls out by name: the `ls`-able directory tree (discoverable junk), the inlined MCP wiring (duplicates `project-mining/.mcp.json`), and the entire Design Decisions section (ADR history with chronological language — banned by user-global rule).

HTML marker added at line 1 so the next doctor run can short-circuit if nothing has changed.

## Why

Triage flagged the project CLAUDE.md at 210 lines, over the 200-line budget, with smells visible from a single read:

- ~30 lines of ASCII directory tree that `ls -R` reproduces and that drifts every time a file is added.
- ~20 lines restating `project-mining/.mcp.json` verbatim.
- ~25 lines of one-line-per-file plugin inventories that carry no information beyond the filenames.
- ~65 lines of "Design Decisions" narrative using exactly the chronological language `~/.claude/CLAUDE.md` forbids ("During initial development", "We later hit", "started as a raw brain dump", "We reorganized it").

The principles, ownership stance, GitHub workflow, dogfooding rule, plugin infrastructure facts, and high-level plugin descriptions are all preserved. What's gone is decoration, duplication, and history that `git log` already carries.

## Per-file changes

### `./CLAUDE.md` — 210 → 69 lines

**Preserved** (load-bearing for a session opening this repo cold):
- Repo purpose & audience framing
- Engineering ownership (root-cause, push-back, evolve upstream types)
- GitHub Issues workflow (start-check, file-as-you-see, foundational-issue scan)
- cc-explorer dogfooding rule
- All five principles (summary-at-top, skills-self-contained, three-variants, one-authoritative-source, toolbox-not-product)
- Plugin infrastructure facts (`{baseDir}`, `${CLAUDE_PLUGIN_ROOT}`, frontmatter shapes, the `el:` colon-in-name trick)
- Plugin one-paragraph descriptions (project-mining, engineering-loop, mcp-authoring)
- Pointer to `engineering-loop/NOTICE` for fork provenance
- Pointer to `docs/CLAUDE.md` for design references
- New: HTML marker line 1

**Dropped:**
- `ls`-able directory tree (right-mechanism: discoverable). Evidence: covered by `ls`, `cat .claude-plugin/marketplace.json`, frontmatter reads.
- MCP wiring block (right-mechanism: one authoritative source — the actual `.mcp.json`).
- Per-plugin "Components" bullet lists. Evidence: `ls <plugin>/agents/`, frontmatter reads.
- "Output: user-confirmed during alignment" — operational, belongs in the skill.
- Entire "Design Decisions" section (architecture-astronaut narrative, plugin-conversion history, IDE-mining-framework history, cc-explorer-vs-strip_chat.py evolution, `agent_content` parameter notes, engineering-loop fork-deltas detail). Evidence: chronological language forbidden by user-global rule; right-mechanism routes ADR content to `docs/decisions/` or `git log`; the durable principle from each narrative is already preserved elsewhere (three-variants rule in Principles; el:review naming trick in Plugin infrastructure; fork ledger in `engineering-loop/NOTICE`; superseded code already gone from the tree).
- Trailing "see GitHub Issues for refactor list" — duplicates the GitHub workflow section.

**Added:**
- One paragraph for `mcp-authoring` (it was previously mentioned only in the tree, never described).
- Reference-material closing section pointing to `docs/` and `engineering-loop/NOTICE`.

Both small additions are offset many times over by the cuts.

## Hypothesis-only proposals (N=1 evidence)

None. No new rules proposed under the N≥2 gate — this pass is purely subtractive plus reorganization of existing content.

## Right-mechanism moves

| Smell | Route taken |
|---|---|
| `ls`-able directory tree | **Deleted.** Discoverable via `ls`. |
| Inlined MCP wiring | **Deleted.** `project-mining/.mcp.json` is the single source. |
| ADR narratives (architecture astronaut, plugin conversion, IDE mining, cc-explorer evolution, `agent_content` design) | **Trust `git log`.** No content moved to `docs/decisions/` — superseded code is already gone, and the durable principles each narrative encoded survive in Principles or the Plugin infrastructure section. |
| Engineering-loop fork-deltas bullets | **Pointer to `engineering-loop/NOTICE`** (where the slimming ledger already lives). |
| Maintainer note about cleanup-branch state | **HTML comment marker** at line 1. |

No new hooks proposed. No `settings.json` changes proposed. No `.claude/rules/` files created — there are no path-specific rules in the current content.

## Open questions

1. **Should the dropped Design Decisions content be preserved in `docs/decisions/<date>-<slug>.md` before deletion, or is `git log` retrieval sufficient?** I defaulted to `git log` on the grounds that (a) the superseded code each narrative described is already gone, (b) the durable principles each one encoded survive in the slimmed file, and (c) ADR files have their own maintenance overhead. If you want any of those narratives preserved as a navigable doc — particularly the "architecture astronaut" piece, which reads as a recurring engineering principle rather than a one-time decision — say so in a comment and I'll extract it into `docs/decisions/` on a follow-up commit.

2. **`mcp-authoring` description.** I wrote a one-paragraph blurb to bring it to parity with the other two plugins. If you'd rather drop the mention entirely (it has no skill the orchestrator dispatches automatically), say so.

3. **Marker SHA.** I used `f0b28b3` (the decompose commit) as the marker's commit reference. The skill leaves this ambiguous; the alternative is to amend after this commit lands. Not worth amending unless you want exactness.

## Bundle

`.claude/claude-md-doctor/2026-05-14T18-17-07Z/` (gitignored by the existing `.claude/` rule; force-added in the decompose commit so the bundle ships with this branch).

- `triage.md` — verdict + smell sweep
- `decompose.md` — PRESENT / MISSING / DROP map with line-number citations
- `pre/project-CLAUDE.md` — snapshot before
- `post/project-CLAUDE.md` — snapshot after (this commit)

## Review mechanics

- **Merge = approved.** Marker lands in `main`, next run sees it.
- **Close = rejected.** Marker never lands; next run starts fresh.
- **Dangling = haven't looked.** The skill will detect the open cleanup branch on its next run and warn instead of stacking work.
