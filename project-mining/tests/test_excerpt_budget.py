"""Shared grep budgets bound source excerpts without changing search totals."""

import pytest

from cc_explorer.providers import Harness
from cc_explorer.responses import (
    GrepPatternResult, GrepSessionResponse, GrepSessionsResponse, MatchBlock,
)
from cc_explorer.utils import PrefixId


def _line(text: str = "a matching phrase", turn: str = "01234567") -> str:
    return f"{turn}|1720000000|A|9000|{text}"


def _pattern(name: str, *blocks: MatchBlock, hits: int = 10) -> GrepPatternResult:
    return GrepPatternResult(pattern=name, hits=hits, showing=len(blocks), matches=list(blocks))


def _session(*patterns: GrepPatternResult, session: str = "11111111") -> GrepSessionResponse:
    return GrepSessionResponse(
        session=PrefixId(session), harness=Harness.codex, project="/repo/example", patterns=list(patterns),
    )


def test_under_budget_is_unchanged_and_summary_marker_is_omitted():
    response = _session(_pattern("phrase", MatchBlock(before=[_line("before")], match=_line(), after=[_line("after")]), hits=1))
    before = response.model_dump()

    assert response.bound_excerpts(10000) is response
    assert response.model_dump() == before
    assert "output_truncated" not in response.model_dump()


def test_one_budget_spans_patterns_and_preserves_hit_counts():
    block = MatchBlock(match=_line())
    response = _session(_pattern("first", block, block), _pattern("second", block, hits=4))

    response.bound_excerpts(len(block.match) * 2)

    assert response.excerpt_chars == len(block.match) * 2
    assert [(p.hits, p.showing, len(p.matches)) for p in response.patterns] == [(10, 2, 2), (4, 0, 0)]
    assert response.output_truncated is True
    assert "budget" in response.patterns[1].overflow
    assert response.patterns[0].overflow is None


def test_first_oversized_block_keeps_locator_and_original_full_length():
    block = MatchBlock(before=[_line("before")], match=_line("word " * 1000), after=[_line("after")])
    response = _session(_pattern("word", block))
    prefix = "01234567|1720000000|A|9000|"
    budget = len(prefix) + 27

    response.bound_excerpts(budget)

    shown = response.patterns[0].matches[0]
    assert shown.before == shown.after == []
    assert shown.match.startswith(prefix)
    assert shown.match.endswith("...")
    assert response.excerpt_chars <= budget
    assert response.patterns[0].showing == 1
    assert response.patterns[0].hits == 10
    assert response.output_truncated is True
    assert response.patterns[0].overflow


def test_context_is_dropped_before_shortening_matching_line():
    line = _line()
    response = _session(_pattern("phrase", MatchBlock(before=[_line("before")], match=line, after=[_line("after")])))

    response.bound_excerpts(len(line))

    block = response.patterns[0].matches[0]
    assert block.match == line
    assert block.before == block.after == []
    assert response.output_truncated is True


@pytest.mark.parametrize("budget", [0, 1, 5, 15, 25, 26, 27, 28, 29, 50, 100, 400])
def test_budget_is_a_hard_character_bound_even_for_tiny_locator_budgets(budget):
    response = _session(_pattern("first", MatchBlock(match=_line("x" * 1000))), _pattern("second", MatchBlock(match=_line())))

    response.bound_excerpts(budget)

    assert response.excerpt_chars <= budget
    assert all(p.showing == len(p.matches) for p in response.patterns)
    assert all(p.hits == 10 for p in response.patterns)
    assert response.output_truncated is True
    for pattern in response.patterns:
        for block in pattern.matches:
            assert block.match.startswith("01234567|1720000000|A|9000|")


def test_zero_budget_drops_all_blocks_but_keeps_overflow_and_identity():
    pattern = _pattern("first", MatchBlock(match=_line()))
    pattern.overflow = "already limited"
    response = _session(pattern)

    response.bound_excerpts(0)

    assert response.patterns[0].matches == []
    assert response.patterns[0].showing == 0
    assert response.patterns[0].hits == 10
    assert response.patterns[0].overflow
    assert response.session.full == "11111111"
    assert response.project == "/repo/example"


def test_multi_session_budget_is_shared_and_keeps_empty_session_summaries():
    block = MatchBlock(match=_line())
    response = GrepSessionsResponse(sessions=[
        _session(_pattern("phrase", block), session="11111111"),
        _session(_pattern("phrase", block), session="22222222"),
    ], not_found=["missing"])

    assert response.bound_excerpts(len(block.match)) is response

    assert sum(s.excerpt_chars for s in response.sessions) == len(block.match)
    assert [s.session.full for s in response.sessions] == ["11111111", "22222222"]
    assert [s.patterns[0].showing for s in response.sessions] == [1, 0]
    assert [s.patterns[0].hits for s in response.sessions] == [10, 10]
    assert response.sessions[1].output_truncated is True
    assert response.output_truncated is True
    assert response.not_found == ["missing"]


def test_multi_session_under_budget_and_empty_result_have_no_marker():
    response = GrepSessionsResponse(sessions=[_session(_pattern("nohit", hits=0))])
    before = response.model_dump()
    response.bound_excerpts(0)
    assert response.model_dump() == before
    assert "output_truncated" not in response.model_dump()


@pytest.mark.parametrize("response", [_session(), GrepSessionsResponse(sessions=[])])
def test_negative_budget_is_rejected(response):
    with pytest.raises(ValueError, match="non-negative"):
        response.bound_excerpts(-1)
