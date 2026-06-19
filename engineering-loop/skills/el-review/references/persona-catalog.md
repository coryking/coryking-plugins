# Persona Catalog

Reviewer personas across always-on, cross-cutting conditional, and stack-specific conditional layers, plus one CE conditional agent. The orchestrator uses this catalog to select which reviewers to spawn for each review.

This is the engineering-loop catalog — it omits upstream compound-engineering's Rails/Swift stack-specific personas (we don't ship them), the always-on `agent-native-reviewer` and `learnings-researcher` (we run agent-native as conditional rather than always-on, since most of these projects don't ship agent features; learnings-researcher is audit-validated dead for this fork's target audience), and the migration-specific `schema-drift-detector` (Rails-specific `db/schema.rb` cross-reference).

## Always-on

Spawned on every review regardless of diff content.

| Persona | Agent | Focus |
|---------|-------|-------|
| `correctness` | `correctness-reviewer` | Logic errors, edge cases, state bugs, error propagation, intent compliance |
| `testing` | `testing-reviewer` | Coverage gaps, weak assertions, brittle tests, missing edge case tests |
| `maintainability` | `maintainability-reviewer` | Coupling, complexity, naming, dead code, premature abstraction |
| `code-simplicity` | `code-simplicity-reviewer` | YAGNI violations, unnecessary abstractions, over-engineering, things that should just be simpler |
| `project-standards` | `project-standards-reviewer` | CLAUDE.md and AGENTS.md compliance — frontmatter, references, naming, cross-platform portability, tool selection |
| `design-intent` | `design-intent-reviewer` | Whether what was produced (code, plan, or doc) satisfies the humans' intent; recovers it from docs, tickets/epics, commits, and chat (or by interrogating the originating session) and flags drift, missed steering, or missing human provenance. |

## Cross-cutting conditional

Spawned when the orchestrator identifies relevant patterns in the diff. This is agent judgment, not keyword matching.

| Persona | Agent | Select when diff touches... |
|---------|-------|---------------------------|
| `security` | `security-reviewer` | Auth middleware, public endpoints, user input handling, permission checks, secrets management |
| `performance` | `performance-reviewer` | Database queries, ORM calls, loop-heavy data transforms, caching layers, async/concurrent code |
| `reliability` | `reliability-reviewer` | Error handling, retry logic, circuit breakers, timeouts, background jobs, async handlers, health checks |
| `adversarial` | `adversarial-reviewer` | Diff has ≥50 changed non-test, non-generated, non-lockfile lines, OR touches auth, payments, data mutations, external API integrations, or other high-risk domains |
| `api-contract` | `api-contract-reviewer` | Route definitions, serializer/interface changes, event schemas, exported type signatures, API versioning, response shapes |
| `data-migrations` | `data-migrations-reviewer` | Migration files, schema changes, backfill scripts, ID/enum mappings, irreversible DDL |
| `agent-native` | `agent-native-reviewer` | LLM tool definitions, system prompt construction, MCP server config, agent integration code. **Unstructured output** (markdown Capability Map + categorized findings) — surfaced in Stage 6 Agent-Native Gaps section, not the findings table. |
| `previous-comments` | `previous-comments-reviewer` | **PR-only AND comment-gated.** Reviewing a PR that has existing review comments or review threads from prior review rounds. Skip entirely when no PR metadata was gathered in Stage 1, OR when Stage 1's `hasPriorComments` flag is false (no `reviews` and no `comments` on the PR). |

## Stack-specific conditional

These reviewers keep their original opinionated lens. Additive with the cross-cutting personas above, not replacements.

| Persona | Agent | Select when diff touches... |
|---------|-------|---------------------------|
| `kieran-python` | `kieran-python-reviewer` | Python modules, endpoints, services, scripts, or typed domain code |
| `kieran-typescript` | `kieran-typescript-reviewer` | TypeScript components, services, hooks, utilities, or shared types |
| `julik-frontend-races` | `julik-frontend-races-reviewer` | DOM event wiring, timers, async UI flows, animations, or frontend state transitions with race potential (React, Stimulus/Turbo, vanilla JS) |

