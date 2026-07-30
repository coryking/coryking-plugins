# Adapted from claude-code-log by Daniel Demmel (MIT License)
# https://github.com/daaain/claude-code-log
"""Typed Pydantic models for Claude Code JSONL transcript entries.

Design: UserTranscriptEntry from claude-code-log is split into three classes
so isinstance() is the filtering mechanism:
- HumanEntry — actual human messages
- ToolResultEntry — tool output fed back to model (has toolUseResult)
- MetaEntry — system-injected messages (has isMeta)

Additional entry types for records that claude-code-log skips:
- ProgressEntry — streaming progress records (~87% of some subagent files)
- FileSnapshotEntry — file-history-snapshot records (~10% of main transcripts)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import BaseModel, BeforeValidator

from .utils import PrefixId, smart_truncate


# Coerce None → 0 for token fields that the API may return as null
NoneAsZero = Annotated[int, BeforeValidator(lambda v: 0 if v is None else v)]


# =============================================================================
# Hide — assistant-turn content filter
# =============================================================================
#
# `hide` is a comma-separated set of assistant-turn content atoms to suppress
# from both search and display. Default empty = show/search everything. Text
# is always shown and is not an atom.
#
# Atoms:
#   thinking — extended thinking blocks
#   inputs   — tool call summaries (assistant-side)
#   outputs  — tool results (ToolResultEntry content)


HIDE_ATOMS = frozenset({"thinking", "inputs", "outputs"})


def parse_hide(value: str | None) -> frozenset[str]:
    """Parse comma-separated hide string into a validated frozenset.

    None → frozenset() (show everything — the default)
    "" → frozenset() (show everything)
    "outputs" → frozenset({"outputs"}) (hide tool results)
    "inputs,thinking" → frozenset({"inputs", "thinking"})
    """
    if not value or not value.strip():
        return frozenset()
    atoms = frozenset(a.strip() for a in value.split(",") if a.strip())
    invalid = atoms - HIDE_ATOMS
    if invalid:
        raise ValueError(
            f"Invalid hide atoms: {sorted(invalid)}. "
            f"Valid: {sorted(HIDE_ATOMS)}. "
            f"Text is always shown and is not an atom."
        )
    return atoms


# =============================================================================
# Failure taxonomy — what a failed tool call was, and whose fault it was
# =============================================================================
#
# The ONE place that answers "did this tool call fail, and how?" — the failure
# analogue of UserOrigin. `ToolResultContent.failure` is the only classification
# entry point; everything else (audit, search's errors_only, survey_failures)
# reads it rather than re-deriving.
#
# The GATE is the structured signal: `is_error`. Prose heuristics are not used
# to decide *whether* something failed — measured against the live corpus, a
# marker-text heuristic ("not found", "no matches") fires on ~1400 results that
# the harness did NOT flag, and sampling those shows they are overwhelmingly
# successful command output that merely CONTAINS the phrase (npm build logs,
# ssh banners, grep output). Using prose as the gate would bury real breakage
# under noise. See `looks_like_no_match` for the separate, narrower question
# audit_session_tools asks.
#
# Kinds are matched in order, first hit wins, against a leading window of the
# whitespace-normalized result text (see FAILURE_SCAN_CHARS). Order matters:
# specific content rules run BEFORE `exit_code`, so a Bash failure is reported
# as what actually went wrong (git_rejected, parse_error, bad_path) rather than
# collapsing half the corpus into one uninformative bucket.


class FailureCategory(str, Enum):
    """Whose problem a failure is — the axis that separates noise from breakage.

    Most failure volume is `agent`: the model called a tool wrong and corrected
    itself. Someone asking "is SSH broken?" needs that out of the way, and
    someone asking "are my agents using my tools right?" needs exactly it.
    """

    cascade = "cascade"          # collateral damage from a sibling call in the same batch
    policy = "policy"            # a human or the permission system declined the call
    agent = "agent"              # the agent used the tool wrong
    environment = "environment"  # the world outside said no
    unknown = "unknown"          # unclassified


class FailureKind(str, Enum):
    """A failed tool result's classification. `unclassified` is not a fallback
    to be ignored — it is the tool's yield, the breakage no rule anticipated."""

    cascade = "cascade"
    user_rejected = "user_rejected"
    permission_denied = "permission_denied"
    stale_read = "stale_read"
    oversized_read = "oversized_read"
    edit_no_match = "edit_no_match"
    bad_call = "bad_call"
    bad_path = "bad_path"
    auth_failed = "auth_failed"
    network = "network"
    http_status = "http_status"
    timeout = "timeout"
    command_not_found = "command_not_found"
    git_rejected = "git_rejected"
    parse_error = "parse_error"
    exit_code = "exit_code"
    unclassified = "unclassified"

    @property
    def category(self) -> FailureCategory:
        return _FAILURE_CATEGORIES.get(self, FailureCategory.unknown)


