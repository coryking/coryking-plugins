---
name: activity-reflection
description: Analyse how human attention was spent across agent sessions over a window, combining activity timeline proxies with original conversation evidence. Use for attention, delegation, intervention and recurring activity reports.
---

# Reflect on attention

This is an analytical workflow, not a token-usage report. Keep deterministic activity measurements separate from judgements about what human participation meant. A high turn count or an interruption does not establish that the agent needed babysitting.

Give the [activity analyst](../../agents/activity-analyst.md) the original request and clarifications, subject, dates/timezone, scope, headline question, classification definitions, and previous results explicitly labelled as priors to test. Include a glossary or known campaign facts only when useful; separate hypotheses from facts. Use the user's existing definitions/output preference, otherwise choose sensible defaults and state them. Ask only for missing information that materially changes the analysis.

Use fresh context where the harness supports it to reduce carryover from the caller's interpretation; do not claim the worker inherits no harness or repository instructions. Claude Code can dispatch `project-mining:activity-analyst` (Sonnet default). Codex uses a generic `spawn_agent` with that file's body, `gpt-6.1-sol`, high effort and `fork_turns: "none"` when available. Otherwise use an available suitable reasoning model or analyse inline. Do not invent a registered Codex agent type.

The analyst calls `get_activity_timeline` for the window and uses source reads to calibrate its categories. A fleet-wide attention question needs all projects so concurrency is visible; a project-scoped question may narrow sources but must describe that denominator. Do not send preclassified sessions as ground truth. Review its quoted originals and reopen consequential or disputed examples before endorsing the interpretation.

Return the requested report in chat or at the requested destination. For recurring comparisons, preserve dated reports with window, taxonomy, units and priors so later runs can compare like with like. Do not create a recurring job unless requested.
