"""Tests for the failure axis: classification, the shared tool-call walk, the
corpus-wide survey, and search's errors_only filter.

Two invariants carry most of the weight here:

- `is_error` is the GATE. Prose never promotes a successful result to a
  failure — measured against the live corpus, a marker heuristic fires on
  ~1400 successful results that merely CONTAIN "not found"/"no matches"
  (npm build logs, ssh banners, grep output). Several tests pin that.
- Cascade artifacts are suppressed by default. One real failure in a parallel
  batch cancels its siblings and each cancellation is recorded as its own
  error, so leaving them in multiplies that batch's apparent failure count.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from cc_explorer.corpus import Corpus, ScannerError, SessionRef
from cc_explorer.failures import survey_failures
from cc_explorer.models import (
    _FAILURE_CATEGORIES,
    _FAILURE_RULES,
    FAILURE_SCAN_CHARS,
    FailureCategory,
    FailureKind,
    ToolResultContent,
    ToolResultEntry,
    classify_failure,
    collect_tool_calls,
    parse_kinds,
)
from cc_explorer.parser import load_transcript
from cc_explorer.responses import SurveyFailuresResponse
from cc_explorer.search import SessionInfo, triage
from cc_explorer.subagents import looks_like_no_match
from cc_explorer.utils import PrefixId

SID_A = "aaaaaaaa-1111-2222-3333-444444444444"
SID_B = "bbbbbbbb-1111-2222-3333-444444444444"
AGENT_ID = "a" + "f" * 16
TS = datetime(2026, 6, 15, 10, 30, 0, tzinfo=timezone.utc)


# =============================================================================
# JSONL builders — raw dicts so the files go through the real parser
# =============================================================================


def _assistant(tool: str, tool_id: str, inp: dict, ts=TS, session=SID_A, uuid=None):
    return {
        "type": "assistant",
        "uuid": uuid or f"a{tool_id}0000-0000-0000-0000-000000000000"[:36],
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "sessionId": session,
        "message": {
            "id": "m1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-5",
            "content": [
                {"type": "tool_use", "id": tool_id, "name": tool, "input": inp}
            ],
        },
    }


def _result(tool_id: str, text: str, is_error=False, ts=TS, session=SID_A, uuid=None):
    return {
        "type": "user",
        "uuid": uuid or f"c{tool_id}0000-0000-0000-0000-000000000000"[:36],
        "timestamp": ts.isoformat().replace("+00:00", "Z"),
        "sessionId": session,
        "toolUseResult": {},
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": [{"type": "text", "text": text}],
                    "is_error": is_error,
                }
            ],
        },
    }


def _call(tool: str, tool_id: str, text: str, is_error=False, ts=TS, session=SID_A):
    """A tool_use plus the tool_result it got back."""
    return [
        _assistant(tool, tool_id, {"command": "x"}, ts=ts, session=session),
        _result(tool_id, text, is_error=is_error, ts=ts, session=session),
    ]


def _write(enc: Path, sid: str, lines: list[dict], project="/fake") -> SessionRef:
    enc.mkdir(parents=True, exist_ok=True)
    path = enc / f"{sid}.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return SessionRef(session_id=PrefixId(sid), path=path, project_path=project)


def _write_agent(ref: SessionRef, agent_id: str, lines: list[dict]) -> Path:
    subdir = ref.path.with_suffix("") / "subagents"
    subdir.mkdir(parents=True, exist_ok=True)
    path = subdir / f"agent-{agent_id}.jsonl"
    path.write_text("".join(json.dumps(x) + "\n" for x in lines))
    return path


@pytest.fixture
def corpus_of(monkeypatch):
    """Point survey_failures at a hand-built Corpus instead of ~/.claude."""

    def install(refs: list[SessionRef]):
        monkeypatch.setattr(
            Corpus, "discover", classmethod(lambda cls, projects=None: Corpus(list(refs)))
        )

    return install


# =============================================================================
# classify_failure — the taxonomy
# =============================================================================


@pytest.mark.parametrize(
    "text,expected",
    [
        # Cascade must win over everything — it is checked first on purpose.
        ("<tool_use_error>Sibling tool call errored</tool_use_error>", FailureKind.cascade),
        ("<tool_use_error>Cancelled: parallel tool call Bash(ls)</tool_use_error>", FailureKind.cascade),
        ("The user doesn't want to proceed with this tool use.", FailureKind.user_rejected),
        ("Permission for this action was denied by the Claude user", FailureKind.user_rejected),
        ("Permission to use Bash has been auto-denied (prompts are disabled)", FailureKind.permission_denied),
        ("<tool_use_error>File has not been read yet. Read it first</tool_use_error>", FailureKind.stale_read),
        ("<tool_use_error>File has been modified since read</tool_use_error>", FailureKind.stale_read),
        ("File content (30070 tokens) exceeds maximum allowed tokens (25000). Use offset", FailureKind.oversized_read),
        ("<tool_use_error>String to replace not found in file.</tool_use_error>", FailureKind.edit_no_match),
        ("<tool_use_error>InputValidationError: Read failed</tool_use_error>", FailureKind.bad_call),
        ("1 validation error for call[search_project] patterns Input should be a list", FailureKind.bad_call),
        ("File does not exist. Note: your current working directory is /x", FailureKind.bad_path),
        ("EISDIR: illegal operation on a directory, read '/x'", FailureKind.bad_path),
        ("Request failed with status code 403", FailureKind.http_status),
        ("timeout of 60000ms exceeded", FailureKind.timeout),
        ("Exit code 1 Command timed out after 2m 0s", FailureKind.timeout),
        # Content rules beat the exit_code catch-all, so a Bash failure reports
        # what actually went wrong rather than collapsing into one bucket.
        ("Exit code 1 ssh: connect to host wigglebutt port 22: Connection refused", FailureKind.network),
        ("Exit code 255 Permission denied (publickey,password).", FailureKind.auth_failed),
        ("Exit code 1 hint: See the 'Note about fast-forwards' in git push --help", FailureKind.git_rejected),
        ("Exit code 1 json.decoder.JSONDecodeError: Expecting value", FailureKind.parse_error),
        ("Exit code 1 (eval):1: command not found: uv", FailureKind.command_not_found),
        # No rule names it → exit_code, the honest "a command failed" fallback.
        ("Exit code 1 ====== test session starts ======", FailureKind.exit_code),
        ("Exit code 2", FailureKind.exit_code),
        # Nothing at all matched: the yield, not a dumping ground.
        ("<tool_use_error>This agent is isolated in the worktree /x</tool_use_error>", FailureKind.unclassified),
        ("", FailureKind.unclassified),
    ],
)
def test_classify_failure(text, expected):
    assert classify_failure(text) is expected


def test_auth_message_is_not_mistaken_for_a_malformed_call():
    """`missing required scopes` is an auth failure. The bad_call rule is
    deliberately narrow so it can't swallow it."""
    text = "error: your authentication token is missing required scopes"
    assert classify_failure(text) is FailureKind.auth_failed


