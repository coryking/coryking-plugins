"""Wrong-parameter-name forgiveness and teaching at the MCP boundary.

Callers invent parameter names at a steady rate — `query` for `patterns`,
`project` for `projects`, `session_id` for `session` — and the raw pydantic
error ("patterns / Missing required argument") never names the guess that was
wrong, so recovery takes several retries.

Two layers under test (both in ParameterRepairMiddleware, over param_repair.py):
  * forgiveness — the unambiguous guesses are rewritten before validation, so
    the call SUCCEEDS, while the advertised schema stays canonical;
  * teaching — a guess that is NOT alias-covered fails with a message that names
    the likely intended parameter and lists the tool's real ones.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastmcp.exceptions import ToolError
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from cc_explorer.mcp_server import mcp
from cc_explorer.models import (
    AssistantMessageModel,
    AssistantTranscriptEntry,
    TextContent,
    TranscriptStats,
)
from cc_explorer.param_repair import (
    ALIASES,
    ID_LIST_PARAMS,
    _accepts_array,
    argument_error_message,
    repair_arguments,
)
from cc_explorer.search import MatchHit, SessionInfo
from cc_explorer.utils import PrefixId

from tests.conftest import patch_session_corpus

TS = datetime(2026, 3, 15, 10, 30, 0, tzinfo=timezone.utc)
SESSION_ID = "aaaaaaaa-1111-2222-3333-444444444444"
TURN_UUID = PrefixId("11111111-aaaa-bbbb-cccc-dddddddddddd")


# =============================================================================
# Helpers
# =============================================================================


def _schemas() -> dict[str, dict]:
    """Every registered tool's ADVERTISED input schema, keyed by tool name."""
    tools = asyncio.run(mcp.list_tools())
    return {t.name: t.parameters for t in tools}


SCHEMAS = _schemas()


def _call(name: str, arguments: dict):
    """Invoke a tool the way a client does — through the middleware stack."""
    return asyncio.run(mcp.call_tool(name, arguments))


def _build_session(sid: str = SESSION_ID) -> SessionInfo:
    return SessionInfo(
        session_id=PrefixId(sid),
        path=Path("/fake/one.jsonl"),
        title="t",
        first_timestamp=TS,
        message_count=4,
        stats=TranscriptStats(),
    )


def _hit_multi(sid: str, pattern: str = "TARGET"):
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
    hit = MatchHit(
        session_id=PrefixId(sid),
        turn_uuid=TURN_UUID,
        entry=entry,
        context_before=[],
        context_after=[],
    )
    return [(pattern, [hit], 1)]


# =============================================================================
# Forgiveness: each documented wrong guess maps to the right canonical name
# =============================================================================


