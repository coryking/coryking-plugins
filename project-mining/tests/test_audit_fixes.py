"""Pins for cc-explorer behaviors that are easy to lose silently.

Each class here exists because something used to fail in a way that wasn't
visible: a stray dependency, a partial result with no diagnostic, a count
that hid skipped work, a model field that quietly shadowed its parent.
The tests pin the visible behavior so future edits can't reintroduce the
silent failure mode.

What's pinned and why:

  TestNoPlaywrightDependency
    cc-explorer is a stdio MCP server with no browser surface. playwright
    is a 40+ MB browser-automation lib that has no business in this
    package's dependency tree, but it's easy to paste in by accident from
    a sibling project. The test reads pyproject.toml directly so a stray
    dependency fails CI before it ever lands in uv.lock.

  TestGrepSessionsResponseHasNotFoundField
  TestGrepSessionsSurfacesUnresolvedPrefixes
    grep_sessions takes a list of session prefixes and fans out across
    them. If a prefix doesn't resolve to a real session, the response
    must surface the unresolved value via a `not_found` field — otherwise
    the caller can't distinguish a typo from a session that just had no
    matches (zero-hit sessions are intentionally omitted from the result).
    When *every* prefix fails to resolve, the tool raises ToolError
    instead, since there's nothing useful to return.

  TestSessionToolAuditResponseHasDispatchVsAuditedCounts
  TestSessionToolAuditCountsReflectSkippedAgents
    session_tool_audit can only audit subagents whose .output files are
    accessible. The response has to expose both `total_dispatched` (every
    subagent the session spawned) and `total_audited` (the subset whose
    output files were readable), so callers can see when work was skipped.
    A single `total_agents` field would be ambiguous and would hide the
    gap.

  TestGrepSessionsPreservesInputOrder
  TestGrepSessionsOmitsZeroHitSessions
    grep_sessions returns sessions in the order the caller passed them
    (not the order load_sessions yields), and drops sessions that had
    zero matches across all patterns. Both behaviors are load-bearing
    for how callers consume the response.

  TestToolResultEntryDoesNotRedeclareAgentId
    agentId is declared on BaseTranscriptEntry. ToolResultEntry must
    inherit it rather than redeclaring with an identical type — a
    same-type shadow is dead code that obscures the type hierarchy and
    invites a future reader to "fix" the wrong layer. If a real reason
    forces the redeclaration back (a pyright narrowing issue, a
    serialization quirk), document it in a comment so this test can be
    updated with that justification.
"""

from __future__ import annotations

import tomllib
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    TextContent,
    ToolResultEntry,
    TranscriptStats,
)
from cc_explorer.search import MatchHit, SessionInfo
from cc_explorer.utils import PrefixId


TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
TURN_UUID = PrefixId("11111111-aaaa-bbbb-cccc-dddddddddddd")


# =============================================================================
# Helpers
# =============================================================================


def _build_session(sid: str, name: str = "t") -> SessionInfo:
    return SessionInfo(
        session_id=PrefixId(sid),
        path=Path(f"/fake/{name}.jsonl"),
        title=name,
        first_timestamp=TS,
        message_count=4,
        stats=TranscriptStats(),
    )


def _fake_match_for(sid: str) -> MatchHit:
    entry = AssistantTranscriptEntry(
        uuid=TURN_UUID,
        timestamp=TS,
        sessionId=PrefixId(sid),
        type="assistant",
        message=AssistantMessageModel(
            id="m1",
            type="message",
            role="assistant",
            model="claude-sonnet-4",
            content=[TextContent(type="text", text="line containing TARGET inside")],
        ),
    )
    return MatchHit(
        session_id=PrefixId(sid),
        turn_uuid=TURN_UUID,
        entry=entry,
        context_before=[],
        context_after=[],
    )


# search_multi returns {session_id: [(pattern, [matches], total), ...]}
def _hit_multi(sid: str, pattern: str = "TARGET") -> list[tuple[str, list[MatchHit], int]]:
    return [(pattern, [_fake_match_for(sid)], 1)]


