# Workload token accounting

Use `get_usage_report` for token consumption and `get_usage_observations` to audit its evidence. Accounting is separate from conversation browsing and attention reconstruction. These tools calculate no prices, accept no rates, and estimate no subscription allowance debit.

## Select and inspect

1. Discover sessions with `list_project_sessions`, `search_projects` or `list_session_agents`.
2. Call `get_usage_report(sessions=["codex:<full-thread-id>"])` or `get_usage_report(projects=["project-name"])`. Omit both selectors to inspect the local corpus; `harnesses` narrows providers. Explicit IDs can be unique prefixes of at least six characters; ambiguous/unresolved members fail the whole selection. Full harness-qualified identities are the durable join keys.
3. Read `scope`, `totals`, `coverage` and `warnings` before detail. Default descendant expansion includes evidenced Codex parent links and Claude nested files/progress/dispatch records; independent sessions require explicit selection or corpus/project scope. Explicit roots include discoverable children in other projects. Selecting a child twice, including via its parent, adds no usage. Calling sessions are eligible.
4. Pass a returned session identity to `get_usage_observations`. This uses the same engine without descendants. Observation IDs join to native counters and file/root/line/byte locators. `offset`/`limit` page observations, lifecycle and source detail; their counts precede the arrays. Limits are 1–500. A `next_offset` can exist because any of those arrays has more detail.

Reports page only session detail; scope membership, totals, coverage and rollups cover the whole selected scope. Each call is a fresh snapshot: a growing workload can produce different totals across calls. Source boundaries are retained within one call; snapshots are per file, not an atomic corpus snapshot. No permanent accounting parse cache stores transcript content.

`start` and `end` are timezone-aware ISO-8601 values selecting `[start,end)`. The engine reads predecessors before `start` for cumulative deltas. A cumulative interval spanning the start boundary is excluded from the window subtotal with a reason; there is no defensible allocation of its consumption to either side. Records without a usable time cannot be allocated to a window. Conversation counts and lifecycle markers are also filtered by their recorded event times.

## Read quantities correctly

`totals.categories` gives an `observed_subtotal`, `observations_measured` and `observations_unknown` for each category. The subtotals are sums of known accountable increments, not invented complete totals. Conflict/reset/baseline/inherited observations have no accountable increment. Native counters survive in drilldown even when excluded. Every nullable category has a machine-readable `category_reasons` entry.

The disjoint input categories are `uncached_input`, `cache_read_input`, `cache_creation_input`; output is `output`. `cache_creation_5m` and `cache_creation_1h` decompose cache creation and must not be added again. `reasoning_output` is included in output and must not be added again. A missing field is null, including absent write or TTL categories; it is never silently zero.

- Claude native `input_tokens` excludes cache reads and creation. Recorded request input is their sum. Cache TTL totals must reconcile with aggregate cache creation. Streaming/multipart records sharing `message.id` are reconciled within a file, then copies are compared; conflicting terminal copies remain visible and contribute no exact increment. Request IDs, record UUIDs and execution/sequence fallbacks are different identity qualities. Distinct requests with identical token values remain distinct.
- Codex `token_usage_record.payload.usage` is best-effort per completed response; `response_id` establishes response identity; a recorded `thread_id` distinguishes its execution owner from inherited copied context. Native input includes cached reads and cache writes, so uncached input subtracts both. `turn_token_usage`/`thread_token_usage` are not additional charges. When per-response records exist in a physical rollout, its cumulative `token_count` observations remain drillable and add no usage. This preference does not certify that every failed or unrecorded response has usage evidence.
- For cumulative-only Codex rollouts, increasing category counters yield deltas. Repeated counters add zero. The first counter can include earlier consumption and is an unassigned subtotal, with unknown time/configuration attribution. Inherited histories require a recovered predecessor; otherwise the first counter is an excluded baseline. Negative deltas indicate a reset/rewrite, never a silently clamped exact increment. Missing evidence or an intervening configuration change leaves a positive delta in the unknown model/effort bucket. A native total inconsistent with input plus output is excluded as context/inconsistent evidence; Codex can fill such counters to an estimated context window.

`model_requests` counts identified observed requests/responses when recoverable. Cumulative samples, synthetic assistants, missing request identity or unmeasured API/stream failures make that value null, with `model_requests_lower_bound` and a reason. Assistant turns are distinct Claude message IDs or Codex assistant message items, not cumulative events, reasoning items or tool calls. Human turns use Claude's `UserOrigin`; Codex bootstrap/scaffolding and agent messages are injected user messages. Tool calls have their own identity/count. A tool error does not establish a model retry or workflow failure.