_FAILURE_CATEGORIES: dict[FailureKind, FailureCategory] = {
    FailureKind.cascade: FailureCategory.cascade,
    FailureKind.user_rejected: FailureCategory.policy,
    FailureKind.permission_denied: FailureCategory.policy,
    FailureKind.stale_read: FailureCategory.agent,
    FailureKind.oversized_read: FailureCategory.agent,
    FailureKind.edit_no_match: FailureCategory.agent,
    FailureKind.bad_call: FailureCategory.agent,
    FailureKind.bad_path: FailureCategory.agent,
    FailureKind.auth_failed: FailureCategory.environment,
    FailureKind.network: FailureCategory.environment,
    FailureKind.http_status: FailureCategory.environment,
    FailureKind.timeout: FailureCategory.environment,
    FailureKind.command_not_found: FailureCategory.environment,
    FailureKind.git_rejected: FailureCategory.environment,
    FailureKind.parse_error: FailureCategory.environment,
    FailureKind.exit_code: FailureCategory.environment,
    FailureKind.unclassified: FailureCategory.unknown,
}


# How much of a result to read when classifying. Long enough to reach past a
# leading "Exit code N" into the real error, short enough that a phrase buried
# in 200 lines of successful output can't misclassify the result.
FAILURE_SCAN_CHARS = 1000


_FAILURE_RULES: tuple[tuple[FailureKind, re.Pattern[str]], ...] = (
    # An artifact, not a failure: one real error in a parallel batch cancels or
    # errors every sibling. Must be caught FIRST or every parallel batch triples.
    (FailureKind.cascade, re.compile(
        r"Sibling tool call errored|Cancelled: parallel tool call", re.I)),
    # A human said no.
    (FailureKind.user_rejected, re.compile(
        r"user doesn't want to proceed|Permission for this (?:action|tool use) was denied|"
        r"^Denied by user", re.I)),
    # The permission system said no (a rule, or an auto-deny).
    (FailureKind.permission_denied, re.compile(
        r"Permission to use \S+ has been (?:auto-)?denied", re.I)),
    # Agent-behavior errors: the model called the tool wrong.
    (FailureKind.stale_read, re.compile(
        r"File has not been read yet|File has been modified since read", re.I)),
    (FailureKind.oversized_read, re.compile(
        r"exceeds maximum allowed (?:tokens|size)|read specific portions of the file", re.I)),
    (FailureKind.edit_no_match, re.compile(
        r"String to replace not found in file|Found \d+ matches of the string to replace", re.I)),
    # Deliberately narrow: only markers a schema validator emits. A loose
    # "missing required" would swallow `your authentication token is missing
    # required scopes`, which is an auth failure, not a malformed call.
    (FailureKind.bad_call, re.compile(
        r"InputValidationError|validation error for call|Input should be|"
        r"missing required (?:argument|parameter|field|propert)|"
        r"unexpected keyword argument|Agent type '[^']*' not found", re.I)),
    # The world said no. Network before timeout so a refused/timed-out SSH reads
    # as a network fact rather than a generic timeout.
    (FailureKind.network, re.compile(
        r"ssh: connect to host|Connection refused|Could not resolve host|"
        r"Network is unreachable|No route to host|ECONNREFUSED|ENOTFOUND", re.I)),
    (FailureKind.auth_failed, re.compile(
        r"Permission denied \(publickey|Permission denied, please try again|"
        r"authentication token|AADSTS\d+|401 Unauthorized|Bad credentials|"
        r"Authentication failed", re.I)),
    (FailureKind.http_status, re.compile(
        r"status code [45]\d\d|HTTP (?:error )?[45]\d\d|\b[45]\d\d (?:Forbidden|Not Found|"
        r"Bad Request|Internal Server Error)", re.I)),
    (FailureKind.timeout, re.compile(
        r"timed out after|timeout of \d+\s*ms exceeded|Connection timed out|ETIMEDOUT", re.I)),
    (FailureKind.command_not_found, re.compile(
        r"command not found|\bsh: \d+: [^:]+: not found", re.I)),
    (FailureKind.git_rejected, re.compile(
        r"Note about fast-forwards|! \[rejected\]|cannot pull with rebase|"
        r"Not possible to fast-forward|non-fast-forward", re.I)),
    (FailureKind.parse_error, re.compile(
        r"JSONDecodeError|jq: error|SyntaxError|Expecting value: line|ParserError", re.I)),
    (FailureKind.bad_path, re.compile(
        r"File does not exist|EISDIR|ENOENT|no such file or directory|does not exist", re.I)),
    # Late catch-all: a Bash command that failed for a reason no rule named.
    (FailureKind.exit_code, re.compile(r"^Exit code \d+", re.I)),
)


def classify_failure(text: str) -> FailureKind:
    """Classify already-known-failed result text into a FailureKind.

    Does NOT decide whether something failed — the caller has already read the
    `is_error` flag. Returns `unclassified` when no rule matches, which is the
    signal a surveyor actually wants: breakage the taxonomy has not seen.
    """
    window = " ".join(text.split())[:FAILURE_SCAN_CHARS]
    if not window:
        return FailureKind.unclassified
    for kind, rx in _FAILURE_RULES:
        if rx.search(window):
            return kind
    return FailureKind.unclassified