## CE conditional

Specialized analysis beyond what the persona agents cover. Spawn when the diff includes destructive or risky data-layer changes and a Go/No-Go runbook is wanted.

| Agent | Focus |
|-------|-------|
| `deployment-verification-agent` | Produces Go/No-Go deployment checklist with SQL verification queries, rollback procedures, and monitoring plans. **Unstructured output** (markdown runbook) — surfaced in Stage 6 Deployment Notes section, not the findings table. |

## Project-custom conditional

A fourth layer that lives **in the consuming project, not in engineering-loop**. A project with a domain reviewer too specific to ship in the general roster (a finance project's cash-flow-modeling lens, a game's determinism reviewer, a data team's schema-conventions reviewer) contributes it to its own review runs without modifying this plugin. The roster is extensible per-project; the reviewers stay where they belong.

**Discovery.** The orchestrator globs the repo under review for `.claude/agents/*.md` and keeps only agents whose frontmatter sets `el-review: true`. The flag is the opt-in — most `.claude/agents/` entries are not review personas, so discovery is explicit, never automatic. Unmarked agents are ignored.

**Selection.** Each opted-in agent's frontmatter `description` carries its own "invoke when…" criterion. The orchestrator treats it exactly like a built-in cross-cutting conditional's selection line: read the diff, decide whether the agent's stated domain is touched, select it when it is. Additive to the built-in layers, never a replacement.

**Model.** Project-custom reviewers run at whatever their own frontmatter `model:` declares — they are exempt from the mid-tier dispatch override the built-ins receive. The built-in tiering was hand-calibrated per persona; an unknown project reviewer's stakes cannot be, so the author owns the call (`model: inherit` runs it at the session model).

**Author contract.** A project-custom reviewer must conform to the same output contract as a built-in persona (see `subagent-template.md`):

- Emit `findings-schema.json` JSON with a stable `reviewer` name (e.g. `financial-modeling`). Findings merge into the normal Stage 5 pipeline keyed by that name — no special surfacing.
- Honor the anchored confidence rubric (anchors `0/25/50/75/100`; actionable floor `75`, P0 may surface at `50`).
- Respect the `run_id` artifact convention: write full analysis to `/tmp/engineering-loop/review/{run_id}/{reviewer}.json`, return compact merge-tier JSON.
- An agent that emits **unstructured** markdown instead can use the existing agent-native / deployment surfaces (Stage 6 dedicated sections); structured JSON is the path that flows through merge/dedup/confidence.

**Reference implementation.** The King-family finance project's `financial-modeling-reviewer` (`~/projects/finance/.claude/agents/financial-modeling-reviewer.md`) is built to this contract — schema- and rubric-compatible, with an explicit "Invoke when…" description — and serves as the worked example for authors.

## Selection rules

1. **Always spawn all always-on personas.**
2. **For each cross-cutting conditional persona**, the orchestrator reads the diff and decides whether the persona's domain is relevant. This is a judgment call, not a keyword match.
3. **For each stack-specific conditional persona**, use file extensions and changed patterns as a starting point, then decide whether the diff actually introduces meaningful work for that reviewer. Do not spawn language-specific reviewers just because one config or generated file happens to match the extension.
4. **For the CE conditional agent**, spawn when the diff has destructive or risky data-layer changes (NOT NULL additions on populated tables, type changes that can lose precision, bulk backfills, irreversible DDL) and the operator wants an executable Go/No-Go runbook.
5. **For each project-custom reviewer** discovered via the `el-review: true` flag in the repo under review, judge its frontmatter `description` against the diff the same way as a cross-cutting conditional, and select it when its domain is touched.
6. **Announce the team** before spawning with a one-line justification per conditional reviewer selected.