def test_classification_reads_only_a_leading_window():
    """A phrase buried in a wall of otherwise-fine output must not reclassify
    the result — otherwise any long Bash log could be labeled anything."""
    text = "Exit code 1 " + ("filler " * 4000) + "Connection refused"
    assert classify_failure(text) is FailureKind.exit_code


def test_every_kind_has_a_category():
    """Membership, not `isinstance(kind.category, FailureCategory)` — that form
    can't fail, and the hazard it means to catch (add a kind, forget the map, it
    silently renders as 'unknown' = "unclassified" to callers) would sail past."""
    missing = set(FailureKind) - set(_FAILURE_CATEGORIES)
    assert not missing, f"kinds with no category: {sorted(k.value for k in missing)}"
    assert FailureKind.stale_read.category is FailureCategory.agent
    assert FailureKind.network.category is FailureCategory.environment
    assert FailureKind.user_rejected.category is FailureCategory.policy
    assert FailureKind.cascade.category is FailureCategory.cascade
    assert FailureKind.unclassified.category is FailureCategory.unknown


def test_every_classified_kind_has_a_declared_rule():
    """Detect kinds absent from the rule table. This checks membership, not
    reachability: earlier rules can still shadow a later rule's examples."""
    ruled = {kind for kind, _ in _FAILURE_RULES}
    assert ruled == set(FailureKind) - {FailureKind.unclassified}


@pytest.mark.parametrize(
    "text,not_kind",
    [
        # #1: bare ENOTFOUND substring-matched these under re.I. 96 Python
        # tracebacks landed in a 175-member `network` bucket in the live corpus
        # — the exact bucket the docs tell surveyors to trust.
        ("Exit code 1 ModuleNotFoundError: No module named 'httpx'", FailureKind.network),
        ("Exit code 1 FileNotFoundError: [Errno 2] No such file", FailureKind.network),
        # #5: bare `does not exist` is also every SQL error's phrasing, and
        # bad_path is category `agent` — so environment breakage got filed
        # under the category surveyors are told to filter OUT. 244 of them.
        ('Exit code 1 ERROR: column "location_id" does not exist', FailureKind.bad_path),
        ('Exit code 1 ERROR: relation "slideshows" does not exist', FailureKind.bad_path),
        ("Exit code 1 ERROR: operator does not exist: text = integer", FailureKind.bad_path),
        # The cascade rule is anchored: a result that merely QUOTES the marker
        # is a real failure, not an artifact to suppress. This repo greps
        # transcripts for a living, so that path is not hypothetical.
        (
            "Exit code 1 grep: no match for 'Sibling tool call errored' in corpus",
            FailureKind.cascade,
        ),
    ],
)
def test_rules_do_not_collide_with_ordinary_text(text, not_kind):
    """Positive-examples-only classification tests are how #1 and #5 shipped."""
    assert classify_failure(text) is not not_kind


