# engineering-loop

The parts of [compound-engineering](https://github.com/EveryInc/compound-engineering-plugin) that compound for a solo developer, without the team-scale scaffolding.

## Why this exists

Every Inc's compound-engineering plugin ships ~36k tokens of ambient context (60+ agents, ~40 skills) shaped for a content-company AI team with active issue queues, Slack workspaces, named-persona reviewers (DHH, Kieran, julik), and a full plan→work→review→compound delivery methodology. Most of that doesn't apply to solo work on a long-lived codebase, and the token tax is real.

What does compound for solo work:

- **Parallel multi-lens code review.** Several review personas in parallel still catches things one model pass misses.
- **Curated research agents** for "scan prior art before deciding" and "what are current best practices for X." Single-purpose system prompts with the right epistemics baked in.

What doesn't compound for solo work (audit-validated against 30+ days of session history):

- The `/ce:compound` deposit-retrieve loop. For a single operator with strong CLAUDE.md/standing-conventions discipline, principle-shaped manual curation beats incident-shaped agent-deposited learnings. The `learnings-researcher` agent searches `docs/solutions/` directories that never get populated.
- The stack-specific persona reviewers (Rails/Ruby/Python flavors) when your stack is something else.
- The editorial pipeline (brainstorm→plan→work→review→compound). Imposes process overhead that hobby-project motivation can't sustain.

See [docs/process/2026-05-13-compound-engineering-audit.md](../../Mozicode/docs/process/2026-05-13-compound-engineering-audit.md) (private) for the audit that drove the slimming decision.

## Status

**v0.1.0** — scaffold + research agents only.

Roadmap:
- [x] `web-researcher` agent (forked, tool-restriction removed)
- [x] `best-practices-researcher` agent (forked, skill-mapping de-namespaced)
- [ ] `/engineering-loop:review` skill — slim parallel-review with 7–8 reviewer personas (correctness, security, testing, maintainability, performance, reliability, code-simplicity, julik-frontend-races)
- [ ] Forked review-persona agents to back the slim review skill
- [ ] Optional: `/engineering-loop:plan`, `/engineering-loop:brainstorm`, `/engineering-loop:ideate` if they earn their token weight after `/review` is shipped

## What's different from upstream

Ported from `compound-engineering-v3.8.1`. We start as a straight-laced port of the prompt content and change only very specific things, building up our own opinions over time as we use it.

| | upstream compound-engineering v3.8.1 | engineering-loop |
|---|---|---|
| Agent namespace | `ce-X` prefix | bare slug (`X`) |
| Tool restriction on research agents | `tools: WebSearch, WebFetch` (or restricted list) | unrestricted — research subagents can also write their findings |
| Skill cross-references in prompts | references `ce-X` skills | hardcoded name lookups replaced with semantic-matching prose; dead integration-point sections removed |
| Review persona roster | 17 personas + Every-internal flavor (DHH, Kieran, every-style) | 7–8 stack-agnostic personas (planned) |
| Compound deposit loop | always-on `learnings-researcher` in every review | removed (audit-validated dead for solo use) |

## Attribution

Forked from [EveryInc/compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin) at tag `compound-engineering-v3.8.1` (MIT). See [NOTICE](NOTICE) for the upstream copyright and license terms.

## Install

This plugin lives in the [coryking-plugins marketplace](../README.md). Install via:

```
/install https://github.com/coryking/coryking-plugins
```

Then enable just `engineering-loop` if you want the slim setup without the other plugins in the marketplace.