def _empty_multi(pattern: str = "TARGET") -> list[tuple[str, list[MatchHit], int]]:
    return [(pattern, [], 0)]


# =============================================================================
# Dependency hygiene
# =============================================================================


class TestNoPlaywrightDependency:
    """playwright must not appear in cc-explorer's dependency list.

    cc-explorer is a stdio MCP server for chat-history exploration. It has
    no browser surface and nothing under src/cc_explorer/ imports playwright.
    The dependency was added once by accidental paste from a sibling project,
    pulled in 40+ MB of browser binaries via uv.lock, and went unnoticed
    until review. This test makes a recurrence fail loudly.
    """

    def test_playwright_not_in_project_dependencies(self):
        pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
        data = tomllib.loads(pyproject.read_text())
        deps = data["project"]["dependencies"]
        offenders = [d for d in deps if "playwright" in d.lower()]
        assert not offenders, (
            f"playwright dependency found: {offenders}. "
            "It's not imported anywhere in src/cc_explorer/. Strip it."
        )


# =============================================================================
# grep_sessions: surfacing unresolved prefixes
# =============================================================================
#
# grep_sessions takes a list of session prefixes. Two failure modes used to
# be invisible:
#   - a prefix that doesn't resolve to any session
#   - a session that resolves but has zero matches across all patterns
# Both used to be silently omitted from the response, leaving the caller
# unable to distinguish a typo from an empty result. The fix surfaces
# unresolved prefixes via a `not_found` field on GrepSessionsResponse;
# zero-hit sessions remain (intentionally) omitted, but the caller can now
# tell which case they're in. When *every* prefix fails to resolve, the
# tool raises ToolError instead of returning a degenerate response.


class TestGrepSessionsResponseHasNotFoundField:
    def test_response_model_declares_not_found(self):
        from cc_explorer.responses import GrepSessionsResponse

        assert "not_found" in GrepSessionsResponse.model_fields, (
            "GrepSessionsResponse needs a `not_found` field so unresolved "
            "session prefixes are surfaced rather than silently dropped."
        )


class TestGrepSessionsSurfacesUnresolvedPrefixes:
    def test_bad_prefixes_appear_in_not_found(self):
        from cc_explorer.mcp_server import grep_sessions

        good_id = "aaaaaaaa-1111-2222-3333-444444444444"
        sessions = [_build_session(good_id, "good")]

        def fake_search_multi(target_sessions, patterns, **kwargs):
            return {s.session_id: _hit_multi(s.session_id) for s in target_sessions}

        with patch("cc_explorer.mcp_server.load_sessions", return_value=sessions), \
             patch("cc_explorer.mcp_server.search_multi", side_effect=fake_search_multi):
            resp = grep_sessions(
                sessions=["aaaaaaaa", "deadbeef", "cafef00d"],
                patterns=["TARGET"],
                project="/tmp/fake",
            )

        # Good session resolved
        assert len(resp.sessions) == 1
        # Bad ones surfaced — content matters, ordering doesn't
        assert resp.not_found is not None
        assert set(resp.not_found) == {"deadbeef", "cafef00d"}

    def test_all_prefixes_unresolved_still_raises(self):
        from cc_explorer.mcp_server import grep_sessions
        from fastmcp.exceptions import ToolError

        good_id = "aaaaaaaa-1111-2222-3333-444444444444"
        sessions = [_build_session(good_id, "good")]

        with patch("cc_explorer.mcp_server.load_sessions", return_value=sessions):
            with pytest.raises(ToolError):
                grep_sessions(
                    sessions=["deadbeef", "cafef00d"],
                    patterns=["TARGET"],
                    project="/tmp/fake",
                )