@pytest.mark.parametrize(
    "text,expected",
    [
        # A Python FileNotFoundError is a bad path — it just must not be
        # `network`, which ran first and swallowed it.
        (
            "Exit code 1 FileNotFoundError: [Errno 2] No such file or directory: '/x'",
            FailureKind.bad_path,
        ),
        # ...and a real bad path still classifies, including with a long quoted
        # path between the filesystem noun and the phrase.
        ("<tool_use_error>Path does not exist</tool_use_error>", FailureKind.bad_path),
        (
            'Path "/Users/x/projects/thing/.claude/worktrees/some-long-name-8fce27" '
            "does not exist",
            FailureKind.bad_path,
        ),
        # A real network failure is still a network failure.
        ("Error: getaddrinfo ENOTFOUND api.example.com", FailureKind.network),
        ("Error: connect ECONNREFUSED 127.0.0.1:5432", FailureKind.network),
    ],
)
def test_tightened_rules_still_classify_the_real_thing(text, expected):
    assert classify_failure(text) is expected


def test_classification_cost_is_bounded_by_the_window_not_the_input():
    """Reject normalize-before-slicing without a hardware timing threshold.

    The string probe bounds materialized slices and split input. The allowance
    permits normalization slack without freezing its exact factor.
    """
    budget = FAILURE_SCAN_CHARS * 16

    class BoundedText(str):
        def __getitem__(self, key):
            if isinstance(key, slice):
                assert len(range(*key.indices(len(self)))) <= budget
            return BoundedText(super().__getitem__(key))

        def split(self, *args, **kwargs):
            assert len(self) <= budget, "normalization scanned the entire tool result"
            return super().split(*args, **kwargs)

    huge = BoundedText("Exit code 1 " + ("some output line here " * 30_000))
    assert len(huge) > 500_000
    assert classify_failure(huge) is FailureKind.exit_code


def test_parse_kinds():
    assert parse_kinds(None) is None
    assert parse_kinds("  ") is None
    assert parse_kinds("network, timeout") == [FailureKind.network, FailureKind.timeout]
    with pytest.raises(ValueError, match="nonsense"):
        parse_kinds("network,nonsense")


# =============================================================================
# ToolResultContent — is_error is the gate
# =============================================================================


def _block(text: str, is_error=None) -> ToolResultContent:
    return ToolResultContent(
        type="tool_result",
        tool_use_id=PrefixId("t1"),
        content=[{"type": "text", "text": text}],
        is_error=is_error,
    )


def test_successful_result_is_never_a_failure_however_it_reads():
    """The measured false-positive case: successful command output containing
    error vocabulary. 1400+ such results exist in the live corpus."""
    noisy = "> app@0.1.0 build\nsh: 1: nc: not found\nbuild finished"
    assert _block(noisy).failure is None
    assert _block(noisy, is_error=False).failure is None
    # ...and the narrower zero-hit heuristic, which audit_session_tools uses,
    # still fires on it. That divergence is the point of keeping them separate.
    assert looks_like_no_match(noisy) is True


def test_flagged_result_is_classified():
    assert _block("Exit code 1", is_error=True).failure is FailureKind.exit_code


def test_result_text_flattens_both_content_shapes():
    assert _block("hello").text == "hello"
    bare = ToolResultContent(type="tool_result", tool_use_id=PrefixId("t1"), content="raw")
    assert bare.text == "raw"


# =============================================================================
# collect_tool_calls — the shared pairing walk
# =============================================================================


def test_collect_tool_calls_pairs_and_names(tmp_path):
    ref = _write(tmp_path / "enc", SID_A, _call("Bash", "t1", "ok"))
    calls = collect_tool_calls(load_transcript(ref.path))
    assert len(calls) == 1
    assert calls[0].name == "Bash"
    assert calls[0].result is not None
    assert calls[0].failure is None


def test_collect_tool_calls_shortens_mcp_names(tmp_path):
    ref = _write(
        tmp_path / "enc",
        SID_A,
        _call("mcp__plugin_project-mining_cc-explorer__grep_session", "t1", "{}"),
    )
    assert collect_tool_calls(load_transcript(ref.path))[0].short_name == "grep_session"