# A DIFFERENT question from failure: "did this call come back empty / malformed
# enough that the agent learned nothing?" audit_session_tools asks it because a
# zero-hit search is a tool-usage problem worth seeing, even though the harness
# considers the call a success. It is deliberately NOT part of the failure gate
# — see the taxonomy note above.
NO_MATCH_MARKERS = (
    "no matches",
    "not found",
    "validation error",
    "input should be",
    "missing required",
    "unexpected keyword",
    "exceeds maximum",
)


def looks_like_no_match(text: str) -> bool:
    """True when a SUCCESSFUL tool result reads as a zero-hit / rejected call.

    A prose heuristic, and a noisy one at corpus scale (it fires on any command
    output containing "not found"). Scoped to single-session tool auditing,
    where the false-positive cost is a human glancing at one extra row.
    """
    if not text:
        return False
    head = text[:300].lower()
    return any(marker in head for marker in NO_MATCH_MARKERS)


# =============================================================================
# Content Models
# =============================================================================


class TextContent(BaseModel):
    type: Literal["text"]
    text: str


class ImageSource(BaseModel):
    type: Literal["base64"]
    media_type: str
    data: str


class ImageContent(BaseModel):
    type: Literal["image"]
    source: ImageSource


class ThinkingContent(BaseModel):
    type: Literal["thinking"]
    thinking: str
    signature: Optional[str] = None


class ToolUseContent(BaseModel):
    type: Literal["tool_use"]
    id: PrefixId
    name: str
    input: dict[str, Any]
    caller: Optional[dict] = None


class ToolResultContent(BaseModel):
    type: Literal["tool_result"]
    tool_use_id: PrefixId
    content: Union[str, list[dict[str, Any]]]
    is_error: Optional[bool] = None
    agentId: Optional[PrefixId] = None

    @property
    def text(self) -> str:
        """The result's text, untruncated and unformatted.

        Content arrives either as a bare string or as a list of typed blocks;
        this is the single place that flattens both. Non-text blocks (images)
        contribute nothing.
        """
        if isinstance(self.content, str):
            return self.content
        parts: list[str] = []
        for block in self.content:
            if isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "\n".join(parts)

    @property
    def failure(self) -> Optional[FailureKind]:
        """This result's FailureKind, or None when the call succeeded.

        `is_error` is the gate — see the failure-taxonomy note above for why
        prose is never used to decide failure, only to classify it.
        """
        if not self.is_error:
            return None
        return classify_failure(self.text)


ContentItem = Union[
    TextContent,
    ToolUseContent,
    ToolResultContent,
    ThinkingContent,
    ImageContent,
]


# =============================================================================
# Teammate messages — agent-team orchestration DMs
# =============================================================================
#
# In an agent-team session, an orchestrator (or peer worker) DMs a worker's pane
# by writing a user-role turn whose content opens with `<teammate-message ...>`.
# These are the orchestration protocol, not human attention. The markup grammar
# is fixed: `<teammate-message teammate_id="..." [color="..."] [summary="..."]>`
# wrapping a body of free prose or embedded JSON. We parse the attributes into
# structure but keep the body as a raw string (never parse embedded JSON).


class TeammateMessage(BaseModel):
    """A teammate-injected user turn, parsed from its `<teammate-message>` markup.

    `teammate_id` is the sender (orchestrator or peer worker). `color`/`summary`
    are optional presentation attributes. `body` is the raw content inside/after
    the tag — free prose or embedded JSON, kept verbatim (never JSON-parsed).
    """

    teammate_id: str
    color: Optional[str] = None
    summary: Optional[str] = None
    body: str = ""


# `<teammate-message teammate_id="..." [color="..."] [summary="..."]>BODY[</teammate-message>]`
# The closing tag is optional in the wild; body is everything after the opening
# tag, with a trailing close stripped. Attributes are order-independent.
_TEAMMATE_OPEN_RE = re.compile(
    r"<teammate-message\b([^>]*)>",
    re.IGNORECASE,
)
_TEAMMATE_ATTR_RE = re.compile(r'(\w+)\s*=\s*"([^"]*)"')
_TEAMMATE_CLOSE_RE = re.compile(r"</teammate-message>\s*$", re.IGNORECASE)


def parse_teammate_message(text: str) -> Optional[TeammateMessage]:
    """Parse `<teammate-message ...>` markup into a TeammateMessage, else None.

    Only fires when the (left-stripped) text OPENS with the marker — a turn that
    merely mentions the string mid-prose is not a teammate DM. `teammate_id` is
    required; markup missing it is not a valid teammate message.
    """
    stripped = text.lstrip()
    m = _TEAMMATE_OPEN_RE.match(stripped)
    if not m:
        return None
    attrs = dict(_TEAMMATE_ATTR_RE.findall(m.group(1)))
    if "teammate_id" not in attrs:
        return None
    body = stripped[m.end():]
    body = _TEAMMATE_CLOSE_RE.sub("", body).strip()
    return TeammateMessage(
        teammate_id=attrs["teammate_id"],
        color=attrs.get("color"),
        summary=attrs.get("summary"),
        body=body,
    )


