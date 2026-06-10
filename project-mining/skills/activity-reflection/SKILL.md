---
name: activity-reflection
description: >
  Dispatch the activity-analyst agent to characterize how the human's attention was
  actually spent across their Claude Code sessions over a time window — pulled-in vs.
  delegated, and what kind of attention. Use when the user asks how their time with
  the fleet is going, wants an attention/herding makeup report, wants to test a prior
  claim about where their time goes, or a scheduled job (nightly dreamer, weekly cron)
  needs to produce the recurring reflection report. This skill is the CALLER's
  contract — it builds the dispatch packet; the analysis itself happens in the
  isolated activity-analyst agent.
---

# activity-reflection

Produces an evidence-backed report on the makeup of the human's attention across their Claude Code sessions: how much time was pulled in vs. delegated, classified into buckets (e.g. drift-response vs. genuine design vs. dispatch), with verbatim quotes as auditable evidence.

The architecture is deliberate: the **agent is a generic instrument with an isolated context** (it inherits none of this session's CLAUDE.md, memory, or priors — that isolation is what keeps the classification uncontaminated), and **all subject-specific knowledge enters through the dispatch packet you build here**. Do not "help" the agent by telling it what the projects are or what the answer should look like — it self-calibrates from transcripts. Its prompt already covers method, tool usage, and traps.

## Build the context packet

Assemble these, then dispatch `activity-analyst` (Task/Agent tool, `subagent_type: "project-mining:activity-analyst"`) with the packet as the prompt. Every item maps to a section the agent's definition expects:

1. **Subject + window** — whose attention; start/end dates; timezone. The agent calls `get_activity_timeline` itself; give it dates, not data.
2. **Taxonomy** — the classification buckets. Canonical example: *HERDING/drift-response · GENUINE design/architecture/product · MANUAL DISPATCH/orchestration · SCHEDULED AUTOMATION · OTHER* — with one line defining each in the subject's terms.
3. **Headline metric** — the single question the report must answer (e.g. "what fraction of pulled-in time is herding, by minutes and by intervention count").
4. **Priors to test** — the previous run's numbers, verbatim, framed as "corroborate or correct," never as "confirm." This is how trend continuity works across isolated contexts: the prior rides in as a declared, testable input.
5. **Min-reads threshold** — sessions the agent must read inside a bucket before publishing a sub-breakdown (default 3).
6. **Report destination** — where the deliverable lands. Reports MUST be kept as dated artifacts (file with date in name, issue comment, etc.); judgment-layer results cannot be recomputed later the way the deterministic map can, and the trend over weeks is built from these.
7. *Optional:* a short glossary of the subject's terms of art the window will contain ("the dreamer", project nicknames); standing facts about the window ("an agent-team campaign ran Tuesday").

## Dispatch template

```
Characterize the makeup of <subject>'s time working with their Claude Code sessions
over <start> through <end> (timezone <tz>).

Taxonomy: <buckets with one-line definitions>.
Headline metric: <the question>.
Priors to test: <previous numbers, verbatim — corroborate or correct>.
Min-reads threshold: <N>.
Deliver the report: <destination>.
Glossary / window facts: <optional>.
```

## Rules that protect the measurement

- **Never scope the tool to one project.** Attention is only measurable across the whole pool — concurrency and multitasking are cross-project facts. "Go deep on project X, shallow elsewhere" is a *taxonomy/lens* instruction inside the packet, not a tool scope.
- **Don't pre-classify in the packet.** Naming a session "that herding mess from Tuesday" contaminates the classifier. Give windows, definitions, and priors — not conclusions.
- **Chain the priors.** Each run's packet carries the previous run's headline numbers. A run without priors produces a baseline; a run with priors produces a trend point.
- **Scheduled use** (nightly dreamer, weekly cron): the calling job fills the packet mechanically — window = the period since the last report, priors = the last report's numbers, destination = the dated reports location. Nothing else changes.