def test_unanswered_call_is_kept_but_has_no_failure(tmp_path):
    """A call cut off mid-flight is not a success and not a failure — callers
    must be able to tell "never returned" from "returned nothing"."""
    ref = _write(tmp_path / "enc", SID_A, [_assistant("Bash", "t1", {"command": "x"})])
    call = collect_tool_calls(load_transcript(ref.path))[0]
    assert call.result is None
    assert call.failure is None


def test_result_written_before_its_tool_use_is_still_paired(tmp_path):
    """`tool_use_id` is the causal link; line position is NOT. JSONL lines flush
    out of order — 740 of 258k tool_result blocks in the live corpus are written
    before the tool_use they answer. A single forward pass drops every one, so
    the failure vanishes from survey_failures while errors_only still finds it,
    and the two halves of the feature disagree about the same transcript."""
    ref = _write(
        tmp_path / "enc",
        SID_A,
        [
            _result("t1", "Exit code 1 boom", is_error=True),
            _assistant("Bash", "t1", {"command": "x"}),
        ],
    )
    calls = collect_tool_calls(load_transcript(ref.path))
    assert len(calls) == 1
    assert calls[0].result is not None, "result precedes its tool_use in the file"
    assert calls[0].failure is FailureKind.exit_code


def test_one_entry_can_answer_several_parallel_calls(tmp_path):
    """The models explicitly allow N tool_result blocks in one entry (that is
    what a parallel batch looks like); every one of them must be paired."""
    entry = {
        "type": "user",
        "uuid": "dddddddd-aaaa-bbbb-cccc-dddddddddddd",
        "timestamp": TS.isoformat().replace("+00:00", "Z"),
        "sessionId": SID_A,
        "toolUseResult": {},
        "message": {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": tid,
                    "content": [{"type": "text", "text": text}],
                    "is_error": err,
                }
                for tid, text, err in (
                    ("t1", "fine", False),
                    ("t2", "Exit code 1 boom", True),
                    ("t3", "<tool_use_error>File has not been read yet</tool_use_error>", True),
                )
            ],
        },
    }
    ref = _write(
        tmp_path / "enc",
        SID_A,
        [_assistant("Bash", f"t{i}", {"command": "x"}) for i in (1, 2, 3)] + [entry],
    )
    calls = collect_tool_calls(load_transcript(ref.path))
    assert [c.result is not None for c in calls] == [True, True, True]
    assert [c.failure for c in calls] == [
        None,
        FailureKind.exit_code,
        FailureKind.stale_read,
    ]


# =============================================================================
# survey_failures
# =============================================================================


def test_survey_counts_and_classifies(tmp_path, corpus_of):
    ref = _write(
        tmp_path / "enc",
        SID_A,
        _call("Bash", "t1", "Exit code 1", is_error=True)
        + _call("Read", "t2", "<tool_use_error>File has not been read yet</tool_use_error>", is_error=True)
        + _call("Bash", "t3", "all good"),
    )
    corpus_of([ref])

    s = survey_failures()
    assert s.total == 2
    assert s.sessions_affected == 1
    assert {k.kind for k in s.by_kind} == {FailureKind.exit_code, FailureKind.stale_read}
    by_tool = {t.display: t for t in s.by_tool}
    # Denominators count every call in the scanned transcript, not just failures.
    assert (by_tool["Bash"].errors, by_tool["Bash"].calls) == (1, 2)
    assert (by_tool["Read"].errors, by_tool["Read"].calls) == (1, 1)


def test_cascade_is_suppressed_by_default_and_recoverable(tmp_path, corpus_of):
    lines = _call("Bash", "t1", "Exit code 1", is_error=True)
    for i, tid in enumerate(("t2", "t3", "t4")):
        lines += _call(
            "Bash", tid, "<tool_use_error>Sibling tool call errored</tool_use_error>",
            is_error=True,
        )
    corpus_of([_write(tmp_path / "enc", SID_A, lines)])

    default = survey_failures()
    assert default.total == 1, "one real failure, not four"
    assert default.cascade_suppressed == 3
    assert FailureKind.cascade not in {k.kind for k in default.by_kind}

    folded = survey_failures(include_cascade=True)
    assert folded.total == 4
    assert folded.cascade_suppressed == 0


def test_kinds_filter_narrows_errors_but_not_denominators(tmp_path, corpus_of):
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call("Bash", "t1", "Exit code 1 Connection refused", is_error=True)
            + _call("Bash", "t2", "<tool_use_error>File has not been read yet</tool_use_error>", is_error=True)
            + _call("Bash", "t3", "fine"),
        )
    ])

    s = survey_failures(kinds=[FailureKind.network])
    assert s.total == 1
    assert [k.kind for k in s.by_kind] == [FailureKind.network]
    # `calls` measures exposure, so it stays whole even when errors are filtered.
    assert s.by_tool[0].calls == 3