# =============================================================================
# User-turn origin — what a user-role entry really is
# =============================================================================


class UserOrigin(str, Enum):
    """What a user-role transcript entry actually is.

    User-role JSONL entries are a grab bag: real human prompts, teammate DMs in
    agent-team sessions, tool outputs fed back to the model, system-injected meta
    messages, bare command scaffolding (a `/clear` with no prompt), and interrupt
    sentinels. This is the ONE place that answers "what is this entry, really?";
    the scattered helpers (substantive_human_text, is_teammate_injected, the
    interrupt-sentinel checks) are views consistent with it.

    Note: `meta`/`command_scaffolding` turns still count as human turns in the
    current counting paths (list_project_sessions, activity.py) — a deliberate
    deferred decision. `origin` exposes the classification; tools opt in later.
    """

    human = "human"                          # a genuine human-typed prompt
    teammate = "teammate"                    # a `<teammate-message>` DM (agent-team)
    tool_result = "tool_result"              # tool output fed back to the model
    command_scaffolding = "command_scaffolding"  # bare command, no human prose
    interrupt = "interrupt"                  # `[Request interrupted` esc sentinel
    meta = "meta"                            # system-injected (isMeta) message


_INTERRUPT_SENTINEL = "[Request interrupted"


# =============================================================================
# Message Models
# =============================================================================


class UsageInfo(BaseModel):
    input_tokens: NoneAsZero = 0
    cache_creation_input_tokens: NoneAsZero = 0
    cache_read_input_tokens: NoneAsZero = 0
    output_tokens: NoneAsZero = 0
    service_tier: Optional[str] = None
    server_tool_use: Optional[dict[str, Any]] = None


class UserMessageModel(BaseModel):
    role: Literal["user"]
    content: Union[str, list[ContentItem]]


class AssistantMessageModel(BaseModel):
    id: str
    type: Literal["message"]
    role: Literal["assistant"]
    model: str
    content: list[ContentItem]
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    usage: Optional[UsageInfo] = None


# Flexible type for toolUseResult field
ToolUseResult = Union[
    str,
    list[Any],
    dict[str, Any],
]


# =============================================================================
# Transcript Entry Models
# =============================================================================


class BaseTranscriptEntry(BaseModel):
    """Common fields across all transcript entries."""
    uuid: PrefixId
    parentUuid: Optional[PrefixId] = None
    timestamp: datetime
    sessionId: PrefixId
    isSidechain: bool = False
    userType: str = ""
    cwd: str = ""
    version: str = ""
    agentId: Optional[PrefixId] = None
    gitBranch: Optional[str] = None
    # How this session was invoked. "cli" = interactive; "sdk-cli" = headless
    # (claude -p / SDK / cron — e.g. the nightly dreamer runs). Distinguishes
    # human-driven attention from automated runs.
    entrypoint: Optional[str] = None
    # Agent-team membership, stamped on every entry of a team-worker session.
    # teamName is the team the pane belongs to (e.g. "cef-integration");
    # agentName is this worker's role in it (e.g. "reviewer-3"). Both absent
    # outside agent-team sessions. A worker's user-role turns are mostly
    # INJECTED BY TEAMMATES, not typed by the human — see is_teammate_injected.
    teamName: Optional[str] = None
    agentName: Optional[str] = None

    @property
    def is_headless(self) -> bool:
        """True for non-interactive invocations (claude -p / SDK / cron)."""
        return self.entrypoint == "sdk-cli"

    def display(
        self,
        truncate: int,
        hide: frozenset[str] = frozenset(),
    ) -> str:
        """Short display string for unknown entry kinds (no role/id; pipe line carries that)."""
        return "[?]"