# =============================================================================
# session_tool_audit: dispatched vs. audited counts
# =============================================================================
#
# session_tool_audit walks subagent .output files to report tool usage per
# agent. Not every spawned subagent has a readable output file — some get
# skipped. The response exposes two separate counts so the caller can see
# the gap:
#   - total_dispatched: every subagent the session spawned
#   - total_audited:    the subset whose output files were accessible
# A single ambiguous `total_agents` field (was it dispatched? was it
# audited?) would hide the skipped work entirely, so the response model
# must carry both — and the ambiguous name must not be present.


class TestSessionToolAuditResponseHasDispatchVsAuditedCounts:
    def test_response_model_has_total_dispatched_and_total_audited(self):
        from cc_explorer.responses import SessionToolAuditResponse

        fields = SessionToolAuditResponse.model_fields
        assert "total_dispatched" in fields, (
            "Need total_dispatched: number of subagents the session spawned, "
            "regardless of whether their output files were accessible."
        )
        assert "total_audited" in fields, (
            "Rename total_agents -> total_audited so it's clear it's the "
            "subset with accessible output files."
        )
        assert "total_agents" not in fields, (
            "Drop total_agents — its meaning was ambiguous (audited or dispatched?). "
            "Toolbox, not product — no backwards-compat shim."
        )


class TestSessionToolAuditCountsReflectSkippedAgents:
    """End-to-end: build a session with N spawned agents where only some
    have accessible output files. Both counts should be visible in the
    response.
    """

    def test_dispatched_and_audited_counts_diverge_when_outputs_missing(self):
        from cc_explorer.mcp_server import session_tool_audit
        from cc_explorer.subagents import SubagentInfo
        from cc_explorer.utils import PrefixId

        target_id = "aaaaaaaa-1111-2222-3333-444444444444"
        sessions = [_build_session(target_id, "audit")]

        # Three agents: only one has an accessible output file
        audited_agent = SubagentInfo(
            tool_use_id=PrefixId("11111111-aaaa-bbbb-cccc-dddddddddddd"),
            agent_id=PrefixId("aaaaaaa1-aaaa-bbbb-cccc-dddddddddddd"),
            subagent_type="researcher",
            description="audited one",
        )
        skipped_a = SubagentInfo(
            tool_use_id=PrefixId("22222222-aaaa-bbbb-cccc-dddddddddddd"),
            agent_id=PrefixId("aaaaaaa2-aaaa-bbbb-cccc-dddddddddddd"),
            subagent_type="researcher",
            description="missing output",
        )
        skipped_b = SubagentInfo(
            tool_use_id=PrefixId("33333333-aaaa-bbbb-cccc-dddddddddddd"),
            agent_id=PrefixId("aaaaaaa3-aaaa-bbbb-cccc-dddddddddddd"),
            subagent_type="researcher",
            description="also missing",
        )
        all_agents = [audited_agent, skipped_a, skipped_b]

        # Only audited_agent appears in entries_map (others have no output file)
        entries_map = {audited_agent.agent_id: []}

        def fake_resolve(_agents, _output_dir):
            pass

        def fake_scan(_agents, keep_entries=False):
            return entries_map

        def fake_extract(entries, tool_name_filter=None, truncate=80):
            # Empty trace — we just care about the counts
            return [], {}, 0

        with patch("cc_explorer.mcp_server.load_sessions", return_value=sessions), \
             patch("cc_explorer.mcp_server.extract_subagents", return_value=all_agents), \
             patch("cc_explorer.mcp_server.resolve_output_files", side_effect=fake_resolve), \
             patch("cc_explorer.mcp_server.scan_output_file_stats", side_effect=fake_scan), \
             patch(
                 "cc_explorer.mcp_server.extract_agent_tool_audit",
                 side_effect=fake_extract,
             ):
            resp = session_tool_audit(session="aaaaaaaa", project="/tmp/fake")

        assert resp.total_dispatched == 3, (
            f"3 agents were spawned, got total_dispatched={resp.total_dispatched}"
        )
        assert resp.total_audited == 1, (
            f"only 1 had an accessible output file, got total_audited={resp.total_audited}"
        )
        assert len(resp.agents) == 1