class TestAliasMapping:
    """Alias -> canonical, resolved against each tool's own schema."""

    @pytest.mark.parametrize(
        "tool,wrong,canonical",
        [
            # `query` / `pattern` on every search surface — the most common guess.
            ("search_projects", "query", "patterns"),
            ("search_projects", "pattern", "patterns"),
            ("grep_session", "query", "patterns"),
            ("grep_session", "pattern", "patterns"),
            ("grep_sessions", "query", "patterns"),
            ("grep_sessions", "pattern", "patterns"),
            # singular project
            ("search_projects", "project", "projects"),
            ("grep_session", "project", "projects"),
            ("grep_sessions", "project", "projects"),
            ("list_project_sessions", "project", "projects"),
            ("survey_failures", "project", "projects"),
            ("get_activity_timeline", "project", "projects"),
            # session_id -> session, on every session-keyed tool
            ("grep_session", "session_id", "session"),
            ("read_turn", "session_id", "session"),
            ("browse_session", "session_id", "session"),
            ("list_session_agents", "session_id", "session"),
            ("get_agent_detail", "session_id", "session"),
            ("audit_session_tools", "session_id", "session"),
            # grep_sessions has no `session` — singular guesses land on `sessions`
            ("grep_sessions", "session_id", "sessions"),
            ("grep_sessions", "session", "sessions"),
            # other corpus-observed guesses
            ("get_agent_detail", "agent_id", "agent_ids"),
            ("read_turn", "turn_id", "turn"),
            ("grep_session", "max_results", "limit"),
        ],
    )
    def test_wrong_name_is_rewritten(self, tool, wrong, canonical):
        repaired = repair_arguments({wrong: ["v"]}, SCHEMAS[tool])
        assert repaired == {canonical: ["v"]}

    def test_alias_only_fires_where_the_canonical_param_exists(self):
        # list_projects takes nothing at all; nothing to map onto.
        assert repair_arguments({"query": ["x"]}, SCHEMAS["list_projects"]) == {
            "query": ["x"]
        }
        # list_project_sessions has no patterns/session param.
        assert repair_arguments({"query": ["x"]}, SCHEMAS["list_project_sessions"]) == {
            "query": ["x"]
        }

    def test_explicit_canonical_wins_over_alias(self):
        repaired = repair_arguments(
            {"patterns": ["real"], "query": ["guess"]}, SCHEMAS["search_projects"]
        )
        assert repaired["patterns"] == ["real"]
        assert repaired["query"] == ["guess"]  # left alone -> teaching layer

    def test_bare_string_is_wrapped_for_list_params(self):
        repaired = repair_arguments(
            {"query": "regex", "project": "/repo"}, SCHEMAS["search_projects"]
        )
        assert repaired == {"patterns": ["regex"], "projects": ["/repo"]}

    def test_bare_string_wrapped_for_canonical_list_params_too(self):
        repaired = repair_arguments(
            {"patterns": "regex", "sessions": "abc123"}, SCHEMAS["grep_sessions"]
        )
        assert repaired == {"patterns": ["regex"], "sessions": ["abc123"]}

    def test_json_array_string_is_parsed_for_identifier_lists(self):
        # Nothing in a project path or session id needs brackets, so a
        # hand-serialized list there can only have been meant as a list.
        assert repair_arguments(
            {"project": '["a", "b"]'}, SCHEMAS["search_projects"]
        ) == {"projects": ["a", "b"]}
        assert repair_arguments(
            {"sessions": '["abc123", "def456"]'}, SCHEMAS["grep_sessions"]
        ) == {"sessions": ["abc123", "def456"]}

    def test_patterns_is_never_unwrapped_from_json(self):
        """A regex is one pattern even when it parses as a JSON array of strings.

        `["error"]` and `["\\d+"]` are valid character classes. Unwrapping them
        would silently search for `error` / `\\d+` — a different query the caller
        never asked for, with no error to notice.
        """
        for regex in ('["error"]', '["\\d+"]', '["a","b"]'):
            assert repair_arguments(
                {"patterns": regex}, SCHEMAS["search_projects"]
            ) == {"patterns": [regex]}
            # Same via the alias path — the parameter decides, not the name used.
            assert repair_arguments({"query": regex}, SCHEMAS["grep_session"]) == {
                "patterns": [regex]
            }

    def test_regex_that_looks_like_a_list_stays_one_pattern(self):
        # `[abc]` and `[1,2]` are character classes, not payloads.
        assert repair_arguments({"patterns": "[abc]"}, SCHEMAS["search_projects"]) == {
            "patterns": ["[abc]"]
        }
        assert repair_arguments({"patterns": "[1,2]"}, SCHEMAS["search_projects"]) == {
            "patterns": ["[1,2]"]
        }

    def test_every_array_parameter_is_classified(self):
        """No array parameter may be left un-triaged as id-list or regex.

        A new list-typed parameter must be a deliberate decision: identifiers
        get the JSON unwrap, regexes must not. Anything else is an oversight —
        this fails until it's named.
        """
        regex_params = {"patterns"}
        unclassified = set()
        for schema in SCHEMAS.values():
            for name, prop in (schema.get("properties") or {}).items():
                if _accepts_array(prop) and name not in ID_LIST_PARAMS | regex_params:
                    unclassified.add(name)
        assert not unclassified, f"array params neither id-list nor regex: {unclassified}"

    def test_scalar_params_are_not_wrapped(self):
        repaired = repair_arguments(
            {"session": "abc123", "hide": "thinking"}, SCHEMAS["grep_session"]
        )
        assert repaired == {"session": "abc123", "hide": "thinking"}

    def test_canonical_arguments_pass_through_untouched(self):
        args = {
            "session": "abc123",
            "patterns": ["x", "y"],
            "projects": ["/repo"],
            "context": 3,
            "errors_only": True,
        }
        assert repair_arguments(dict(args), SCHEMAS["grep_session"]) == args


