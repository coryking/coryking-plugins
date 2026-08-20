---
name: squabble
description: Convene a panel of expert reviewers to argue over the design of a tool, model, spec, plan, or decision before it is built. Use when a design or idea needs scrutiny from several angles at once before committing to it. Not for reviewing finished, working code.
argument-hint: "[what to squabble over — or blank to squabble over the design on the table in this conversation]"
---

# Squabble

A squabble pressure-tests a design while it is still an idea and still cheap to change. A panel of expert reviewers each attacks it from one angle — the question itself, what's missing, whether it can be built, where the numbers came from — then reads each other's reports and rebuts, updates, or concedes until the room goes quiet. The human gets back one record of what the panel agreed on, disputed, and withdrew. The panel advises; the human decides.

The panel is orchestrated end-to-end by a `moderator` agent — a relay and note-taker, not a judge. Your job in this conversation is only the edges: extract what the conversation alone holds, confirm the setup with the human, dispatch the moderator, and relay its record.

## When to use

- A design, spec, model structure, or plan is on the table and a wrong choice is expensive to unwind.
- An idea is still fuzzy and needs both expansion (new options) and pressure-testing before it hardens.
- A consequential, hard-to-reverse decision needs more than one perspective before committing.

Not for: reviewing finished, working code; quick factual lookups; anything a single careful pass honestly covers.

## The roster

Propose **three to five** roles — never more. Each reviewer runs an independent review pass plus one or more short rebuttal passes (until the panel stops moving, capped at four), so the roster is a budget, not a party list.

| Role | Angle |
|---|---|
| `framer` | Is this the right question at all? Owns the premise challenge. |
| `skeptic` | What isn't there — the silent omission, the internal contradiction. |
| `engineer` | Can it be built, and can its structure represent the reality it models? |
| `wildcard` | New options and shapes nobody named. Generative, not critical. |
| `auditor` | Where every number, default, and load-bearing term came from. |
| `calibrator` | Which way the estimates lean, whether the leans stack, where the conclusion breaks. |
| `stress-tester` | The most-likely path, the ugly paths, reversal cost, the unpriced alternative. |
| `historian` | Base rates, the project's own track record, the mistake an earlier analysis already made. |

Picking:

- **Always the `framer`.** Someone must guard the question.
- **Early / fuzzy idea** → `wildcard`, `framer`, `calibrator`.
- **Concrete design or spec** → `framer`, `auditor`, `engineer`, `skeptic`, and `stress-tester` when the cost of being wrong is high.
- **Anything resting on the future, or with a track record to consult** → add the `historian`.

## Procedure

### 1. Gather the packet

Collect from this conversation everything the moderator cannot see. Every field matters — a missing one silently skews the whole panel:

- **The human's question, verbatim.** Quote their words, not your paraphrase of them. This is the panel's scope boundary.
- **The candidates** — each design option on the table, described at roughly equal depth, each tagged with its origin (the human's idea, your idea, from a document). If you have developed your own candidate further in this conversation, summarize it at the same length as the others; the moderator equalizes, but don't make it reconstruct what you omitted from the rest.
- **Artifact paths** — the actual files (code, specs, data) the design touches, plus any project docs that bind it (terminology rules, data-handling rules).
- **Ruled-out topics** — everything the human has excluded, however casually they said it.
- **Context facts** — constraints, history, and prior decisions that live only in this conversation.

### 2. Confirm with the human

Before dispatching anything, show them in plain words: the question as you've captured it (quote it), the proposed roster and why each seat, and the cost — each reviewer does a full review plus up to four short rebuttal rounds, plus the moderator. Invite correction; this is the cheapest moment to catch a wrong premise or a wrong roster. Use a structured question (AskUserQuestion) when available. Do not dispatch until they say go.

### 3. Dispatch the moderator

One Agent call: `subagent_type` pointing at the squabble `moderator` agent, prompt containing the full packet plus the confirmed roster. Then leave it alone — the moderator authors the brief, runs the review and rebuttal passes, and assembles the record. Do not work on the design yourself while it runs.

### 4. Relay the record

The moderator's final message is the squabble's result, written for the human: a digest up front (the panel's answer, findings grouped by how the panel left them, disagreements presented as disagreements, any premise challenge as an explicit question to them), with every report appended verbatim. Deliver it substantially as-is — do not soften disagreements, do not trim the "what held up" section, do not append your own verdict on top. It is advice; the human owes nobody a response to it.

That closes the squabble. If the human wants a follow-up round, dispatch the moderator afresh with the original packet, the record, and their steer.
