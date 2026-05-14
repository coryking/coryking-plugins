# Decompose-Prompt — Full Method

The user-supplied prompt-engineering pattern this skill applies. Adapted from the original technique author's framing.

## Why decompose

Auto-loaded instruction files are dense. Surface-level edits ("rewrite section 3 to be tighter") miss the structural problem: which *commitments* is this file making, and which of those commitments still belong?

**Key insight from the technique's author:**
> "The decomposition pass is mostly forcing the model to spend tokens narrating something it already does internally. The value of externalizing the model's intermediate reasoning is **intervenability, not capability**."

In other words: the model can already silently judge what a CLAUDE.md is doing. Forcing it to write the judgement down lets the user (or a downstream pass) intervene before the rewrite.

## Method

1. **Read the target as specification.** Every unit — rule, section, example, aside, footnote, list-item — is there for a reason. Identify what each is trying to produce, prevent, or express.

2. **Name each commitment as a short English phrase.** Plain language. Not clever. "Always run tests before commit" not "test-gating contractual obligation."

3. **Cite the units it was extracted from.** Use line numbers or anchor headings. The map should let anyone re-derive the commitment from the source.

4. **Sort into three buckets:**

   - **PRESENT** — commitments the prompt currently makes. The bulk of the map. Cluster related commitments.
   - **MISSING** — commitments the framing *implies* but the prompt doesn't actually make. (E.g. file is full of "test before commit" rules but never says what counts as a passing test.)
   - **DROP-CANDIDATES** — commitments that look obsolete, defensive, redundant, or off-key. For each, write *why* it should drop. Categories to consider:
     - **Obsolete** — references stale tooling, abandoned conventions, dead links.
     - **Defensive** — written to prevent a failure that has not occurred, or only occurred once.
     - **Redundant** — duplicates a rule already in another file or another section.
     - **Off-key** — belongs in a different mechanism (skill, hook, settings, rules with `paths:`).
     - **Scar tissue** — added in response to a single incident; the incident is healed.
     - **Vibes** — not concretely verifiable.

5. **Note clusters, tensions, mutual dependencies.** Within PRESENT, do commitments contradict? Are some clusters big enough to deserve their own `.claude/rules/<topic>.md`?

6. **If a materially different decomposition would give a similarly good reading, name it briefly.** Don't pretend there's one correct map.

7. **Flag self-reference.** Guardrails that may be summoning the failure mode they were written to prevent. (E.g. "do not bloat this file" written into the bloated file itself.) These are often the most informative drops.

## Output shape

`decompose.md` should be skimmable. Suggested structure:

```markdown
# Decompose pass — <target file> @ <sha>

## PRESENT (cluster: <topic>)
- [L23-29] Commitment: <short phrase>. Notes: <if any>.
- [L31] Commitment: <short phrase>.

## PRESENT (cluster: <topic>)
- ...

## MISSING
- Commitment implied but not stated: <phrase>. Implication source: <where>.

## DROP-CANDIDATES
- [L40-44] <Phrase>. Reason: scar tissue. Last referenced in commit <sha>. No N≥2 pattern of recurrence.
- [L67] <Phrase>. Reason: off-key — belongs in `.claude/rules/foo.md` with `paths: src/foo/**`.

## Tensions and clusters
- Commitments [L23] and [L80] cut against each other under condition <X>.
- Cluster "GitHub workflow" is large enough to move to `.claude/rules/github-workflow.md`.

## Alternative reading
- One could read the file as primarily about <X> rather than <Y>; that decomposition would move <these> into PRESENT and <these> into DROP.

## Self-reference flags
- The "don't bloat this file" line at [L9] appears in a 210-line file.
```

## Stance

- Reason from the target. Don't soften the read because the author might feel called out.
- The map is the deliverable. The bold rewrite happens **after** the map is written and reviewed.
- Don't quote chat content, personal data, or sensitive material into the map. Cite shape and counts, not contents.
