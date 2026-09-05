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

from .utils import PrefixId, collapse_ws, smart_truncate


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
# under noise. `subagents.looks_like_no_match` asks a separate, narrower
# question — see the note there.
#
# Kinds are matched in order, first hit wins, against a leading window of the
# whitespace-normalized result text (see FAILURE_SCAN_CHARS). Order matters:
# specific content rules run BEFORE `exit_code`, so a Bash failure is reported
# as what actually went wrong (git_rejected, parse_error, bad_path) rather than
# collapsing half the corpus into one uninformative bucket.
#
# Rules are matched against a WINDOW, not a line, so every alternative has to
# survive being embedded in a wall of other text. Two failure modes to check
# whenever a rule is added:
#   - Unanchored uppercase error codes substring-match ordinary words under
#     `re.I`. A bare `ENOTFOUND` matches inside `ModuleNotFoundError` and
#     `FileNotFoundError`; that one mistake put 96 Python tracebacks into the
#     175-member `network` bucket — the exact bucket a surveyor is told to
#     trust. Every such token is `\b`-anchored.
#   - Bare English phrases match anything that quotes them. A bare
#     `does not exist` pulled 244 Postgres `column "x" does not exist` errors
#     into `bad_path` (category `agent`), filing environment breakage under the
#     category surveyors are told to filter OUT.
# The negative-collision table in tests/test_failures.py pins both.


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
        # Total by construction — a kind missing from the map is a bug, and
        # test_every_kind_has_a_category asserts membership rather than letting
        # it silently render as "unclassified" to callers.
        return _FAILURE_CATEGORIES[self]


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
    # `agent`, not `environment`: sampling all 163 in the live corpus, these are
    # overwhelmingly code the AGENT itself wrote and got wrong — python -c
    # heredocs with broken f-string quoting, malformed jq filters, injected JS
    # with `await` outside an async function. A JSONDecodeError on someone
    # else's API response would be environment, but that is the rare case.
    FailureKind.parse_error: FailureCategory.agent,
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
    # ANCHORED to the head of the window: the harness emits this as the entire
    # result, and all 1246 in the live corpus start within the first characters.
    # Unanchored, any result that merely QUOTES the phrase gets suppressed —
    # a real path in a repo whose whole job is grepping transcripts.
    (FailureKind.cascade, re.compile(
        r"^(?:<tool_use_error>\s*)?"
        r"(?:Sibling tool call errored|Cancelled: parallel tool call)", re.I)),
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
    # as a network fact rather than a generic timeout. The bare error codes are
    # `\b`-anchored: unanchored under re.I, `ENOTFOUND` matches inside
    # `ModuleNotFoundError` / `FileNotFoundError`, and since this rule runs
    # before bad_path a real FileNotFoundError could never reach it.
    (FailureKind.network, re.compile(
        r"ssh: connect to host|Connection refused|Could not resolve host|"
        r"Network is unreachable|No route to host|\bECONNREFUSED\b|\bENOTFOUND\b", re.I)),
    (FailureKind.auth_failed, re.compile(
        r"Permission denied \(publickey|Permission denied, please try again|"
        r"authentication token|AADSTS\d+|401 Unauthorized|Bad credentials|"
        r"Authentication failed", re.I)),
    (FailureKind.http_status, re.compile(
        r"status code [45]\d\d|HTTP (?:error )?[45]\d\d|\b[45]\d\d (?:Forbidden|Not Found|"
        r"Bad Request|Internal Server Error)", re.I)),
    (FailureKind.timeout, re.compile(
        r"timed out after|timeout of \d+\s*ms exceeded|Connection timed out|"
        r"\bETIMEDOUT\b", re.I)),
    (FailureKind.command_not_found, re.compile(
        r"command not found|\bsh: \d+: [^:]+: not found", re.I)),
    (FailureKind.git_rejected, re.compile(
        r"Note about fast-forwards|! \[rejected\]|cannot pull with rebase|"
        r"Not possible to fast-forward|non-fast-forward", re.I)),
    (FailureKind.parse_error, re.compile(
        r"JSONDecodeError|jq: error|SyntaxError|Expecting value: line|ParserError", re.I)),
    # "does not exist" needs a filesystem noun in front of it. Bare, it is the
    # standard phrasing of every SQL error too — `column "x" does not exist`,
    # `relation "y" does not exist` — which is environment breakage, not a bad
    # path. The gap allows a long quoted path between the noun and the phrase
    # (`Path "/Users/.../worktrees/foo" does not exist`) but only ONE token, so
    # a filesystem word elsewhere in the window can't reach across a sentence.
    (FailureKind.bad_path, re.compile(
        r"File does not exist|\bEISDIR\b|\bENOENT\b|no such file or directory|"
        r"\b(?:file|directory|folder|path|cwd|working directory)\b"
        r"(?:\s+\S{0,200})?\s+does not exist", re.I)),
    # Late catch-all: a Bash command that failed for a reason no rule named.
    (FailureKind.exit_code, re.compile(r"^Exit code \d+", re.I)),
)


def classify_failure(text: str) -> FailureKind:
    """Classify already-known-failed result text into a FailureKind.

    Does NOT decide whether something failed — the caller has already read the
    `is_error` flag. Returns `unclassified` when no rule matches, which is the
    signal a surveyor actually wants: breakage the taxonomy has not seen.
    """
    window = collapse_ws(text, FAILURE_SCAN_CHARS)
    if not window:
        return FailureKind.unclassified
    for kind, rx in _FAILURE_RULES:
        if rx.search(window):
            return kind
    return FailureKind.unclassified


