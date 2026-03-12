# Claude Code JSONL Chat Log Format

Reference for the JSONL files stored under `~/.claude/projects/`.

## Directory structure

```
~/.claude/projects/<dir-name>/          # one dir per project
    <uuid>.jsonl                        # one file per conversation session
```

Directory names are derived from the project's absolute path with `/` replaced by `-`:
`~/projects/my-web-app` → `-Users-username-projects-my-web-app/`

## Record types

Each line in a JSONL file is one JSON record with a `type` field.

| `type` | What it is | Part of conversation? |
|---|---|---|
| `user` | User-side message (human input OR tool results fed back) | Yes |
| `assistant` | Model-side message (prose, tool calls, thinking) | Yes |
| `progress` | Subagent progress updates | No |
| `system` | System messages (has `subtype` field) | Metadata |
| `file-history-snapshot` | File state snapshots for undo/redo | No |
| `queue-operation` | Internal queue bookkeeping | No |

## Common top-level fields

Present on most record types:

| Field | Type | Description |
|---|---|---|
| `type` | string | Record type (see above) |
| `uuid` | string | Unique ID for this record |
| `parentUuid` | string/null | UUID of parent message (conversation threading) |
| `timestamp` | string | ISO 8601 timestamp |
| `sessionId` | string | Groups records from one session |
| `userType` | string | `"external"` for main conversation; absent on bookkeeping records |
| `isSidechain` | bool | Whether this is on a side branch |
| `cwd` | string | Working directory at time of record |
| `gitBranch` | string | Git branch at time of record |
| `version` | string | Claude Code version |
| `slug` | string | Human-readable turn identifier (e.g. `"jazzy-enchanting-hennessy"`) |

## User records (`type: "user"`)

### Key distinguishing fields

| Field | When present | Meaning |
|---|---|---|
| `toolUseResult` | On tool result messages | This record feeds a tool's output back to the model (file contents, bash output, etc.) — NOT human input |
| `isMeta` | On system-injected messages | System injection: skill prompt loads, local command caveats, etc. — NOT human input |
| `sourceToolAssistantUUID` | On tool result messages | Links back to the assistant message that made the tool call |
| `permissionMode` | Sometimes | User's permission mode setting |
| `todos` | Sometimes | Task list state |

### How to identify what's actually human speech

```
type=user AND toolUseResult absent AND isMeta absent → human talking
type=user AND toolUseResult present                  → tool result (skip for conversation mining)
type=user AND isMeta=true                            → system injection (skip for conversation mining)
```

**Exception:** Subagent completion results are injected as raw string `user` records containing `<task-notification>` XML. These have NO `toolUseResult` or `isMeta` flag — must detect by checking if the raw string content starts with `<task-notification>`.

### Content structure

`message.content` is either:

- **Raw string** — direct human text (most common for actual human input), OR subagent results wrapped in `<task-notification>` XML
- **Array of typed blocks:**
  - `{type: "text", text: "..."}` — human text or system text like `"[Request interrupted by user]"`
  - `{type: "tool_result", tool_use_id: "...", content: "..."}` — tool output fed back

## Assistant records (`type: "assistant"`)

### Content structure

`message.content` is always an array of typed blocks:

| Block type | What it is | Visible on screen? |
|---|---|---|
| `text` | Model prose — explanations, responses, analysis | Yes |
| `tool_use` | Tool calls (Read, Bash, Grep, etc.) | Shown as tool call UI |
| `thinking` | Extended thinking blocks (has `signature` field) | No (internal) |

### Message-level fields

| Field | Description |
|---|---|
| `message.model` | Model ID used (e.g. `"claude-sonnet-4-20250514"`) |
| `message.stop_reason` | Why generation stopped (`"end_turn"`, `"tool_use"`, etc.) |
| `message.usage` | Token counts (`input_tokens`, `output_tokens`) |

### `tool_use` block structure

```json
{
    "type": "tool_use",
    "id": "toolu_...",
    "name": "Read",
    "input": {"file_path": "/path/to/file"},
    "caller": {"type": "direct"}
}
```

## Task notification messages (subagent results)

Subagent completion results are injected as `type=user` records where `message.content` is a raw string containing XML:

```xml
<task-notification>
<task-id>abef64b118c2a573e</task-id>
<tool-use-id>toolu_015DH37qhcEcaVYVQhfnDjpF</tool-use-id>
<status>completed</status>
<summary>Agent "Mine context window chat log" completed</summary>
<result>
[subagent's actual output text here]
</result>
<usage><total_tokens>42702</total_tokens><tool_uses>12</tool_uses><duration_ms>62219</duration_ms></usage>
</task-notification>
Full transcript available at: /private/tmp/claude-501/.../tasks/<id>.output
```

These records have NO `toolUseResult` or `isMeta` field — they look like normal user messages structurally. Detect by checking for `<task-notification>` in the raw string content.

## Other record types

### `progress`

Subagent progress updates during execution. Has `parentToolUseID` and `toolUseID` linking to the tool call, plus a `data` object with progress info.

### `system`

System events. Has `subtype` field (values TBD), `isMeta: true`, and sometimes `durationMs`.

### `file-history-snapshot`

File state snapshots for undo. Has `snapshot` (dict of file states), `messageId` linking to the message that caused the change, and `isSnapshotUpdate` flag.

### `queue-operation`

Internal queue bookkeeping. Has `operation` field and sometimes `content`.

## File size characteristics

Most of the file size comes from `tool_result` content blocks (entire file contents, command output). A 3MB JSONL might contain only 50KB of actual human + assistant conversation text. Stripping tool results, thinking blocks, and tool calls dramatically reduces the data volume while preserving the conversation signal.
