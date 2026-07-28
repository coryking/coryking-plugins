# Bundle skeleton

This is the shape only — use it as a structural reference, not a fill-in form. Every bundle file leads with summary info because tools truncate from the bottom.

## `triage.md` skeleton

```
# Triage — <repo-name> @ <head-sha-short>

**Summary:** <N> files, <X> total auto-loaded lines, <Y> Tier-0, <Z> need attention.
**Tier picked:** <0|1|2|3>.   **Time-to-here:** <approx>.

| Path | Lines | Tier | Smells | Last touched | Since marker | Verdict |
|------|------:|-----:|--------|--------------|--------------|---------|
| ...  |       |      |        |              |              |         |

## Smells legend
- `fence>15` — code fence over 15 inner lines (skill-shaped)
- `bullets>12` — heading with too many bullets (fragment)
- `dup-h2` — H2 name appears in another file
- `temporal` — chronological phrasing
- `no-paths` — rule file missing `paths:`
- `index-dump` — MEMORY.md entries carrying content instead of hooks
- `over-cap` — MEMORY.md past 200 lines / 25KB; the tail never loads
- `inert` — memory-shaped file outside the resolved memory directory
```

The memory scope gets one row for `MEMORY.md` and one summary row for the topic files —
never a row per topic file. Detail lives in `memory-report.md`.

## `decompose.md` skeleton (one section per file)

```
# Decompose

## <relative path>  (tier <N>, <lines> lines)

### PRESENT
- **<English label>** — <one-line description> — citations: [§ <H2>, ¶<n>]; (verifiable: yes/partial/no — <reason>)
  - cluster: <…> | tension with: <…>

### MISSING
- **<English label>** — <why framing calls for it> — N≥2 evidence: <session-id @ date>, <session-id @ date>

### DROP CANDIDATES
- **<English label>** — <why flagged: scar | wrong-mechanism (<which>) | ADR | discoverable> — citations: [§ <H2>] — evidence: <git-untouched-since | chat-hits=<N>>

### ALTERNATIVE READING (optional)
<a materially different decomposition in one paragraph>

### QUESTIONS
- <…>
```

## `proposed-changes.md` skeleton

```
# Proposed changes — actionable checklist

**Read this top-to-bottom on your phone. Each item is independently applicable.**
**Total moves:** <N> (<A> auto, <B> ask).   **Estimated effort:** <complexity tag, not time>.

- [ ] **1.** `auto` — <verb> <unit-name> from <source> → <target>
- [ ] **2.** `ask` — ...

---

## Why narratives

### 1. <verb> <unit-name> …
- **Apply:** auto | ask — <one clause naming the gating criterion from the heuristics table>
- **Why:** <2 sentences, behavioral>
- **How:** <one-line action>
- **Evidence:** <citations, incl. truth-check verdicts>

### 2. …
```

After Stage 5, applied moves are checked off in place (`- [x]`); `ask` moves stay unchecked.

## `memory-report.md` skeleton

Cite personal facts by file and line. Do not quote them into the bundle.

```
# Memory — <resolved path>

**Index:** MEMORY.md <L> lines / <K>KB loaded (cap 200 / 25KB) — <under cap | CUT AT: "<first entry past the cut>">.
**Topic files:** <N> (<I> indexed, <U> unindexed, <F> without frontmatter). **team/:** <N or absent>.
**Snapshot:** <path or "not taken — reason">.
**Inert (loads into nothing):** <path — N lines> · ... | none

## Index contract
| Entry | Breach | Move |
|-------|--------|------|

## Cross-scope findings
### Duplication (memory ↔ instruction surface)
- **<memory file>** ↔ **<instruction file § H2>** — <verbatim | restated> — move <N>

### Contradictions
- **<a>** vs **<b>** — <the tension in one line> — QUESTIONS, not a move

### Routing candidates
- **<file>** → <mechanism> — <which row of the routing table> — move <N>

## Truth check
| File | modified | Claim checked | Verdict |
|------|----------|---------------|---------|
```

## `user-global-proposals.md` skeleton

Same shape as `proposed-changes.md`, prefixed with:

```
> This skill cannot edit files under `~/.claude/`. Apply manually, via chezmoi if managed.
```

## `metadata.json` skeleton

```json
{
  "skill_version": "<from plugin.json>",
  "repo_sha": "<head>",
  "branch": "<name>",
  "mode": "apply | diagnose | diagnose (dirty tree)",
  "started_at": "<ISO>",
  "finished_at": "<ISO>",
  "tier_by_file": { "<path>": <tier> },
  "files_analyzed": ["<path>", "..."],
  "marker_sha": "<head-sha>"
}
```

## `execution-report.md` skeleton (apply runs only)

```
# Execution report

**Summary:** <A>/<N> moves applied, <B> left as `ask`. <path>: <before> → <after> lines. Validation: <PASS | issues below>.
**Footprint:** nothing committed; edits + this bundle + new files are one dirty working tree. New files: <list or none>.
**Outside git:** memory files changed <list or none>, deleted <list or none>. Restore: `cp -r <snapshot>/. <memory-dir>/`

## Applied
- **1.** <unit-name> — <one line: what changed>
- ...

## Left for you (`ask`)
- **N.** <unit-name> — <the judgment only you can make, one line>

## Validation
- Smells re-scan: <clean | surviving smell + explanation>
- Links + @imports: <all resolve | broken: …>
- Locked units untouched: <yes>
- `ask` moves untouched: <yes>
- MEMORY.md under cap after edits: <L lines / KKB | n/a>
- Index entries all resolve: <yes | dangling: …>
- `team/` untouched, no topic-file body rewritten: <yes | n/a>

## Regrades
<moves that started `auto` but hit un-mapped judgment mid-apply — what stopped them. "none" if none.>

---
**Calibration:** <one honest line: did any applied move require judgment the bundle hadn't already made?>
```