# =============================================================================
# grep_sessions: ordering and omission
# =============================================================================
#
# Two load-bearing behaviors for how callers read grep_sessions results:
# the response lists sessions in the order the *caller* passed them (not
# the order load_sessions yields, which is newest-first by timestamp),
# and sessions with zero matches across all patterns are omitted from the
# response entirely. Pinning both so reshuffles in the search layer
# can't quietly break consumer code.


class TestGrepSessionsPreservesInputOrder:
    def test_response_sessions_follow_input_order(self):
        from cc_explorer.mcp_server import grep_sessions

        sid_a = "aaaaaaaa-1111-2222-3333-444444444444"
        sid_b = "bbbbbbbb-1111-2222-3333-444444444444"
        sid_c = "cccccccc-1111-2222-3333-444444444444"
        sessions = [
            _build_session(sid_a, "a"),
            _build_session(sid_b, "b"),
            _build_session(sid_c, "c"),
        ]

        def fake_search_multi(target_sessions, patterns, **kwargs):
            return {s.session_id: _hit_multi(s.session_id) for s in target_sessions}

        with patch("cc_explorer.mcp_server.load_sessions", return_value=sessions), \
             patch("cc_explorer.mcp_server.search_multi", side_effect=fake_search_multi):
            # Caller passes c, a, b — output must follow that order, not the
            # underlying load_sessions order.
            resp = grep_sessions(
                sessions=["cccccccc", "aaaaaaaa", "bbbbbbbb"],
                patterns=["TARGET"],
                project="/tmp/fake",
            )

        out_ids = [str(s.session) for s in resp.sessions]
        assert out_ids == ["cccccccc", "aaaaaaaa", "bbbbbbbb"], (
            f"input order must be preserved, got {out_ids}"
        )


class TestGrepSessionsOmitsZeroHitSessions:
    def test_session_with_zero_matches_is_dropped(self):
        from cc_explorer.mcp_server import grep_sessions

        sid_hit = "aaaaaaaa-1111-2222-3333-444444444444"
        sid_miss = "bbbbbbbb-1111-2222-3333-444444444444"
        sessions = [_build_session(sid_hit, "hit"), _build_session(sid_miss, "miss")]

        def fake_search_multi(target_sessions, patterns, **kwargs):
            return {
                s.session_id: (_hit_multi(s.session_id) if s.session_id == sid_hit else _empty_multi())
                for s in target_sessions
            }

        with patch("cc_explorer.mcp_server.load_sessions", return_value=sessions), \
             patch("cc_explorer.mcp_server.search_multi", side_effect=fake_search_multi):
            resp = grep_sessions(
                sessions=["aaaaaaaa", "bbbbbbbb"],
                patterns=["TARGET"],
                project="/tmp/fake",
            )

        assert len(resp.sessions) == 1
        assert str(resp.sessions[0].session) == "aaaaaaaa"


# =============================================================================
# Type hierarchy: agentId lives on BaseTranscriptEntry
# =============================================================================
#
# BaseTranscriptEntry declares `agentId: Optional[PrefixId] = None`, and
# every entry subclass inherits it. ToolResultEntry must not redeclare
# the field with an identical type — a same-type shadow is dead code that
# obscures the type hierarchy and invites a future reader to "fix" the
# wrong layer. If a real reason forces the redeclaration back (a narrowing
# issue, a serialization quirk), that reason needs to be recorded in a
# comment on the field so this test can be updated with the justification
# instead of blindly deleted.


class TestToolResultEntryDoesNotRedeclareAgentId:
    def test_agent_id_declared_only_on_base(self):
        assert "agentId" in BaseTranscriptEntry.__annotations__, (
            "Sanity check: agentId should still live on BaseTranscriptEntry."
        )
        assert "agentId" not in ToolResultEntry.__annotations__, (
            "ToolResultEntry redeclares agentId with the same type as the parent. "
            "Drop the redeclaration — or, if pyright forces it back, add a "
            "comment explaining why it's load-bearing."
        )