def test_unclassified_groups_by_shape_and_ranks_by_recurrence(tmp_path, corpus_of):
    novel = "<tool_use_error>Frobnicator exploded in sector {}</tool_use_error>"
    lines = []
    for i in range(3):
        lines += _call("Bash", f"t{i}", novel.format("seven"), is_error=True)
    lines += _call("Bash", "t9", "<tool_use_error>A different novel thing</tool_use_error>", is_error=True)
    corpus_of([_write(tmp_path / "enc", SID_A, lines)])

    s = survey_failures()
    assert s.unclassified_total == 4
    assert [sh.count for sh in s.unclassified] == [3, 1]
    assert "Frobnicator" in s.unclassified[0].shape


def test_window_excludes_out_of_range_failures(tmp_path, corpus_of):
    old = TS - timedelta(days=30)
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call("Bash", "t1", "Exit code 1", is_error=True, ts=old)
            + _call("Bash", "t2", "Exit code 2", is_error=True, ts=TS),
        )
    ])

    assert survey_failures().total == 2
    assert survey_failures(after=TS - timedelta(days=1)).total == 1
    assert survey_failures(before=TS - timedelta(days=1)).total == 1


def test_survey_spans_subagent_bodies(tmp_path, corpus_of):
    ref = _write(tmp_path / "enc", SID_A, _call("Bash", "t1", "quiet main transcript"))
    _write_agent(ref, AGENT_ID, _call("Bash", "t9", "Exit code 1", is_error=True))
    corpus_of([ref])

    s = survey_failures()
    assert s.total == 1
    assert s.transcripts_scanned == 1, "the clean main transcript is never parsed"


def test_conversion_artifacts_are_not_double_counted(tmp_path, corpus_of):
    """A converted copy duplicates a transcript already in scope; counting it
    would inflate every failure the original recorded."""
    ref = _write(tmp_path / "enc", SID_A, _call("Bash", "t1", "Exit code 1", is_error=True))
    copy_lines = [
        {
            "type": "x-converter-provenance",
            "x_converter": {
                "v": 1,
                "from": {"kind": "session", "id": SID_A, "project": "/fake"},
                "lines_at_creation": 3,
            },
        },
    ] + _call("Bash", "t1", "Exit code 1", is_error=True)
    _write_agent(ref, AGENT_ID, copy_lines)
    corpus_of([ref])

    assert survey_failures().total == 1


def test_survey_is_empty_when_nothing_failed(tmp_path, corpus_of):
    corpus_of([_write(tmp_path / "enc", SID_A, _call("Bash", "t1", "fine"))])
    s = survey_failures()
    assert s.total == 0
    assert s.by_kind == [] and s.by_session == []


def test_kinds_cascade_is_an_answerable_query(tmp_path, corpus_of):
    """Cascade suppression used to run before the kinds check, so
    `kinds=['cascade']` returned total=0 at the default include_cascade — and
    the MCP layer then told the caller to widen the window, drop `kinds`, or
    drop `projects`, none of which was the problem."""
    lines = _call("Bash", "t1", "Exit code 1", is_error=True)
    for tid in ("t2", "t3"):
        lines += _call(
            "Bash", tid, "<tool_use_error>Sibling tool call errored</tool_use_error>",
            is_error=True,
        )
    corpus_of([_write(tmp_path / "enc", SID_A, lines)])

    s = survey_failures(kinds=[FailureKind.cascade])
    assert s.total == 2, "naming cascade in `kinds` IS the request for it"
    assert [k.kind for k in s.by_kind] == [FailureKind.cascade]
    assert s.cascade_suppressed == 0

    # ...and not naming it leaves the default suppression alone.
    other = survey_failures(kinds=[FailureKind.exit_code])
    assert other.total == 1
    assert other.cascade_suppressed == 2


def test_by_tool_does_not_merge_same_named_tools_from_different_servers(
    tmp_path, corpus_of
):
    """17 short names in the live corpus are shared by two or three MCP servers.
    Merging them blends the error_rate denominators, which the whole section
    exists to provide."""
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call("mcp__serverA__search", "t1", "Exit code 1", is_error=True)
            + _call("mcp__serverB__search", "t2", "fine")
            + _call("mcp__serverB__search", "t3", "fine")
            + _call("Bash", "t4", "Exit code 1", is_error=True),
        )
    ])

    rows = {t.display: t for t in survey_failures().by_tool}
    assert set(rows) == {"mcp__serverA__search", "Bash"}, "no blended `search` row"
    assert (rows["mcp__serverA__search"].errors, rows["mcp__serverA__search"].calls) == (1, 1)
    # Bash is unambiguous, so it keeps the short display name.
    assert rows["Bash"].display == "Bash"


