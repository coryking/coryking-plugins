# Triage — 2026-05-14T18-17-07Z

## Verdict: Tier 2

Project `./CLAUDE.md` is over the 200-line budget and carries substantial ADR-history
content explicitly prohibited by user-global rules. Targeted decompose + rewrite warranted.
No deep mining needed — smells are visible from a single read.

## File-by-file

| File | Lines | Target | Smells | Action |
|---|---:|---:|---|---|
| `./CLAUDE.md` | 210 | 200 | ADR history, chronological language, `ls`-able tree, inventory bullets, MCP-config duplication | Decompose + rewrite |
| `./docs/CLAUDE.md` | 16 | 200 | None — focused ToC | Leave alone |
| `~/.claude/CLAUDE.md` | 53 | 200 | Out of worktree scope | Proposals only (none surfaced) |
| `~/.claude/rules/azure-postgres.md` | 32 | — | Out of worktree scope | Proposals only (none surfaced) |
| `~/CLAUDE.md` (home dir) | 28 | — | Different project (home dir) | Skip |

## HTML marker
Absent on `./CLAUDE.md` line 1. First doctor run.

## git activity (last 60d on instruction files)
Active — 7+ commits touch CLAUDE.md / docs/CLAUDE.md / .claude/ since fc72664.
Not stale. No reason to bias toward Tier 0.

## Existing cleanup branches
None.

## Repo visibility
PUBLIC. Bundle dir under `.claude/` already gitignored — no `.gitignore` change needed.

## Smell sweep on ./CLAUDE.md

1. **ADR history with chronological language** (L145–208, "Design Decisions" section).
   User-global CLAUDE.md L24 explicitly forbids chronological phrasing in documentation
   ("during initial development", "we later hit", "started as a raw brain dump",
   "we reorganized it"). 64 lines of this. Routes to `docs/decisions/` or trust `git log`.

2. **`ls`-able directory tree** (L42–71). Right-mechanism table flags as "discoverable junk".
   30 lines.

3. **MCP server wiring restatement** (L73–92). The `.mcp.json` body is duplicated from
   `project-mining/.mcp.json`. ~20 lines. Single-authoritative-source violation.

4. **Plugin component inventories** (L111–119, L129–139). One-line-per-file bullet lists
   describing what each file in a plugin holds. Discoverable by `ls`. ~25 lines.

5. **Engineering-loop fork-deltas bullets** (L204–208) duplicate `engineering-loop/NOTICE`.

6. **Trailing pointer** (L210) duplicates GitHub workflow guidance from L20.

## Tier rationale
- Not Tier 0: 210 > 200 and clear smells present.
- Not Tier 1: cuts require category judgments (what's ADR history? what's durable
  principle? what's inventory?). Decompose pass earns its tokens.
- Tier 2 (not Tier 3): only one file is meaningfully bloated. Broader mining not
  needed — the smells are surface-readable.