# =============================================================================
# The aliases stay hidden: nothing leaks into the advertised surface
# =============================================================================


class TestNoAliasLeakage:
    def test_no_tool_advertises_both_an_alias_and_its_canonical(self):
        """The tell-tale shape of a leaked alias: both names on one tool.

        `session` / `sessions` are each a real parameter somewhere AND an alias
        elsewhere, so the assertion is per tool: if a tool advertises an alias
        name alongside the canonical it maps to, the recovery mechanism has
        become documented surface.
        """
        offenders = []
        for tool, schema in SCHEMAS.items():
            props = set(schema.get("properties", {}))
            for alias, targets in ALIASES.items():
                if alias in props and any(target in props for target in targets):
                    offenders.append((tool, alias))
        assert not offenders, f"alias names advertised in schemas: {offenders}"

    def test_middleware_never_touches_the_advertised_listing(self):
        # No on_list_tools override => aliases cannot reach a client's schema.
        from fastmcp.server.middleware import Middleware

        from cc_explorer.mcp_server import ParameterRepairMiddleware

        assert (
            ParameterRepairMiddleware.on_list_tools is Middleware.on_list_tools
        ), "the repair middleware must not rewrite what tools/list advertises"

    def test_advertised_search_schemas_are_canonical(self):
        assert set(SCHEMAS["search_projects"]["properties"]) == {
            "patterns",
            "projects",
            "harnesses",
            "role",
            "after",
            "before",
            "excerpt_width",
            "include_current_session",
            "errors_only",
        }
        assert SCHEMAS["search_projects"]["required"] == ["patterns"]
        assert set(SCHEMAS["grep_session"]["properties"]) == {
            "session",
            "patterns",
            "projects",
            "harnesses",
            "context",
            "role",
            "limit",
            "truncate",
            "hide",
            "errors_only",
        }

    def test_parameter_descriptions_do_not_teach_aliases(self):
        # An alias must never be presented as a usable parameter name.
        for tool, schema in SCHEMAS.items():
            for name, prop in schema.get("properties", {}).items():
                desc = prop.get("description", "")
                for alias in ("`query`", "`session_id`", "`pattern`"):
                    assert alias not in desc, f"{tool}.{name} advertises {alias}"


# =============================================================================
# Teaching: a non-aliased wrong name gets a recoverable error
# =============================================================================


class TestTeachingError:
    def test_near_miss_names_the_intended_parameter(self):
        with pytest.raises(ToolError) as exc:
            _call("grep_session", {"session": "abc", "patterns_": ["x"]})
        msg = str(exc.value)
        assert "patterns_" in msg
        assert "did you mean `patterns`" in msg

    def test_unrecognizable_name_still_lists_the_real_parameters(self):
        with pytest.raises(ToolError) as exc:
            _call("grep_session", {"session": "abc", "haystack": ["x"]})
        msg = str(exc.value)
        assert "haystack" in msg
        assert "Parameters of grep_session:" in msg
        for name in ("`session` (required)", "`patterns` (required)", "`errors_only`"):
            assert name in msg

    def test_missing_required_parameter_is_named(self):
        # grep_sessions with no `sessions` — a documented recurring failure.
        with pytest.raises(ToolError) as exc:
            _call("grep_sessions", {"patterns": ["x"]})
        msg = str(exc.value)
        assert "Missing required parameter(s): `sessions`" in msg
        assert "Parameters of grep_sessions:" in msg

    def test_duplicate_alias_plus_canonical_says_drop_the_alias(self):
        with pytest.raises(ToolError) as exc:
            _call("search_projects", {"patterns": ["a"], "query": ["b"]})
        msg = str(exc.value)
        assert "you already passed `patterns`" in msg

    def test_value_errors_are_reported_verbatim(self):
        message = argument_error_message(
            "grep_session",
            SCHEMAS["grep_session"],
            {"session": "abc", "patterns": ["x"], "context": 99},
            [
                {
                    "type": "less_than_equal",
                    "loc": ("context",),
                    "msg": "Input should be less than or equal to 5",
                }
            ],
        )
        assert "`context`: Input should be less than or equal to 5" in message
        assert "Parameters of grep_session:" in message

    def test_diagnosis_comes_before_the_inventory(self):
        # Output is read top-down and truncated from the bottom.
        message = argument_error_message(
            "grep_session",
            SCHEMAS["grep_session"],
            {"session": "abc", "haystack": ["x"]},
            [{"type": "unexpected_keyword_argument", "loc": ("haystack",), "msg": "x"}],
        )
        lines = message.splitlines()
        assert lines[0] == "Invalid arguments for grep_session."
        assert "haystack" in lines[1]
        assert lines[-1].startswith("Parameters of grep_session:")


