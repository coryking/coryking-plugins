# Optional conversation copies

Use a copy when a question needs a past session's interpretation or a user wants to continue its work. An interview replays context and generates testimony; ordinary fact/quote/tool-action retrieval should use original records. A session's retelling may help locate evidence, but verify consequential claims against originals. A compacted session's early history is already summarised, so interview testimony about it has that additional limitation.

Before converting for an interview, check the source size and destination model's available context. Browsing context estimates are approximate; leave room for the task and answer. If headroom is uncertain, use selective source reads. Check that this harness exposes a way to resume the resulting Claude subagent. In Claude Code this can be `SendMessage` (load it with ToolSearch if deferred); a generic Codex `send_message` is not interchangeable with Claude's resume operation. If resume is unavailable, do not create an unusable interview copy.

`convert_session(direction="session_to_subagent")` copies a Claude or Codex session under the destination Claude parent. It does not mutate the original. Codex input is a rendered text conversion: private reasoning is omitted and tool output can be shortened. Check exact commands/results against original records.

For a one-shot interview, open with the response's `suggested_handoff`, identify yourself and the purpose, and offer your outside reading as a hypothesis rather than established fact. Batch the questions to avoid repeated replay. Label the reply as testimony. When finished, explicitly delete that created id with `delete_conversions(ids=[created_id], force=true)`. Interviewing grows the copy: ordinary deletion and automatic cleanup protect grown artifacts. Force is allowed only for explicit ids, never a blanket sweep. Retain a copy only when continuing its work is the intended outcome; preserve its id and resume instructions.

`subagent_to_session` creates a Claude top-level copy and returns a `claude -r` command for continuation. It is not supported for Codex source subagents. `delete_conversions` removes converter-created subagents only; converted top-level sessions are managed separately.

`rewind_transcript` permanently cuts a conversion artifact at an original turn. `cut="after"` keeps the named turn; `cut="before"` drops it and later entries. It refuses originals and ordinary dispatched agents, trims to a resumable boundary and restamps creation provenance. Make another copy first when the discarded tail must survive.

Pristine, never-resumed session-to-subagent copies have an age-based cleanup backstop (about 24 hours, at server start/stop). Grown copies contain unique work and are kept. Do not rely on that backstop for an interview's cleanup. Failed cleanup should be reported with the retained id and reason.