class HumanEntry(BaseTranscriptEntry):
    """Actual human messages — the user talking.

    Despite the name, a user-role entry may not be a human prompt at all: in
    agent-team sessions an orchestrator/peer DMs the pane (see teammate_message),
    a bare slash command carries no prose, an esc produces an interrupt sentinel.
    `origin` is the authoritative classification.
    """
    type: Literal["user"]
    message: UserMessageModel
    isMeta: Optional[bool] = None
    # "sdk" for headless/SDK-driven prompts (cron/-p), absent/other for typed input.
    promptSource: Optional[str] = None

    @property
    def teammate_message(self) -> Optional[TeammateMessage]:
        """Parsed `<teammate-message>` DM if this turn is teammate-injected, else None.

        The marker arrives as a bare string in raw JSONL; the parser normalizes
        user content to `[TextContent]`, so detection keys on the leading text
        (which survives normalization), not the str/list shape (which does not).
        """
        return parse_teammate_message(_user_marker_text(self))

    @property
    def origin(self) -> UserOrigin:
        """What this user-role entry really is — the single classification source."""
        if self.isMeta:
            return UserOrigin.meta
        if _INTERRUPT_SENTINEL in _user_marker_text(self):
            return UserOrigin.interrupt
        if self.teammate_message is not None:
            return UserOrigin.teammate
        if substantive_human_text(self):
            return UserOrigin.human
        return UserOrigin.command_scaffolding

    def display(
        self,
        truncate: int,
        hide: frozenset[str] = frozenset(),
    ) -> str:
        tm = self.teammate_message
        if tm is not None:
            # Render the orchestration DM labeled instead of as raw XML, so a
            # teammate turn reads as `[teammate: <sender> → <recipient>] body`.
            # Body is the full message (preserved at full fidelity); the summary,
            # when present, leads as a compact gloss.
            recipient = self.agentName or "?"
            head = f"[teammate: {tm.teammate_id} → {recipient}]"
            body = f"{tm.summary} — {tm.body}".strip(" —") if tm.summary else tm.body
            line = f"{head} {body}".strip() if body else head
            return smart_truncate(line, truncate)
        text = extract_text(self)
        return smart_truncate(text, truncate)


class ToolResultEntry(BaseTranscriptEntry):
    """Tool output fed back to the model (has toolUseResult)."""
    type: Literal["user"]
    message: UserMessageModel
    toolUseResult: Optional[ToolUseResult] = None
    isMeta: Optional[bool] = None
    # agentId is inherited from BaseTranscriptEntry — do not redeclare

    @property
    def results(self) -> list[ToolResultContent]:
        """The tool_result blocks this entry carries.

        One entry can answer several parallel tool calls, so this is a list.
        """
        content = self.message.content
        if not isinstance(content, list):
            return []
        return [b for b in content if isinstance(b, ToolResultContent)]

    @property
    def failure(self) -> Optional[FailureKind]:
        """The kind of the first failed result block, or None if none failed.

        The entry-level view of `ToolResultContent.failure` — what search's
        `errors_only` filter keys on.
        """
        for block in self.results:
            kind = block.failure
            if kind is not None:
                return kind
        return None

    @property
    def origin(self) -> UserOrigin:
        """A tool result is tool output — unless an esc cut off the tool call,
        in which case it carries the interrupt sentinel."""
        if _INTERRUPT_SENTINEL in _user_marker_text(self):
            return UserOrigin.interrupt
        return UserOrigin.tool_result

    def display(
        self,
        truncate: int,
        hide: frozenset[str] = frozenset(),
    ) -> str:
        """Render tool output. Suppressed when 'outputs' is in hide."""
        if "outputs" in hide:
            return ""
        return self._render_output(truncate)

    def _render_output(self, truncate: int) -> str:
        """Extract and format tool result from message.content ToolResultContent items."""
        parts: list[str] = []
        content = self.message.content
        if isinstance(content, list):
            for item in content:
                if isinstance(item, ToolResultContent):
                    error_prefix = "[error] " if item.is_error else ""
                    if isinstance(item.content, str):
                        text = smart_truncate(item.content, truncate)
                        parts.append(f"{error_prefix}{text}")
                    elif isinstance(item.content, list):
                        for block in item.content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = smart_truncate(block.get("text", ""), truncate)
                                parts.append(f"{error_prefix}{text}")
                            elif isinstance(block, dict) and block.get("type") == "image":
                                parts.append("[image]")
        elif isinstance(content, str):
            text = _strip_system_xml(content)
            if text:
                parts.append(smart_truncate(text, truncate))
        return "  ".join(parts) if parts else ""


class MetaEntry(BaseTranscriptEntry):
    """System-injected messages — skill loads, command caveats (isMeta=True)."""
    type: Literal["user"]
    message: UserMessageModel
    isMeta: Literal[True] = True
    toolUseResult: Optional[ToolUseResult] = None

    @property
    def origin(self) -> UserOrigin:
        return UserOrigin.meta


class AssistantTranscriptEntry(BaseTranscriptEntry):
    """Assistant response with content blocks."""
    type: Literal["assistant"]
    message: AssistantMessageModel
    requestId: Optional[str] = None

    def display(
        self,
        truncate: int,
        hide: frozenset[str] = frozenset(),
    ) -> str:
        parts: list[str] = []

        # Thinking — shown unless 'thinking' is hidden
        if "thinking" not in hide:
            for item in self.message.content:
                if isinstance(item, ThinkingContent) and item.thinking.strip():
                    parts.append(f"[thinking] {smart_truncate(item.thinking.strip(), truncate)}")

        # Text — always shown
        text = extract_text(self)
        if text:
            parts.append(smart_truncate(text, truncate))

        # Tool inputs — shown unless 'inputs' is hidden
        if "inputs" not in hide:
            tool_summaries: list[str] = []
            for item in self.message.content:
                if isinstance(item, ToolUseContent):
                    detail = format_tool_input(item.name, item.input, truncate=truncate)
                    tool_summaries.append(f"→ {item.name}({detail})")
            if tool_summaries:
                parts.append("  ".join(tool_summaries))

        return "  ".join(parts) if parts else ""