def test_by_tool_folds_bare_and_qualified_recordings_of_one_tool(
    tmp_path, corpus_of
):
    """A transcript names the same MCP tool two ways: qualified on nearly every
    call, bare when the model reached it through the deferred-tool path. Keyed
    by the raw name that split one tool's stats across two rows — and the stray
    bare row made its qualified twin look like a cross-server collision, so it
    rendered fully qualified while sibling tools rendered short (issue #67)."""
    server = "mcp__plugin_project-mining_cc-explorer__"
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call(f"{server}search_projects", "t1", "Exit code 1", is_error=True)
            + _call(f"{server}search_projects", "t2", "fine")
            + _call("search_projects", "t3", "Exit code 1", is_error=True)
            + _call(f"{server}grep_session", "t4", "Exit code 1", is_error=True),
        )
    ])

    rows = {t.display: t for t in survey_failures().by_tool}
    assert set(rows) == {"search_projects", "grep_session"}, "one tool, one row"
    assert (rows["search_projects"].errors, rows["search_projects"].calls) == (2, 3)
    # ...and the alias no longer drags its neighbours into full-name display.
    assert rows["grep_session"].display == "grep_session"


def test_by_tool_folds_spelling_variants_of_one_server(tmp_path, corpus_of):
    """The harness sanitizes a server's configured name into the tool name, and
    has changed how: the corpus holds `plugin_project-mining_cc-explorer` and
    `plugin_project_mining_cc_explorer` for the same server. Punctuation drift
    is not a second server."""
    hyphen = "mcp__plugin_project-mining_cc-explorer__list_project_sessions"
    under = "mcp__plugin_project_mining_cc_explorer__list_project_sessions"
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call(hyphen, "t1", "Exit code 1", is_error=True)
            + _call(hyphen, "t2", "fine")
            + _call(under, "t3", "Exit code 1", is_error=True),
        )
    ])

    rows = survey_failures().by_tool
    assert [r.display for r in rows] == ["list_project_sessions"]
    assert (rows[0].errors, rows[0].calls) == (2, 3)


def test_bare_name_stays_its_own_row_when_two_servers_could_claim_it(
    tmp_path, corpus_of
):
    """Folding is attribution, and attribution needs one candidate. With two
    servers exposing `search`, a bare `search` belongs to neither by evidence,
    so it stays separate rather than inflating one server's denominator."""
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call("mcp__serverA__search", "t1", "Exit code 1", is_error=True)
            + _call("mcp__serverB__search", "t2", "Exit code 1", is_error=True)
            + _call("search", "t3", "Exit code 1", is_error=True),
        )
    ])

    rows = {t.display: t for t in survey_failures().by_tool}
    assert set(rows) == {"mcp__serverA__search", "mcp__serverB__search", "search"}
    assert all(r.errors == 1 and r.calls == 1 for r in rows.values())


def test_folding_never_merges_two_servers_exposing_the_same_tool(
    tmp_path, corpus_of
):
    """The fold must not become a prefix strip: same tool name, two servers,
    two rows — with a bare alias present to prove it does not act as a bridge
    that merges them."""
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call("mcp__serverA__search", "t1", "Exit code 1", is_error=True)
            + _call("mcp__serverA__search", "t2", "fine")
            + _call("mcp__serverB__search", "t3", "Exit code 1", is_error=True)
            + _call("search", "t4", "fine"),
        )
    ])

    rows = {t.display: t for t in survey_failures().by_tool}
    assert (rows["mcp__serverA__search"].errors, rows["mcp__serverA__search"].calls) == (1, 2)
    assert (rows["mcp__serverB__search"].errors, rows["mcp__serverB__search"].calls) == (1, 1)


def test_survey_total_equals_the_failed_result_blocks_in_the_corpus(
    tmp_path, corpus_of
):
    """Equivalence oracle. `survey_failures` (walks tool_use -> tool_result) and
    `errors_only` (walks ToolResultEntry directly) are two readers of the same
    fact; when they disagree, one of them is losing failures — which is exactly
    what the single-pass pairing bug did. Pin them together."""
    lines = (
        _call("Bash", "t1", "Exit code 1", is_error=True)
        + _call("Read", "t2", "<tool_use_error>File has not been read yet</tool_use_error>", is_error=True)
        + _call("Bash", "t3", "all good")
        # ...including the out-of-order pair the forward pass used to drop.
        + [
            _result("t4", "Exit code 2 boom", is_error=True),
            _assistant("Bash", "t4", {"command": "x"}),
        ]
    )
    ref = _write(tmp_path / "enc", SID_A, lines)
    corpus_of([ref])

    oracle = sum(
        1
        for entry in load_transcript(ref.path)
        if isinstance(entry, ToolResultEntry)
        for block in entry.results
        if block.has_failure
    )
    assert oracle == 3
    assert survey_failures(include_cascade=True).total == oracle


