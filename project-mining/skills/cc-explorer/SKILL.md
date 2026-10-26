---
name: cc-explorer
description: Search and inspect Claude Code and Codex history for decisions, original wording, agent prompts, tool actions, and usage. Use for questions about past conversations or agent runs.
---

# Read conversation history

Use the cc-explorer MCP tools for history. A known turn or quick lookup can stay inline. For an investigation, delegate discovery using the [evidence worker](../../agents/evidence-worker.md); keep interpretation and the answer in the calling session. Project evidence reports use the project-mining workflow; attention reports use activity-reflection, both in this plugin.

## Delegate with the original question

Pass the user's request and relevant clarifications verbatim, known project/session/time scope, intended use, and any excluded sources. Label your working interpretation and hypotheses separately. Give a concrete inquiry, not a proposed conclusion. If several workers help, divide by questions or subsystems and share existing source locators; avoid mandatory parallel passes over the same sources.

- **Claude Code:** dispatch `project-mining:evidence-worker` with the assignment. Its configured default is Haiku; use Sonnet when conflicting sources or ambiguous relevance require stronger judgement.
- **Codex:** read the evidence-worker file linked above and give its body plus the assignment to a generic `spawn_agent`. Use `model: "gpt-6-luna"`, `reasoning_effort: "high"`, `fork_turns: "none"` when those controls/models are available. Codex does not register this plugin's Claude agent names. Use Sol high for synthesis or a difficult follow-up. These defaults are starting points, not a claim that Haiku and Luna have equal quality.
- **Other harnesses:** inject that same worker body into an available generic subagent with fresh context and a suitable economical model. If delegation or the named model is unavailable, perform the investigation inline with the same evidence contract. Do not invent a tool or model name.

Inspect available tool names first; load only needed descriptions. In code-mode tool orchestration, print `structuredContent` when present, otherwise the text content once. Printing whole MCP envelopes or every tool description duplicates large payloads.

## Retrieve enough context

`search_projects` finds candidate projects/sessions; `grep_sessions` searches several candidates together; `grep_session` and `read_turn` inspect specific moments. `browse_session` supplies a continuous window when keyword matches miss the arc. Use the absolute project and identifiers returned by tools when narrowing; prefer full identities when provided. Short prefixes can be ambiguous, in which case use a longer id from session listings. Search spans both harnesses unless filtered; Claude nested agents are included, while Codex children appear as separate sessions.

Choose `role` deliberately: `user` for human/orchestration messages, `assistant` for agent actions/results, `all` for both. A user-role turn may be a teammate message; it is not automatically a human instruction. Search defaults to `all`. Use `hide` to exclude categories and `truncate`, `limit`, and `max_chars` to bound excerpts. Zero matches are useful information, not a reason to repeat the same call.

Adapt the next read to the remaining uncertainty. A correction, pronoun, conflicting account, or quoted earlier answer calls for wider context. Stop when the question is supported or the missing evidence is clear; no fixed call count determines when to stop or escalate. Escalate with the actual conflict, failed approach, and source packet, so a stronger model can inspect rather than restart blindly.

## Verify and answer

The worker returns bounded originals with locators and relevance notes. Read that packet before synthesising; reopen the original prompt/result and surrounding turns for consequential claims, ambiguous excerpts, or suspected omissions. Increase depth on demand. Worker summaries and agent self-reports are leads, not substitute originals.

Distinguish observed actions, attributed claims, and your interpretation. Preserve later corrections and unresolved disagreement. When an original is missing and a quotation survives inside another tool result, cite both the unavailable original locator and the surviving record; do not imply direct access. If a retained prompt is encrypted or absent, say so rather than reconstructing it. State coverage and material gaps without implying an exhaustive census.

## Specialised operations

- Agent execution: `list_session_agents`, `get_agent_detail`, `audit_session_tools` inspect Claude dispatches, prompts, results and traces. A dispatch request alone is not proof of completed work; inspect the result and trace. Codex children use ordinary session reads.
- Failures: `survey_failures` or `errors_only=true` locates flagged failures. The survey's tool-call denominator includes only scanned error-bearing transcripts, not the whole corpus. Historical no-result errors can be ordinary discovery misses.
- Usage: read [usage accounting](references/usage-accounting.md) before interpreting consumption; use `get_usage_report`, not browsing estimates or guessed prices.
- Optional interviews, conversion, rewind and cleanup: read [conversation copies](references/conversation-copies.md) before using mutating tools. Interviews are an additional, potentially expensive testimony channel, not the default retrieval path.
