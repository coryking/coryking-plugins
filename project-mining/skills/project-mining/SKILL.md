---
name: project-mining
description: Build an evidence-backed account of a project's work against a question, rubric or lens, combining history, source and actual outputs. Use for portfolio evidence, patterns, skills and cross-source investigations; quick history lookups use cc-explorer.
---

# Mine project evidence

Produce useful raw material the user can assess or rewrite: concrete findings grounded in original evidence, with context and gaps. The value is connecting evidence across sources to the question, not producing an inventory or filling a findings quota.

Start from the user's request, supplied rubric/documents and corrections. Establish the subject, projects, time scope, purpose and requested output. Infer routine choices from context; ask only where an unanswered choice materially changes the result. Existing instructions can already authorise execution. Do not require an alignment interview or a separate approval gate when the assignment is clear.

Use focused orientation only where needed: project identity, relevant files, history availability, output access and known constraints. Do not launch a mandatory scout. For multi-human work, distinguish the subject's evidenced contribution from the surrounding system; do not attribute all work from ownership of the repository. AI coauthor trailers neither prove nor disprove a particular human contribution.

## Gather originals, then interpret

Use the [history skill](../cc-explorer/SKILL.md) for retrieval and its portable evidence-worker dispatch. The caller keeps the full question and owns the lens mapping and synthesis. Default economical workers to bounded discovery; use Sol high in Codex or a suitable stronger Claude model for ambiguous cross-source judgement. If the current caller cannot perform that judgement, delegate it with the original request and complete source packet rather than pretending discovery alone answered the lens.

Divide useful independent inquiries by subsystem, question or project. A worker may follow connected history, code and outputs instead of handing the same subsystem between three corpus-specific analysts. Tell workers which sources already exist and what siblings own. Cross-checking is useful when it resolves a concrete uncertainty; repeated inventories and uncoordinated source reads are not a required phase.

Preserve the original request and clarifications in each assignment. Label the caller's interpretation, candidate explanations and relevant leads separately. For a substantial run, keep these and the supplied lens in a run file at the user's chosen destination; workers read that file directly. No placeholder documents or fixed dispatch template are needed.

Evidence may include:

- Original human decisions/corrections, dispatcher prompts, tool results and git history showing how work happened. A proposed action or agent “done” claim is weaker than execution evidence.
- Source/configuration explaining what exists, with file/commit locators. Documentation describes a claim or intention until corroborated.
- Actual outputs, preserved samples, screenshots, records or logs. State whether you directly observed them, read a captured example, relied on a builder's report, or inferred behaviour from code. Do not turn intended behaviour into shipped output. Use existing authorised read paths; an evidence hunt does not authorise deployments, production jobs, new credentials or hardware changes.

The evidence worker returns relevant excerpts and locators, not a prewritten conclusion. Read its packet and reopen consequential originals, including surrounding corrections. Request further context for unresolved contradictions or missing evidence. Avoid a new research wave merely for completeness.

## Write the account

Combine corroborating history, implementation and output evidence into findings organised around the user's question. Explain what each observation demonstrates and what it cannot establish. Separate evidence, attributed claims and interpretation; keep concrete disagreements and corrections visible. Strong work can be named plainly without an advocacy narrative.

Include enough locators to reopen the evidence, even if a later outward-facing draft omits detailed citations. State scope, sampling and meaningful gaps. Dates should establish when the work occurred. Match the requested form: discussion stays in chat; create a lasting report when requested. Keep private evidence out of public issues, repositories and PRs.

During longer work, report useful findings and remaining uncertainties. Finish with the answer and its significance, then the evidence/report location and material limits. The user should not have to inspect agent dispatch logs to understand the result.
