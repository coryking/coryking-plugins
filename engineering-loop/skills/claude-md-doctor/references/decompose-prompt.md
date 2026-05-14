# decompose-prompt — the technique

Cory's own technique (gist `91ff827fd5fdabcda8f87851d030c861`) for surfacing the conceptual basis of a prompt before editing it. **The value is intervenability, not capability** — the explicit pass earns its tokens by giving the user a handle, not by making the model smarter.

## Usage in this skill

Apply once per target file when triage selects Tier 2 or Tier 3. For each file:
- **target**: the file's full contents, with line numbers prefixed.
- **framing**: an LLM session loading this instruction surface for the first time. Conservation of mass — adding requires removing or merging. Detail decays; abstractions survive. The session has limited context budget and every line of auto-loaded content competes for attention.

Cite line numbers in the file as the "units."

## The prompt (verbatim)

```
<task>
Read the target prompt and surface its underlying conceptual basis in plain English, organized so I can decide what to keep, add, and cut. Do not rewrite the prompt. This is a working document for me, not a redraft.
</task>

<what_this_does>
I'm iterating on a prompt and I want to see its structure before I edit it. Extract the implicit commitments as named concepts I can read and argue with. The labels are handles for my own thinking — they need to be legible in English, not compressed or clever.
</what_this_does>

<optional_framing>
If a framing is provided below in <framing>, use it as the vantage for the "missing" and "drop" buckets. The framing describes who I want the prompt to serve, which is not necessarily the author it currently reflects. Without a framing, evaluate the prompt on its own internal terms.
</optional_framing>

<method>
1. Read the target as specification. Every unit (rule, section, example, aside) is there for a reason — identify what it is trying to produce, prevent, or express.

2. Name each commitment as a short English phrase. If a non-English term captures the concept substantially better — not just stylishly — use the English phrase as the primary label and put the foreign term in parentheses with a gloss. The primary label must always be readable in English. Err toward plain language; this is a working document, not a showcase.

3. For each label, cite the units in the target it was extracted from, so I can see the mapping.

4. Sort every label into one of three buckets:
   - PRESENT: commitments the prompt already makes.
   - MISSING: commitments the framing implies should be present but the prompt does not make. Without a framing, this bucket holds commitments the prompt assumes but never states.
   - DROP CANDIDATES: commitments in the prompt that look obsolete, defensive, redundant, or that conflict with the framing. Include why each is flagged.

5. Within PRESENT, note clusters, tensions, cousin-rules, and mutual dependencies. Tensions between PRESENT items and the framing are especially worth surfacing — a commitment can be internally coherent and still be working against the reader the prompt is meant to serve.

6. If a materially different decomposition would give a similarly good reading, name it in one or two sentences and say what the trade is.

7. Note any self-reference — if the prompt describes behaviors that the act of reading it would invoke, flag the recursion. A guardrail that names a failure mode may be summoning the framing it was written to prevent.
</method>

<stance>
Reason from what is in the target and the framing. Do not import an inferred profile of the author beyond what the framing establishes. Do not soften the read — if a rule looks redundant, incoherent, defensive, or off-key for the framing, say so plainly. The point is to see the thing clearly, not to validate it.
</stance>

<do_not>
- Do not rewrite the prompt.
- Do not produce a "cleaner version."
- Do not propose replacement wording for specific lines.
- Do not summarize the prompt back to me as prose.
Output is a map I can work from, not a redraft.
</do_not>

<output_format>
PRESENT
- [English label] (optional: foreign term, gloss) — brief description — [citations to target units]
- ...
Clusters, tensions (including tensions with the framing), mutual dependencies noted inline or below.

MISSING
- [English label] — brief description — why the framing calls for it.
- ...

DROP CANDIDATES
- [English label] — why it looks obsolete, defensive, redundant, or off-key for the framing.
- ...

ALTERNATIVE READING (optional, only if defensible)
One or two sentences on what a materially different decomposition would surface.

QUESTIONS
Things I had to guess at or that the target doesn't resolve.
</output_format>

<framing>
An LLM session loading this instruction surface for the first time. Conservation of mass — adding requires removing or merging. Detail decays; abstractions survive. The session has limited context budget and every line of auto-loaded content competes for attention.
</framing>

<target>
{{file contents inline here, with line numbers prefixed}}
</target>
```

## Grounding DROP CANDIDATES in evidence

Before finalizing a DROP, check at least one of:

- **chat-history** via `cc-explorer` (`search_project`, `grep_session`). If the rule's failure mode never appears in user corrections (`"don't"`, `"stop"`, `"no I said"`, `"ugh"`), the rule may not be earning its tokens.
- **`git log` on the section** — added in initial commit, never touched, AND the thing it guarded has since been removed/refactored → scar tissue.
- **codebase pattern** — rule says `"always X"` but `rg`/`ls` shows the codebase often doesn't → over-claim.

## Grounding MISSING (new) rules — N≥2 gate

A proposed MISSING rule must cite ≥2 independent incidents (chat sessions, commit messages, PR comments) where the absence caused friction. One instance → tag `hypothesis — one instance` and surface to user; never auto-promote.

## Author's own note (from the gist)

> "The decomposition pass is mostly forcing the model to spend tokens narrating something it already does internally. The value of externalizing the model's intermediate reasoning is intervenability, not capability. Worth knowing before you build elaborate scaffolding around something the model handles silently."

Use this to calibrate effort. The decompose pass earns its tokens on messy inputs where the user doesn't yet know what they think and needs a checkpoint to intervene before the model commits to an answer.