def test_scanner_failure_degrades_to_a_complete_scan(tmp_path, corpus_of, monkeypatch):
    """The prefilter is an optimization, never a correctness gate. When rg is
    missing or blows up the survey must still be COMPLETE — and must say it
    took the slow path, or a degraded run looks identical to a fast one."""
    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call("Bash", "t1", "Exit code 1", is_error=True) + _call("Bash", "t2", "fine"),
        )
    ])

    def boom(self, raw_patterns):
        raise ScannerError("rg exploded")

    monkeypatch.setattr(Corpus, "matching_files", boom)

    s = survey_failures()
    assert s.prefiltered is False
    assert s.total == 1, "degraded means slower, never fewer"
    assert s.transcripts_scanned == 1

    r = SurveyFailuresResponse.from_survey(s, limit=10, examples=False)
    assert r.degraded_prefilter is True
    assert r.model_dump()["degraded_prefilter"] is True


# =============================================================================
# Response rendering — lean default, explicit overflow
# =============================================================================


def _survey_with_many(tmp_path, corpus_of, n=8):
    lines = []
    for i in range(n):
        lines += _call("Bash", f"t{i}", f"<tool_use_error>novel thing {i}</tool_use_error>", is_error=True)
    corpus_of([_write(tmp_path / "enc", SID_A, lines)])
    return survey_failures()


def test_default_response_carries_no_error_text(tmp_path, corpus_of):
    """The first orienting call should cost counts, not transcripts."""
    r = SurveyFailuresResponse.from_survey(
        _survey_with_many(tmp_path, corpus_of), limit=10, examples=False
    )
    dumped = r.model_dump()
    assert all("example" not in row for row in dumped["by_kind"])
    assert all("example" not in row for row in dumped["unclassified"])


def test_examples_are_opt_in(tmp_path, corpus_of):
    r = SurveyFailuresResponse.from_survey(
        _survey_with_many(tmp_path, corpus_of), limit=10, examples=True
    )
    assert r.by_kind[0].example is not None
    assert "novel thing" in r.unclassified[0].example.text


def test_truncated_sections_say_so(tmp_path, corpus_of):
    """A silently truncated list reads as "that's all of them" and ends the
    investigation — every cap must announce itself."""
    r = SurveyFailuresResponse.from_survey(
        _survey_with_many(tmp_path, corpus_of, n=8), limit=3, examples=False
    )
    assert len(r.unclassified) == 3
    assert r.unclassified_overflow is not None
    assert "3 of 8" in r.unclassified_overflow


def test_no_overflow_hint_when_nothing_dropped(tmp_path, corpus_of):
    r = SurveyFailuresResponse.from_survey(
        _survey_with_many(tmp_path, corpus_of, n=2), limit=10, examples=False
    )
    assert r.unclassified_overflow is None
    assert "unclassified_overflow" not in r.model_dump()


# =============================================================================
# Corpus.matching_files — the raw-JSONL prefilter, file-granular
# =============================================================================


def test_matching_files_returns_only_the_files_that_hit(tmp_path):
    from cc_explorer.failures import ERROR_FLAG_PATTERN

    ref = _write(tmp_path / "enc", SID_A, _call("Bash", "t1", "fine"))
    agent = _write_agent(ref, AGENT_ID, _call("Bash", "t9", "boom", is_error=True))
    clean = _write(tmp_path / "enc", SID_B, _call("Bash", "t1", "fine", session=SID_B))

    got = Corpus([ref, clean]).matching_files([ERROR_FLAG_PATTERN])
    assert [r for r, _ in got] == [ref]
    assert got[0][1] == [agent], "the clean main transcript is not a candidate"


# =============================================================================
# errors_only — the search filter
# =============================================================================


def _session(ref: SessionRef) -> SessionInfo:
    info = SessionInfo.load(ref)
    assert info is not None
    return info


def test_errors_only_keeps_failed_results_and_drops_successful_ones(tmp_path):
    ref = _write(
        tmp_path / "enc",
        SID_A,
        _call("Bash", "t1", "ssh worked fine")
        + _call("Bash", "t2", "Exit code 255 ssh: Connection refused", is_error=True),
    )
    session = _session(ref)

    assert triage([session], "ssh", base_types=(), errors_only=True)[0].count == 1
    plain = triage([session], "ssh", base_types=(), errors_only=False)
    assert plain == [] or plain[0].count >= 2


def test_errors_only_ignores_conversation_text(tmp_path):
    """Only a tool result can fail, so prose that merely mentions the topic
    must not come back — that is the whole point of the filter."""
    lines = [
        {
            "type": "user",
            "uuid": "11111111-aaaa-bbbb-cccc-dddddddddddd",
            "timestamp": TS.isoformat().replace("+00:00", "Z"),
            "sessionId": SID_A,
            "message": {"role": "user", "content": [{"type": "text", "text": "ssh is broken"}]},
        }
    ] + _call("Bash", "t1", "ssh fine")
    session = _session(_write(tmp_path / "enc", SID_A, lines))

    assert triage([session], "ssh", errors_only=True) == []