class SummaryTranscriptEntry(BaseModel):
    """Context compaction summary."""
    type: Literal["summary"]
    summary: str
    leafUuid: PrefixId
    cwd: Optional[str] = None
    sessionId: Optional[PrefixId] = None


class SystemTranscriptEntry(BaseTranscriptEntry):
    """System messages — warnings, notifications, hook summaries.

    Two subtypes carry timing/orchestration data the harness computes for us:
      - subtype="turn_duration": durationMs is the wall-clock the agent spent
        on the just-finished turn (prompt → idle), and messageCount is how many
        messages that turn produced. Emitted only on *clean* turn completion —
        an interrupted turn produces none, so durationMs undercounts agent-active
        time on its own and must be cross-checked against timestamp deltas.
      - subtype="away_summary": content prose summarizing what happened while
        the human was idle — the harness's own "user walked away here" marker.
    """
    type: Literal["system"]
    content: Optional[str] = None
    subtype: Optional[str] = None
    level: Optional[str] = None
    durationMs: Optional[int] = None
    messageCount: Optional[int] = None
    hasOutput: Optional[bool] = None
    hookErrors: Optional[list[str]] = None
    hookInfos: Optional[list[dict[str, Any]]] = None
    preventedContinuation: Optional[bool] = None

    @property
    def turn_duration_ms(self) -> Optional[int]:
        """durationMs when this is a turn_duration marker, else None."""
        return self.durationMs if self.subtype == "turn_duration" else None


class QueueOperationTranscriptEntry(BaseModel):
    """Queue operations for message queueing tracking."""
    type: Literal["queue-operation"]
    operation: Literal["enqueue", "dequeue", "remove", "popAll"]
    timestamp: datetime
    sessionId: PrefixId
    content: Optional[Union[list[ContentItem], str]] = None


class ProgressEntry(BaseModel):
    """Streaming progress records — bulk of subagent files, always skipped."""
    type: Literal["progress"]
    model_config = {"extra": "allow"}


class FileSnapshotEntry(BaseModel):
    """File history snapshot records."""
    type: Literal["file-history-snapshot"]
    model_config = {"extra": "allow"}


# The union of all entry types the parser can produce
TranscriptEntry = Union[
    HumanEntry,
    ToolResultEntry,
    MetaEntry,
    AssistantTranscriptEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    QueueOperationTranscriptEntry,
    ProgressEntry,
    FileSnapshotEntry,
]


# Tool names that dispatch agents (foreground or background)
AGENT_TOOL_NAMES = {"Agent", "Task", "TaskCreate"}


@dataclass
class CompactionEvent:
    """A detected context compaction in a transcript."""

    turn: int
    from_tokens: int
    to_tokens: int
    drop_pct: float


@dataclass
class TranscriptStats:
    """Summary stats computed from a list of transcript entries.

    Works on any transcript — main sessions and subagent .output files
    share the same JSONL format.

    context_tokens: last assistant turn's input (actual context window size)
    input_tokens: total input across all turns
    output_tokens: total output across all turns (new tokens generated)
    duration_ms: elapsed time from first to last entry timestamp
    compaction_events: detected context window compactions (>30% drop from peak)
    """

    context_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_use_count: int = 0
    agent_count: int = 0
    duration_ms: Optional[int] = None
    compaction_events: list[CompactionEvent] = field(default_factory=list)

    @classmethod
    def from_entries(cls, entries: list[TranscriptEntry]) -> TranscriptStats:
        stats = cls()
        peak_context = 0
        prev_context = 0
        turn_num = 0
        first_ts: Optional[datetime] = None
        last_ts: Optional[datetime] = None

        for entry in entries:
            # Track timestamps from typed entries (not getattr)
            if isinstance(entry, (BaseTranscriptEntry, QueueOperationTranscriptEntry)):
                if first_ts is None:
                    first_ts = entry.timestamp
                last_ts = entry.timestamp

            if not isinstance(entry, AssistantTranscriptEntry):
                continue
            usage = entry.message.usage
            if usage:
                turn_num += 1
                turn_input = (
                    usage.input_tokens
                    + usage.cache_creation_input_tokens
                    + usage.cache_read_input_tokens
                )
                if turn_input > 0:
                    stats.context_tokens = turn_input  # overwrite — last real turn wins
                stats.input_tokens += turn_input
                stats.output_tokens += usage.output_tokens

                # Compaction detection: context drops >30% from peak
                if turn_input > peak_context:
                    peak_context = turn_input
                if prev_context > 10000 and turn_input < prev_context * 0.7:
                    drop_pct = (1 - turn_input / prev_context) * 100
                    stats.compaction_events.append(CompactionEvent(
                        turn=turn_num,
                        from_tokens=prev_context,
                        to_tokens=turn_input,
                        drop_pct=drop_pct,
                    ))
                prev_context = turn_input

            for item in entry.message.content:
                if isinstance(item, ToolUseContent):
                    stats.tool_use_count += 1
                    if item.name in AGENT_TOOL_NAMES:
                        stats.agent_count += 1

        # Duration from first to last timestamp — datetime math, no parsing
        if first_ts and last_ts and first_ts != last_ts:
            stats.duration_ms = int((last_ts - first_ts).total_seconds() * 1000)

        return stats


