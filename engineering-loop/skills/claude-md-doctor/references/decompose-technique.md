# Decompose-prompt technique (verbatim)

This is the source-of-truth methodology for Variant D. Treat the `<task>` block as your operating instructions when running the decompose pass on each non-Tier-0 file.

```
<task>
Read the target prompt and surface its underlying conceptual basis in plain English, organized so I can decide what to keep, add, and cut. Do not rewrite the prompt. This is a working document for me, not a redraft.
</task>

<what_this_does>
I'm iterating on a prompt and I want to see its structure before I edit it. Extract the implicit commitments as named concepts I can read and argue with. The labels are handles for my own thinking — they need to be legible in English, not compressed or clever.
</what_this_does>

<optional_framing>
If a framing is provided below in <framing>, use it as the vantage for the "missing" and "drop" buckets. The framing describes who I want the prompt to serve, which is not necessarily the author it currently reflects.
</optional_framing>

<method>
1. Read the target as specification. Every unit is there for a reason — identify what it tries to produce, prevent, or express.
2. Name each commitment as a short English phrase. Plain language. Foreign terms only when they capture the concept substantially better; parenthetical gloss; English primary label.
3. For each label, cite the units in the target it was extracted from.
4. Sort labels into PRESENT / MISSING / DROP CANDIDATES.
5. Within PRESENT, note clusters, tensions, mutual dependencies.
6. If a materially different decomposition would work, name it briefly.
7. Note any self-reference — flag recursion.
</method>

<stance>
Don't soften the read. If a rule looks redundant, defensive, off-key — say so plainly.
</stance>

<do_not>
- Do not rewrite the prompt. Do not produce a "cleaner version." Do not propose replacement wording. Do not summarize back as prose.
</do_not>

<output_format>
PRESENT - [English label] — brief — [citations]; clusters/tensions inline.
MISSING - [English label] — why framing calls for it.
DROP CANDIDATES - [English label] — why flagged.
ALTERNATIVE READING (optional)
QUESTIONS
</output_format>
```

## Variant-D-specific framing for the `<framing>` slot

When applying this technique to a CLAUDE.md / rules file, use this framing:

> *The reader is a fresh Claude Code session in this repo on a working task. The prompt's job is to install the behavioral pull that survives detail-decay — principles, mechanisms, and N≥2-supported patterns. Anything that reads as ADR-style historical narrative, discoverable from the filesystem, or wrong-mechanism (skill-shaped, hook-shaped, settings-shaped, path-scoped-rule-shaped) should appear in DROP CANDIDATES even when the prose is good. The author's pride in a section is not evidence for its inclusion.*

## Conservation of mass

Every DROP CANDIDATE that contains *real signal* needs a relocation target, not a delete. The skill recommends moves, not deletions. Pure delete is reserved for: pure ADR history (relocate to git log / docs/decisions/), discoverable junk (ASCII trees), and confirmed scar tissue.