def parse_kinds(value: str | None) -> list[FailureKind] | None:
    """Parse a comma-separated FailureKind filter. None/"" → None ("all kinds").

    Same split as `parse_hide`: the pure parser raises ValueError and lives
    beside the type it parses; the MCP boundary wraps it in a ToolError.
    """
    if not value or not value.strip():
        return None
    valid = {k.value for k in FailureKind}
    atoms = [a.strip() for a in value.split(",") if a.strip()]
    invalid = [a for a in atoms if a not in valid]
    if invalid:
        raise ValueError(
            f"Unknown failure kind(s): {', '.join(invalid)}. "
            f"Valid: {', '.join(sorted(valid))}."
        )
    return [FailureKind(a) for a in atoms]


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
    def has_failure(self) -> bool:
        """Whether this result failed — the GATE, with no classification cost.

        `failure` runs the whole rule table on every call (it is a property, so
        nothing is cached); callers that only need the yes/no — errors_only's
        per-pattern filter, the audit walk — must ask this instead.
        """
        return bool(self.is_error)

    @property
    def failure(self) -> Optional[FailureKind]:
        """This result's FailureKind, or None when the call succeeded.

        `is_error` is the gate — see the failure-taxonomy note above for why
        prose is never used to decide failure, only to classify it.
        """
        if not self.has_failure:
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
    def has_failure(self) -> bool:
        """Whether any result block in this entry failed. The cheap predicate —
        `errors_only` asks this once per entry per pattern, so it must not run
        the rule table. Use `failure` when you need the kind."""
        return any(block.has_failure for block in self.results)

    @property
    def failure(self) -> Optional[FailureKind]:
        """The kind of the first failed result block, or None if none failed.

        The entry-level view of `ToolResultContent.failure`.
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

    @property
    def compaction_count(self) -> int:
        """How many compactions this transcript went through."""
        return len(self.compaction_events)

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

                # Compaction detection: context drops >30% from peak.
                # Zero-usage turns are excluded, same as for context_tokens above:
                # a `<synthetic>` placeholder (API error, interrupt) reports an
                # all-zero usage block, which is not a context drop. Left
                # unguarded such a turn both fabricates a 100% "compaction" and
                # zeroes the baseline, masking the next real one.
                if turn_input > peak_context:
                    peak_context = turn_input
                if turn_input > 0:
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


def _user_marker_text(entry: HumanEntry | ToolResultEntry) -> str:
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


def split_tool_name(name: str) -> tuple[Optional[str], str]:
    """Split a recorded tool name into (server, tool).

    An MCP tool is recorded as `mcp__<server>__<tool>`; a built-in tool is
    recorded bare, and so — occasionally — is an MCP tool, when the model
    reached it through the deferred-tool path (`search_projects` with no
    prefix). `server` is None for both bare cases: the name simply carries no
    server, which is a fact callers have to reconcile, not one this can invent.

    Splits at most twice, so a tool whose own name contains `__` survives whole.
    """
    if name.startswith("mcp__"):
        parts = name.split("__", 2)
        if len(parts) == 3 and parts[1] and parts[2]:
            return parts[1], parts[2]
    return None, name


def canonical_server(server: str) -> str:
    """Fold the spellings the harness gives ONE server into a single key.

    A server's configured name is sanitized into the tool name, and the
    sanitization has changed: the live corpus holds both
    `plugin_project-mining_cc-explorer` and `plugin_project_mining_cc_explorer`
    for the same server, and both `claude-in-chrome` and `Claude_in_Chrome`.
    Treating those as different servers splits one tool's stats in two. Names
    that differ only in `-` vs `_` or in case are the same server; a config
    holding `foo-bar` AND `foo_bar` as genuinely different servers is not a
    thing anyone does, and would only cost their rows a merge.
    """
    return server.replace("-", "_").casefold()


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
        """Display name — the tool half of `mcp__server__tool`, else the name."""
        return split_tool_name(self.name)[1]

    @property
    def has_failure(self) -> bool:
        """Whether this call failed — the gate, without classifying it."""
        return self.result is not None and self.result.has_failure

    @property
    def failure(self) -> Optional[FailureKind]:
        """This call's FailureKind, or None if it succeeded or never returned."""
        return self.result.failure if self.result is not None else None


def collect_tool_calls(entries: list[TranscriptEntry]) -> list[ToolCall]:
    """Pair every tool_use in a transcript with its tool_result.

    THE walk behind every tool-outcome question — the single-session audit and
    the corpus-wide failure survey both build on it, so the pairing rules live
    in one place. Returns calls in tool_use order, including calls that never
    got a result.

    TWO passes, because file order is not causal order. `tool_use_id` is the
    authoritative link; line position is not. JSONL lines flush out of order,
    and in the live corpus 740 of 258k tool_result blocks are written BEFORE
    the tool_use they answer. A single forward pass silently drops every one of
    them — the call reads as "never returned", so its failure disappears from
    the survey while errors_only still finds it, and the two halves of the
    feature disagree.
    """
    results: dict[str, ToolResultContent] = {}
    for entry in entries:
        if isinstance(entry, ToolResultEntry):
            for block in entry.results:
                # First result wins: a retried id would otherwise let a later
                # block overwrite the outcome the model actually saw.
                results.setdefault(block.tool_use_id.full, block)

    calls: list[ToolCall] = []
    for entry in entries:
        if not isinstance(entry, AssistantTranscriptEntry):
            continue
        for item in entry.message.content:
            if not isinstance(item, ToolUseContent):
                continue
            calls.append(
                ToolCall(
                    name=item.name,
                    input=item.input,
                    timestamp=entry.timestamp,
                    result=results.get(item.id.full),
                )
            )

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