# =============================================================================
# Text extraction (moved from parser.py)
# =============================================================================


def _strip_system_xml(text: str) -> str:
    """Remove system/metadata XML from raw string messages.

    Keeps meaningful content inside <result> tags.
    """
    text = re.sub(r"<usage>[\s\S]*?</usage>", "", text)
    text = re.sub(r"</?task-notification>", "", text)
    text = re.sub(r"<task-id>[^<]*</task-id>", "", text)
    text = re.sub(r"<tool-use-id>[^<]*</tool-use-id>", "", text)
    text = re.sub(r"<status>[^<]*</status>", "", text)
    text = re.sub(r"<summary>[^<]*</summary>", "", text)
    text = re.sub(r"</?result>", "", text)
    text = re.sub(r"<system-reminder>[\s\S]*?</system-reminder>", "", text)
    text = re.sub(
        r"</?(?:command-name|command-message|command-args|local-command-stdout|"
        r"local-command-caveat|user-prompt-submit-hook)>[^<]*",
        "",
        text,
    )
    text = re.sub(r"Full transcript available at:.*", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(entry: Union[HumanEntry, AssistantTranscriptEntry]) -> str:
    """Extract readable text from an entry's content blocks.

    Joins TextContent.text values. Handles str | list[ContentItem] content.
    Strips system XML wrappers from raw string content.
    """
    content = entry.message.content

    if isinstance(content, str):
        return _strip_system_xml(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, TextContent):
            text = _strip_system_xml(item.text).strip()
            if text and not text.startswith("[Request interrupted by user"):
                parts.append(text)
    return "\n".join(parts)


_COMMAND_ARGS_RE = re.compile(r"<command-args>([\s\S]*?)</command-args>")
# A leading XML wrapper: an opening tag, its content, and the MATCHING close tag
# (paired via the \1 backreference so `<a>...</b>` never matches and no dangling
# `</tag>` survives). Stripped repeatedly by _strip_leading_xml while a wrapper
# remains at the front.
_LEADING_XML_RE = re.compile(r"^<(\w[\w-]*)\b[^>]*>[\s\S]*?</\1>\s*")


def _strip_leading_xml(body: str) -> str:
    """Strip leading matched-pair XML wrappers, bounded. Returns body unchanged
    if no leading wrapper is present."""
    text = body
    for _ in range(8):  # bounded: real prompts nest only a couple wrappers deep
        stripped = _LEADING_XML_RE.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return text


def _user_marker_text(entry: BaseTranscriptEntry) -> str:
    """Raw text of a user-role entry, for marker/sentinel classification.

    The single authoritative raw-text source for sentinel/teammate detection.
    Joins ALL TextContent blocks (the field-proven all-blocks semantics from the
    fleet-timeline prototype) with no system-XML stripping, so the teammate marker
    and interrupt sentinel — both of which live at the very front of the content —
    survive. The teammate marker check still works because `parse_teammate_message`
    lstrips and matches at the start. The parser normalizes user content to
    `[TextContent]`; we still handle a bare str for safety.
    """
    content = entry.message.content
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(b.text for b in content if isinstance(b, TextContent))
    return ""


def is_teammate_injected(entry: BaseTranscriptEntry) -> bool:
    """True if this user turn was injected by a teammate, not typed by the human.

    A trivial view over `HumanEntry.teammate_message`: an agent-team
    orchestrator/peer DMs a worker's pane by writing a user-role turn whose
    content opens with `<teammate-message ...>`. Only HumanEntry is considered —
    ToolResultEntry and MetaEntry are never teammate DMs.

    These injected turns are the orchestration protocol, not human attention:
    callers must not count them as human turns, interrupts, or opening/closing
    candidates.
    """
    return isinstance(entry, HumanEntry) and entry.teammate_message is not None


def substantive_human_text(entry: HumanEntry) -> str:
    """The substantive prompt text of a human turn, or '' for pure scaffolding.

    A human turn often carries no real prompt — a bare slash command
    (`<command-name>/clear</command-name>`), a `<local-command-stdout>` echo,
    caveat boilerplate, or an interrupt sentinel. `extract_text` already strips
    all of that to '', which is the signal "skip this turn".

    The one case where a command turn DOES carry intent is `<command-args>` with
    real text (`/wrapup just fyi -- ...`): the args are the user's actual words,
    so we recover them rather than discarding the turn. Leading skill/command XML
    wrappers around an otherwise-real prompt are also stripped.

    A teammate-injected turn (`<teammate-message ...>`) carries no human prompt —
    it is a peer/orchestrator DMing this worker's pane — so it is never
    substantive. A worker session's opening/closing therefore lands on a genuine
    human turn if one exists, else stays null.

    This is the single source of truth for "what did the human actually say in
    this turn" — `session_title` and the activity timeline's opening/closing both
    route through it so they agree on what counts as substance.
    """
    if is_teammate_injected(entry):
        return ""
    body = extract_text(entry).strip()
    if body:
        # Strip leading skill/command XML wrappers if a real prompt follows them.
        stripped = _strip_leading_xml(body).strip()
        return stripped or body

    # extract_text came back empty (command scaffolding / noise). Recover real
    # user text carried in <command-args>, if any.
    raw = entry.message.content
    if not isinstance(raw, str):
        raw = " ".join(
            b.text for b in raw if isinstance(b, TextContent)
        )
    args = " ".join(m.group(1).strip() for m in _COMMAND_ARGS_RE.finditer(raw))
    return args.strip()


def extract_thinking_text(entry: AssistantTranscriptEntry) -> str:
    """Extract thinking-block text from an assistant entry. Empty string if none."""
    parts: list[str] = []
    for item in entry.message.content:
        if isinstance(item, ThinkingContent):
            text = item.thinking.strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def extract_output_text(entry: "ToolResultEntry") -> str:
    """Extract raw searchable text from a tool result entry's content.

    Walks message.content ToolResultContent items. Unlike display(), this is
    untruncated and unformatted — intended for search and centered-excerpt
    extraction, not for direct rendering.
    """
    content = entry.message.content
    if isinstance(content, str):
        return content
    return "\n".join(block.text for block in entry.results)


# =============================================================================
# Tool call pairing — the tool_use ↔ tool_result relation
# =============================================================================


@dataclass
class ToolCall:
    """One tool invocation paired with the result it got back.

    `result` is None when the transcript holds no matching tool_result — the
    call was cut off, or the transcript ends mid-flight. That is distinct from
    a result that came back empty, so callers must not conflate them.
    """

    name: str  # full tool name, e.g. mcp__server__do_thing
    input: dict[str, Any]
    timestamp: Optional[datetime] = None
    result: Optional[ToolResultContent] = None

    @property
    def short_name(self) -> str:
        """Display name — the segment after the last `__` for MCP tools."""
        return self.name.split("__")[-1]

    @property
    def failure(self) -> Optional[FailureKind]:
        """This call's FailureKind, or None if it succeeded or never returned."""
        return self.result.failure if self.result is not None else None


def collect_tool_calls(entries: list[TranscriptEntry]) -> list[ToolCall]:
    """Pair every tool_use in a transcript with its tool_result.

    THE walk behind every tool-outcome question — the single-session audit and
    the corpus-wide failure survey both build on it, so the pairing rules live
    in one place. Returns calls in tool_use order (results arrive later in the
    file and are backfilled), including calls that never got a result.
    """
    calls: list[ToolCall] = []
    pending: dict[str, ToolCall] = {}

    for entry in entries:
        if isinstance(entry, AssistantTranscriptEntry):
            for item in entry.message.content:
                if not isinstance(item, ToolUseContent):
                    continue
                call = ToolCall(
                    name=item.name,
                    input=item.input,
                    timestamp=entry.timestamp,
                )
                pending[item.id.full] = call
                calls.append(call)
        elif isinstance(entry, ToolResultEntry):
            for block in entry.results:
                call = pending.pop(block.tool_use_id.full, None)
                if call is not None:
                    call.result = block

    return calls


# =============================================================================
# Tool input summarization (moved from formatting.py)
# =============================================================================


def format_tool_input(name: str, inp: dict[str, Any], truncate: int = 80) -> str:
    """Format a tool's input for display.

    truncate controls detail level (0 = full input, N = cap at N chars).
    Per-tool-name logic picks the most relevant field to show;
    truncate controls how much of it is visible.
    """
    import json

    if truncate == 0:
        # Full input — JSON-format the entire input dict
        return json.dumps(inp, indent=2, default=str)

    if name == "Read" and "file_path" in inp:
        return smart_truncate(inp["file_path"], truncate)
    if name in ("navigate", "WebFetch") and "url" in inp:
        return smart_truncate(inp["url"], truncate)
    if name == "javascript_tool" and "text" in inp:
        return smart_truncate(inp["text"], truncate)
    if name == "Grep" and "pattern" in inp:
        s = f"/{inp['pattern']}/"
        if "path" in inp:
            s += f" {inp['path']}"
        return smart_truncate(s, truncate)
    if name == "Glob" and "pattern" in inp:
        s = inp["pattern"]
        if "path" in inp:
            s += f" in {inp['path']}"
        return smart_truncate(s, truncate)
    if name == "Edit" and "file_path" in inp:
        return smart_truncate(inp["file_path"], truncate)
    if name == "Write" and "file_path" in inp:
        return smart_truncate(inp["file_path"], truncate)
    if name == "Bash" and "command" in inp:
        return smart_truncate(inp["command"], truncate)
    # Default: stringify and truncate
    return smart_truncate(str(inp), truncate)