def test_errors_only_finds_the_failure_regardless_of_role(tmp_path):
    """`role` selects sides of the conversation; a failure lives in neither,
    so errors_only must not silently return nothing for role='user'."""
    from cc_explorer.models import HumanEntry

    session = _session(
        _write(tmp_path / "enc", SID_A, _call("Bash", "t1", "Exit code 1 boom", is_error=True))
    )
    for base in ((HumanEntry,), ()):
        assert triage([session], "boom", base_types=base, errors_only=True)[0].count == 1


def test_errors_only_rejects_hiding_outputs():
    from fastmcp.exceptions import ToolError

    from cc_explorer.mcp_server import _check_errors_only

    _check_errors_only(False, frozenset({"outputs"}))  # fine when not filtering
    with pytest.raises(ToolError, match="incompatible"):
        _check_errors_only(True, frozenset({"outputs"}))


@pytest.mark.parametrize("role", ["user", "assistant", "all"])
def test_errors_only_context_always_reaches_the_call_that_failed(tmp_path, role):
    """The one turn that explains a failed tool result is the assistant tool_use
    that caused it. `errors_only` narrows the MATCH set to tool results but the
    context was still composed from `role` — so at the default role='user' the
    context was human turns only and the causing call was structurally
    unreachable. That is the exact workflow SKILL.md prescribes."""
    from cc_explorer.models import AssistantTranscriptEntry
    from cc_explorer.search import ENTRY_TYPE_MAP, search_multi

    lines = [
        {
            "type": "user",
            "uuid": "11111111-aaaa-bbbb-cccc-dddddddddddd",
            "timestamp": TS.isoformat().replace("+00:00", "Z"),
            "sessionId": SID_A,
            "message": {"role": "user", "content": [{"type": "text", "text": "go"}]},
        }
    ] + _call("Bash", "t1", "Exit code 255 ssh: Connection refused", is_error=True)
    session = _session(_write(tmp_path / "enc", SID_A, lines))

    out = search_multi(
        [session], ["ssh"], base_types=ENTRY_TYPE_MAP[role], context=2, errors_only=True
    )
    (_, hits, _), = out[session.session_id]
    assert len(hits) == 1
    assert any(
        isinstance(e, AssistantTranscriptEntry) for e in hits[0].context_before
    ), f"role={role}: the assistant tool_use that failed is not in context"


# =============================================================================
# survey_failures at the MCP registration layer
# =============================================================================


def _survey_tool():
    """The registered tool. `@mcp.tool` returns the plain function, so it is
    callable directly — this is the tool as an MCP client reaches it."""
    from cc_explorer import mcp_server

    return mcp_server.survey_failures


def test_mcp_survey_rejects_an_unknown_kind():
    from fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="Unknown failure kind"):
        _survey_tool()(kinds="network,not_a_kind")


def test_mcp_survey_zero_result_names_include_cascade_when_it_is_the_reason(
    tmp_path, corpus_of
):
    """"Widen the window, drop `kinds`, or omit `projects`" is a FALSE statement
    when cascades were found and suppressed — three remedies, none of them the
    actual one."""
    from fastmcp.exceptions import ToolError

    corpus_of([
        _write(
            tmp_path / "enc",
            SID_A,
            _call(
                "Bash", "t1",
                "<tool_use_error>Sibling tool call errored</tool_use_error>",
                is_error=True,
            ),
        )
    ])
    with pytest.raises(ToolError, match="include_cascade=true"):
        _survey_tool()()


def test_mcp_survey_zero_result_suggests_widening_when_nothing_was_suppressed(
    tmp_path, corpus_of
):
    from fastmcp.exceptions import ToolError

    corpus_of([_write(tmp_path / "enc", SID_A, _call("Bash", "t1", "fine"))])
    with pytest.raises(ToolError, match="Widen the window"):
        _survey_tool()()


def test_mcp_survey_wires_examples_and_limit_through(tmp_path, corpus_of):
    lines = []
    for i in range(4):
        lines += _call(
            "Bash", f"t{i}", f"<tool_use_error>novel thing {i}</tool_use_error>",
            is_error=True,
        )
    corpus_of([_write(tmp_path / "enc", SID_A, lines)])

    lean = _survey_tool()()
    assert lean.total == 4
    assert lean.unclassified[0].example is None

    rich = _survey_tool()(examples=True, limit=2)
    assert rich.unclassified[0].example is not None
    assert len(rich.unclassified) == 2
    assert "2 of 4" in rich.unclassified_overflow
