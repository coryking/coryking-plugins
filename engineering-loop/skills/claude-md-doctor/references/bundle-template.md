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
```

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
**Undo:** `git stash -u` shelves everything (edits + bundle + new files) · `git checkout -- . && git clean -fd` discards everything INCLUDING this bundle. New files: <list or none>.

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

## Regrades
<moves that started `auto` but hit un-mapped judgment mid-apply — what stopped them. "none" if none.>

---
**Calibration:** <one honest line: did any applied move require judgment the bundle hadn't already made?>
```