First/latest/peak input observations describe recorded request input; the first observation need not be the first prompt, and request input is not exact current context occupancy. `elapsed_span_ms` spans the earliest/latest selected evidence; `sum_session_spans_ms` sums individual spans; `overlap_ms` is the excess over their interval union. Explicit Claude clean-turn duration markers contribute `recorded_active_session_work_ms`, which can undercount interruptions and double-count concurrent session time. `active_work_ms` remains unavailable without complete interval evidence. A response or turn completion is lifecycle evidence, not proof of workflow success; final workflow completion is unavailable.

The token fields on browsing/agent-detail tools are lightweight browsing estimates. They can reflect surviving conversation fragments and streaming records; use accounting tools for reconciled execution consumption.

## Branches, conversions and provenance

Codex accounting discovery retains every physical rollout/copy for each full logical thread ID. Retained response IDs deduplicate shared history and preserve distinct newly executed responses, including retained work outside the newest browsing head. A cumulative branch can use the retained history-base byte cutoff as its predecessor. Missing/conflicting bases and inherited ordinal gaps remain explicit. Child/fork requests with neither a recoverable boundary nor a recorded execution owner are excluded with a reason. This is observed retained history, not a lifetime reconstruction: deleted or unrecorded work cannot be recovered.

`history_base.thread_id` names a physical rollout ID, despite its field name; it can differ from the stable `session_meta.id`. History lineage and cutoffs alone do not establish user-revert intent. `forked_from_id` and its ordinal boundary describe a logical fork; `subagent_history_start_ordinal` distinguishes inherited child prefix from own work. Branch detail exposes these recorded facts without inferring intent.

Claude conversion sentinels supply `lines_at_creation`: that copied prefix is excluded, while appended resume work is accountable. A malformed/unrecoverable boundary excludes uncertain copied work and reports the limitation. Parent dispatch summaries are not additional request charges. Nested progress observations and child bodies sharing a request identity count once. Dispatch-only children without retained bodies remain visible with missing usage. Nested files without corroborating dispatch are labeled `nested_file_dispatch_unverified`; containment is evidence of storage relationship, not a fabricated causal dispatch.

Source roots and the report's `storage_host` establish where evidence was inspected. Recorded execution host and account are separate nullable provenance facts with reasons; a copied file's location does not establish its execution host. Unknown model/effort, role and caller labels have explicit null rollup buckets. Unsupported structural records differ from malformed accounting evidence. Coverage reports root enumeration failures, discovered/included/excluded sessions and files, unreadable/malformed/partial sources, missing usage and conflicts with denominators. Raw missing streaming fragments remain in diagnostic counts even if a later fragment recovers the request's usage.

## Caller attribution

`attribution` accepts objects targeting exactly one full included `session` identity or one `observation` identity. Labels can be `run`, `role`, `pass`, `round`, `phase`. Optional artifacts are `{name,value,unit}`; values must be finite and nonnegative and units explicit. Overlapping assignments fail. Attribution is a join and cannot multiply usage. Artifact sizes/results are caller facts; the engine does not parse arbitrary workflow files, convert bytes to tokens or infer accepted outcomes.

Example:

```json
{"sessions":["claude:<full-id>"],"attribution":[{"session":"claude:<full-id>","labels":{"run":"comparison-a","phase":"review"},"artifacts":[{"name":"accepted_results","value":1,"unit":"result"}]}]}
```

## Native semantic sources

The implementation uses these primary definitions, with uncertainty retained for missing or undocumented wire fields:

- [Anthropic Messages usage](https://platform.claude.com/docs/en/api/messages/create): disjoint input/cache categories, cache TTL breakdowns and thinking tokens included in output.
- [OpenAI token counting](https://developers.openai.com/api/docs/guides/token-counting) and [prompt caching](https://developers.openai.com/api/docs/guides/prompt-caching): inclusive input/output totals and their cache/reasoning decompositions.
- [Codex protocol at commit 0b863c69f50335acd92164aab971cb58d298c2fe](https://github.com/openai/codex/blob/0b863c69f50335acd92164aab971cb58d298c2fe/codex-rs/protocol/src/protocol.rs): `TokenUsageRecord`, `TokenUsageInfo`/`fill_to_context_window`, `HistoryPosition`, `SessionMeta`, `TurnContextItem`, parent/source metadata and lifecycle event definitions.
- [Codex response usage parsing at the same commit](https://github.com/openai/codex/blob/0b863c69f50335acd92164aab971cb58d298c2fe/codex-rs/codex-api/src/sse/responses.rs): mapping inclusive Responses usage to Codex categories and the cache-write test with input 100, cached 40, cache writes 60.

Cross-host aggregation requires overlapping-copy reconciliation, host/root reachability coverage, account identity and consistent snapshots. Adding per-host subtotals is unsafe. Federation/shared-store work is outside these local tools.
