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

See [NOTICE](NOTICE) for the upstream provenance and the slimming ledger (which agents were dropped, demoted, or had restrictions removed).

## Status

**v0.3.2** — research agents + parallel-review skill + 17 reviewer personas. Skill exposed as `/el:review`.

Roadmap:
- [x] `web-researcher` agent (forked, tool-restriction removed)
- [x] `best-practices-researcher` agent (forked, skill-mapping de-namespaced)
- [x] `/engineering-loop:review` skill — parallel-review pipeline
- [x] 17 forked reviewer-persona agents backing the slim review skill
- [ ] Optional: `/engineering-loop:plan`, `/engineering-loop:brainstorm`, `/engineering-loop:ideate` if they earn their token weight after `/review` has been used in real work

### Personas shipped with `/engineering-loop:review`

**Always-on (5):** `correctness`, `testing`, `maintainability`, `code-simplicity`, `project-standards`

**Cross-cutting conditional (8):** `security`, `performance`, `reliability`, `adversarial`, `api-contract`, `data-migrations`, `agent-native`, `previous-comments`

**Stack-specific conditional (3):** `kieran-python`, `kieran-typescript`, `julik-frontend-races`

**CE conditional (1):** `deployment-verification-agent` — emits a Go/No-Go runbook (markdown, not JSON findings) when the diff has risky data changes.

Upstream ships 18 personas plus 4 CE always-on agents and 2 CE conditional agents. We dropped: `learnings-researcher` (audit-validated dead for solo use), `dhh-rails-reviewer`, `kieran-rails-reviewer`, `swift-ios-reviewer` (stack-mismatch for our projects), and `schema-drift-detector` (Rails-specific `db/schema.rb` cross-reference). Re-add any of them later by porting from `compound-engineering-v3.8.1` and adding to `plugin.json` + `persona-catalog.md` + `SKILL.md`.

We moved `agent-native-reviewer` from upstream's always-on tier into conditional — most projects don't ship agent features, so always-on would burn an agent dispatch on every review for a no-op triage. Conditional means the orchestrator fires it only when the diff touches LLM/MCP/tool-definition code.

## What's different from upstream

Ported from `compound-engineering-v3.8.1`. We start as a straight-laced port of the prompt content and change only very specific things, building up our own opinions over time as we use it.

| | upstream compound-engineering v3.8.1 | engineering-loop |
|---|---|---|
| Agent namespace | `ce-X` prefix | bare slug (`X`) |
| Tool restriction on research agents | `tools: WebSearch, WebFetch` (or restricted list) | unrestricted — research subagents can also write their findings |
| Skill cross-references in prompts | references `ce-X` skills | hardcoded name lookups replaced with semantic-matching prose; dead integration-point sections removed |
| Review persona roster | 18 personas + 4 CE always-on agents + 2 CE conditional agents | 17 personas (5 always-on + 8 cross-cutting conditional + 3 stack-specific conditional) + 1 CE conditional agent. Dropped Rails/Swift personas and `learnings-researcher`/`schema-drift-detector`. |
| Compound deposit loop | always-on `learnings-researcher` in every review | removed (audit-validated dead for solo use) |

## Attribution

Forked from [EveryInc/compound-engineering-plugin](https://github.com/EveryInc/compound-engineering-plugin) at tag `compound-engineering-v3.8.1` (MIT). See [NOTICE](NOTICE) for the upstream copyright and license terms.

## Install

This plugin lives in the [coryking-plugins marketplace](../README.md). Install via:

```
/install https://github.com/coryking/coryking-plugins
```

Then enable just `engineering-loop` if you want the slim setup without the other plugins in the marketplace.
