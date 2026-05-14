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
**Total moves:** <N>.   **Estimated effort:** <complexity tag, not time>.

- [ ] **1.** <verb> <unit-name> from <source> → <target>
- [ ] **2.** ...

---

## Why narratives

### 1. <verb> <unit-name> …
- **Why:** <2 sentences, behavioral>
- **How:** <one-line action>
- **Evidence:** <citations>

### 2. …
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
  "started_at": "<ISO>",
  "finished_at": "<ISO>",
  "tier_by_file": { "<path>": <tier> },
  "files_analyzed": ["<path>", "..."],
  "marker_sha": "<head-sha>"
}
```

## `design-flaws.md` skeleton

```
# Design flaws / temptations log

<one entry per moment you wanted to do more than diagnose>

## <short-tag>
- **Wanted to:** <…>
- **Blocked by:** diagnose-only constraint
- **Bundle sufficient?** <yes / no — <why>>

---
**Calibration:** <one-line honest answer to: did the map alone feel like enough?>
```
