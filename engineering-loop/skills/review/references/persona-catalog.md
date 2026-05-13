# Persona Catalog

11 reviewer personas organized into always-on and conditional layers. The orchestrator uses this catalog to select which reviewers to spawn for each review.

This is the slim engineering-loop catalog — it omits upstream compound-engineering's Rails/Python/Swift/etc. stack-specific personas (we don't ship them), the CE-internal always-on agents (`agent-native-reviewer`, `learnings-researcher` — the latter audit-validated dead for this fork's target audience), and the migration-specific CE conditional agents (`schema-drift-detector`, `deployment-verification-agent`).

## Always-on (5 personas)

Spawned on every review regardless of diff content.

| Persona | Agent | Focus |
|---------|-------|-------|
| `correctness` | `correctness-reviewer` | Logic errors, edge cases, state bugs, error propagation, intent compliance |
| `testing` | `testing-reviewer` | Coverage gaps, weak assertions, brittle tests, missing edge case tests |
| `maintainability` | `maintainability-reviewer` | Coupling, complexity, naming, dead code, premature abstraction |
| `code-simplicity` | `code-simplicity-reviewer` | YAGNI violations, unnecessary abstractions, over-engineering, things that should just be simpler |
| `project-standards` | `project-standards-reviewer` | CLAUDE.md and AGENTS.md compliance — frontmatter, references, naming, cross-platform portability, tool selection |

## Conditional (6 personas)

Spawned when the orchestrator identifies relevant patterns in the diff. The orchestrator reads the full diff and reasons about selection — this is agent judgment, not keyword matching.

| Persona | Agent | Select when diff touches... |
|---------|-------|---------------------------|
| `security` | `security-reviewer` | Auth middleware, public endpoints, user input handling, permission checks, secrets management |
| `performance` | `performance-reviewer` | Database queries, ORM calls, loop-heavy data transforms, caching layers, async/concurrent code |
| `reliability` | `reliability-reviewer` | Error handling, retry logic, circuit breakers, timeouts, background jobs, async handlers, health checks |
| `adversarial` | `adversarial-reviewer` | Diff has ≥50 changed non-test, non-generated, non-lockfile lines, OR touches auth, payments, data mutations, external API integrations, or other high-risk domains |
| `julik-frontend-races` | `julik-frontend-races-reviewer` | DOM event wiring, timers, async UI flows, animations, or frontend state transitions with race potential (React, Stimulus/Turbo, vanilla JS) |
| `previous-comments` | `previous-comments-reviewer` | **PR-only AND comment-gated.** Reviewing a PR that has existing review comments or review threads from prior review rounds. Skip entirely when no PR metadata was gathered in Stage 1, OR when Stage 1's `hasPriorComments` flag is false (no `reviews` and no `comments` on the PR). |

## Selection rules

1. **Always spawn all 5 always-on personas.**
2. **For each conditional persona**, the orchestrator reads the diff and decides whether the persona's domain is relevant. This is a judgment call, not a keyword match.
3. **Announce the team** before spawning with a one-line justification per conditional reviewer selected.