# =============================================================================
# The client surface: what an actual MCP client sees
# =============================================================================


class TestClientSurface:
    """Through a real client session, not just the in-process call path."""

    def test_advertised_schemas_carry_no_alias_properties(self):
        async def run():
            from fastmcp import Client

            async with Client(mcp) as client:
                return await client.list_tools()

        for tool in asyncio.run(run()):
            props = set(tool.inputSchema.get("properties", {}))
            leaked = props & {"query", "queries", "pattern", "project", "session_id"}
            assert not leaked, f"{tool.name} advertises alias(es) {leaked}"

    def test_teaching_message_arrives_as_the_error_content(self):
        async def run():
            from fastmcp import Client

            async with Client(mcp) as client:
                return await client.call_tool(
                    "grep_session",
                    {"session": "abc", "haystack": ["x"]},
                    raise_on_error=False,
                )

        result = asyncio.run(run())
        assert result.is_error
        text = result.content[0].text
        assert "No parameter `haystack` on grep_session" in text
        assert "Parameters of grep_session:" in text
        # The raw pydantic wall of text is gone.
        assert "errors.pydantic.dev" not in text


# =============================================================================
# End to end: the wrong guess now returns results instead of an error
# =============================================================================


class TestForgivenessEndToEnd:
    def test_grep_session_accepts_query_and_session_id(self):
        sessions = [_build_session()]

        def fake_search_multi(target_sessions, patterns, **kwargs):
            return {s.session_id: _hit_multi(str(s.session_id)) for s in target_sessions}

        with patch_session_corpus(sessions), patch(
            "cc_explorer.mcp_server.search_multi", side_effect=fake_search_multi
        ):
            result = _call(
                "grep_session",
                {"session_id": SESSION_ID, "query": ["TARGET"], "project": "/fake"},
            )

        assert result.structured_content["session"].startswith("aaaaaaaa")
        assert result.structured_content["patterns"][0]["pattern"] == "TARGET"

    def test_validation_error_from_inside_the_tool_is_not_rewritten(self):
        """A pydantic error from the tool BODY is not an argument error.

        The middleware's gate is the pydantic title: only a function-call
        validation (`call[...]`) describes the caller's arguments. A malformed
        transcript that fails model validation deep inside the tool must surface
        as itself — rewriting it into "here are grep_session's parameters" would
        send the caller off fixing arguments that were never wrong.
        """

        class Deeper(BaseModel):
            n: int

        with pytest.raises(PydanticValidationError) as inner:
            Deeper(n="not-an-int")
        body_error = inner.value
        assert not body_error.title.startswith("call[")

        sessions = [_build_session()]

        def blow_up(*args, **kwargs):
            raise body_error

        with patch_session_corpus(sessions), patch(
            "cc_explorer.mcp_server.search_multi", side_effect=blow_up
        ):
            with pytest.raises(PydanticValidationError) as raised:
                _call(
                    "grep_session",
                    {"session": SESSION_ID, "patterns": ["TARGET"], "projects": ["/fake"]},
                )

        assert raised.value is body_error
        message = str(raised.value)
        assert "Parameters of grep_session:" not in message
        assert "No parameter" not in message

    def test_canonical_call_still_works(self):
        sessions = [_build_session()]

        def fake_search_multi(target_sessions, patterns, **kwargs):
            return {s.session_id: _hit_multi(str(s.session_id)) for s in target_sessions}

        with patch_session_corpus(sessions), patch(
            "cc_explorer.mcp_server.search_multi", side_effect=fake_search_multi
        ):
            result = _call(
                "grep_session",
                {"session": SESSION_ID, "patterns": ["TARGET"], "projects": ["/fake"]},
            )

        assert result.structured_content["session"].startswith("aaaaaaaa")
        assert result.structured_content["patterns"][0]["pattern"] == "TARGET"
