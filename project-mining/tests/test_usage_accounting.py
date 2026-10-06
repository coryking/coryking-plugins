"""Synthetic wire shapes only: accounting invariants, scope and MCP contracts."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from cc_explorer.usage import get_report, get_observations
from cc_explorer.mcp_server import get_usage_report, get_usage_observations, mcp
from cc_explorer.usage_sources import snapshot

PARENT = "11111111-1111-4111-8111-111111111111"
CHILD = "22222222-2222-4222-8222-222222222222"
THIRD = "33333333-3333-4333-8333-333333333333"
TS = "2026-10-06T12:00:00Z"


def write(path, *records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in records))
    return path


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    claude = tmp_path / "claude"
    codex = tmp_path / "codex"
    claude.mkdir()
    (codex / "sessions").mkdir(parents=True)
    (codex / "archived_sessions").mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex))
    monkeypatch.setattr("cc_explorer._claude_paths._get_projects_dir", lambda: claude)
    monkeypatch.setattr("cc_explorer._claude_paths._get_worktree_paths", lambda cwd: [])
    return claude, codex


def claude(mid="message-1", *, output=5, timestamp=TS, sid=PARENT, **extra):
    return {"type": "assistant", "uuid": "uuid-" + str(mid), "sessionId": sid,
        "timestamp": timestamp, "cwd": "/repo/example", **extra,
        "message": {"id": mid, "model": "claude-example", "role": "assistant", "type": "message",
            "stop_reason": "end_turn", "content": [{"type": "text", "text": "synthetic"}],
            "usage": {"input_tokens": 10, "cache_read_input_tokens": 20,
                      "cache_creation_input_tokens": 30, "output_tokens": output,
                      "cache_creation": {"ephemeral_5m_input_tokens": 10, "ephemeral_1h_input_tokens": 20},
                      "output_tokens_details": {"thinking_tokens": 2}}}}


def meta(sid=PARENT, **extra):
    return {"type": "session_meta", "timestamp": TS, "ordinal": 0,
            "payload": {"id": sid, "cwd": "/repo/example", "timestamp": TS, **extra}}


def context(model="model-a", effort="medium", ordinal=1, timestamp=TS):
    return {"type": "turn_context", "ordinal": ordinal, "timestamp": timestamp,
            "payload": {"model": model, "effort": effort}}


def tokens(value=100, **extra):
    return {"input_tokens": value, "cached_input_tokens": value//5,
            "cache_write_input_tokens": value//10, "output_tokens": value//2,
            "reasoning_output_tokens": value//10, "total_tokens": value+value//2, **extra}


def response(rid="response-1", value=100, ordinal=2, timestamp=TS, **extra):
    return {"type": "token_usage_record", "ordinal": ordinal, "timestamp": timestamp,
            "payload": {"response_id": rid, "usage": tokens(value), **extra}}


def cumulative(value=100, ordinal=2, timestamp=TS, **extra):
    return {"type": "event_msg", "ordinal": ordinal, "timestamp": timestamp,
            "payload": {"type": "token_count", "info": {"total_token_usage": tokens(value, **extra),
                                                        "last_token_usage": tokens(value)}}}


def roll(codex, sid=PARENT, suffix=None, *items):
    return write(codex / "sessions" / f"rollout-{suffix or sid}.jsonl", *items)


def subtotal(report, category):
    return report.totals.categories[category].observed_subtotal


def test_streaming_duplicates_and_distinct_identical_requests(corpus):
    root, _ = corpus
    a = claude(output=2)
    a["message"]["stop_reason"] = None
    write(root / "project" / f"{PARENT}.jsonl", a, claude(), claude(), claude("message-2"))
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 10
    assert subtotal(report, "uncached_input") == 20
    assert report.totals.model_requests == 2
    assert report.sessions[0].counts["assistant_turns"] == 2
    assert report.sessions[0].first_input == 60
    assert subtotal(report, "cache_creation_input") == 60
    assert subtotal(report, "cache_creation_5m") == 20
    assert subtotal(report, "reasoning_output") == 4


def test_copied_requests_do_not_change_totals_and_keep_locators(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude())
    write(root / "replica" / f"{PARENT}.jsonl", claude())
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    detail = get_observations(session="claude:" + PARENT, harnesses=["claude"])
    assert len(detail.observations[0].sources) == 2
    assert detail.observations[0].identity == "claude:request:message-1"


def test_conflicting_copies_are_visible_and_excluded(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude())
    write(root / "replica" / f"{PARENT}.jsonl", claude(output=6))
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 0
    assert report.coverage["conflicting_sessions"] == 1
    assert report.coverage["conflicting_sources"] == 2
    assert report.coverage["complete_observed_usage"] is False


def test_parent_child_selection_is_idempotent_and_project_preserved(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), response())
    child_meta = meta(CHILD, parent_thread_id=PARENT, agent_role="reviewer")
    child_meta["payload"]["cwd"] = "/repo/elsewhere"
    roll(codex, CHILD, None, child_meta, context(), response("response-2", thread_id=CHILD))
    one = get_report(sessions=["codex:" + PARENT], projects=["/repo/example"], harnesses=["codex"])
    both = get_report(sessions=[PARENT, CHILD], harnesses=["codex"])
    assert one.totals == both.totals
    assert one.session_count == 2
    assert one.sessions[1].project == "/repo/elsewhere"
    assert one.sessions[1].role == "reviewer"
    assert subtotal(one, "uncached_input") == 140


def test_nested_progress_and_child_body_count_once(corpus):
    root, _ = corpus
    nested = claude("nested-request", sid=PARENT)
    progress = {"type": "progress", "timestamp": TS, "cwd": "/repo/example",
                "data": {"type": "agent_progress", "agentId": CHILD, "message": nested}}
    write(root / "project" / f"{PARENT}.jsonl", claude(), progress)
    write(root / "project" / PARENT / "subagents" / f"agent-{CHILD}.jsonl", nested)
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    assert subtotal(report, "output") == 10
    assert report.session_count == 2
    assert report.totals.model_requests == 2


def test_conversion_prefix_excluded_but_resumed_work_counts(corpus):
    root, _ = corpus
    marker = {"type": "x-converter-provenance", "x_converter": {"from": {"id": THIRD}, "lines_at_creation": 2}}
    write(root / "project" / f"{PARENT}.jsonl", marker, claude("copied"), claude("new"))
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    detail = get_observations(session=PARENT, harnesses=["claude"])
    assert {o.quality for o in detail.observations} == {"exact", "excluded"}


def test_invalid_conversion_boundary_exposes_gap(corpus):
    root, _ = corpus
    marker = {"type": "x-converter-provenance", "x_converter": {"from": {}, "lines_at_creation": None}}
    write(root / "project" / f"{PARENT}.jsonl", marker, claude())
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert "invalid_conversion_marker" in report.warnings


def test_codex_per_response_preferred_and_reasoning_not_added(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), response(), cumulative(900, ordinal=3), response("response-2", ordinal=4))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 100
    assert subtotal(report, "uncached_input") == 140
    assert subtotal(report, "cache_read_input") == 40
    assert subtotal(report, "cache_creation_input") == 20
    assert subtotal(report, "reasoning_output") == 20
    assert report.totals.model_requests == 2


def test_cumulative_repeats_add_zero_and_requests_unavailable(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), cumulative(0), cumulative(100, ordinal=3), cumulative(100, ordinal=4), cumulative(200, ordinal=5))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 100
    assert report.totals.model_requests is None
    detail = get_observations(session=PARENT, harnesses=["codex"])
    assert detail.observations[2].increment.output == 0


def test_cumulative_resets_and_model_transitions_are_uncertain(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), cumulative(100), context("model-b", ordinal=3),
         cumulative(200, ordinal=4), cumulative(50, ordinal=5), cumulative(100, ordinal=6))
    report = get_report(harnesses=["codex"])
    assert report.coverage["complete_observed_usage"] is False
    assert subtotal(report, "output") == 125
    assert "counter_reset_or_rewrite" in report.warnings
    detail = get_observations(session=PARENT, harnesses=["codex"])
    transition = next(o for o in detail.observations if o.native_usage["input_tokens"] == 200)
    assert transition.model is None
    assert transition.quality == "unattributed"


def test_cumulative_window_uses_predecessor(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), cumulative(100),
         cumulative(200, ordinal=3, timestamp="2026-10-06T12:01:00Z"),
         cumulative(300, ordinal=4, timestamp="2026-10-06T12:02:00Z"))
    report = get_report(harnesses=["codex"], start="2026-10-06T12:01:00Z")
    # First interval spans the boundary and cannot be assigned entirely inside.
    assert subtotal(report, "output") == 50
    assert "counter_interval_crosses_window_start" in report.warnings


def test_context_fill_is_not_consumption(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), cumulative(100, total_tokens=272000))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 0
    assert "context_estimate_not_consumption" in report.warnings


def test_retained_branches_and_inherited_fork_are_distinct(corpus):
    _, codex = corpus
    base_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    new_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    old = roll(codex, PARENT, base_id, meta(), context(), response("old-kept"), response("old-discarded", ordinal=3))
    offset = len(old.read_bytes().splitlines(keepends=True)[0])
    roll(codex, PARENT, new_id, meta(history_base={"thread_id": base_id, "end_byte_offset": offset}), context(), response("new-branch"))
    roll(codex, CHILD, None, meta(CHILD, parent_thread_id=PARENT, subagent_history_start_ordinal=4),
         context(), response("old-kept"), context(ordinal=4), response("new-child", ordinal=5))
    report = get_report(sessions=[PARENT], harnesses=["codex"])
    assert subtotal(report, "output") == 200
    assert len(report.sessions[0].branches) == 2
    assert report.sessions[0].branches[1]["kind"] == "history_lineage"
    assert "explicit_revert" not in report.sessions[0].lifecycle_counts


def test_missing_usage_nulls_and_malformed_coverage(corpus):
    root, _ = corpus
    row = claude()
    del row["message"]["usage"]
    path = write(root / "project" / f"{PARENT}.jsonl", row)
    with path.open("a") as stream:
        stream.write("{broken}\n")
    report = get_report(harnesses=["claude"])
    assert report.coverage["missing_usage_sessions"] == 1
    assert report.coverage["malformed_records"] == 1
    detail = get_observations(session=PARENT, harnesses=["claude"])
    assert detail.model_dump()["observations"][0]["tokens"]["output"] is None
    assert detail.totals.categories["output"].observations_unknown == 1


def test_live_tail_is_bounded(corpus):
    root, _ = corpus
    path = write(root / "project" / f"{PARENT}.jsonl", claude())
    with path.open("a") as stream:
        stream.write(json.dumps(claude("tail")))
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert report.coverage["partial_sources"] == 1
    assert report.coverage["complete_observed_usage"] is False


def test_unreadable_snapshot_is_reported(tmp_path, monkeypatch):
    path = tmp_path / "unreadable.jsonl"
    def fail(*args, **kwargs):
        raise PermissionError("synthetic")
    monkeypatch.setattr(Path, "open", fail)
    records, coverage = snapshot(path, tmp_path)
    assert records == []
    assert coverage.status == "unreadable"


def test_missing_roots_are_uncertainty(corpus, monkeypatch):
    root, _ = corpus
    monkeypatch.setattr("cc_explorer._claude_paths._get_projects_dir", lambda: root / "missing")
    report = get_report(harnesses=["claude"])
    assert report.coverage["roots"][0]["discovered_sources"] is None
    assert report.coverage["complete_observed_usage"] is False


def test_pagination_and_drilldown_preserve_scope_totals(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), response(), response("response-2", ordinal=3))
    roll(codex, CHILD, None, meta(CHILD), context(), response("response-3"))
    a = get_usage_report(harnesses=["codex"], limit=1)
    b = get_usage_report(harnesses=["codex"], offset=1, limit=1)
    assert a.totals == b.totals
    assert a.scope == b.scope
    detail = get_usage_observations(session=a.sessions[0].identity, harnesses=["codex"], limit=1)
    detail2 = get_usage_observations(session=a.sessions[0].identity, harnesses=["codex"], offset=1, limit=1)
    assert detail.totals == detail2.totals == a.sessions[0].totals
    assert detail.next_offset == 1
    assert detail.observations[0].identity != detail2.observations[0].identity
    assert a.sessions[0].identity != b.sessions[0].identity


def test_calling_session_is_not_excluded(corpus, monkeypatch):
    root, _ = corpus
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", PARENT)
    write(root / "project" / f"{PARENT}.jsonl", claude())
    assert get_usage_report(sessions=[PARENT], harnesses=["claude"]).session_count == 1


def test_attribution_is_a_join_not_usage_multiplier(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude())
    plain = get_report(harnesses=["claude"])
    labeled = get_report(harnesses=["claude"], attribution=[{"session": "claude:" + PARENT,
        "labels": {"run": "experiment-1", "phase": "draft"}, "artifacts": [{"name": "accepted", "value": 1, "unit": "result"}]}])
    assert labeled.totals == plain.totals
    assert labeled.rollups["caller_labels"][0].totals == plain.totals
    assert labeled.rollups["caller_labels"][0].key["phase"] == "draft"
    assert labeled.artifacts[0]["unit"] == "result"
    with pytest.raises(ValueError, match="Overlapping"):
        get_report(harnesses=["claude"], attribution=[{"session": "claude:" + PARENT}, {"observation": "claude:request:message-1"}])


def test_invalid_selectors_and_windows_rejected(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude())
    with pytest.raises(ValueError, match="Unresolved"):
        get_report(sessions=[THIRD], harnesses=["claude"])
    with pytest.raises(ValueError, match="timezone"):
        get_report(harnesses=["claude"], start="2026-10-06T12:00:00")
    with pytest.raises(ValueError, match="Unknown harness"):
        get_report(harnesses=["unknown"])


@pytest.mark.parametrize("bad", [-1, True, 1.5, "3"])
def test_invalid_token_counts_not_accepted(corpus, bad):
    root, _ = corpus
    row = claude()
    row["message"]["usage"]["input_tokens"] = bad
    write(root / "project" / f"{PARENT}.jsonl", row)
    report = get_report(harnesses=["claude"])
    assert report.coverage["complete_observed_usage"] is False
    assert subtotal(report, "uncached_input") == 0


def test_mcp_schema_and_serialization(corpus):
    async def inspect():
        tool = await mcp.get_tool("get_usage_report")
        assert "attribution" in tool.parameters["properties"]
        assert "rates" not in tool.parameters["properties"]
        result = await tool.run({"harnesses": ["claude"]})
        assert result.structured_content is not None
        return result
    result = asyncio.run(inspect())
    assert list(result.structured_content)[:4] == ["scope", "totals", "coverage", "warnings"]


def test_parent_summary_usage_not_a_second_charge_and_missing_child_visible(corpus):
    root, _ = corpus
    dispatch = {"type": "user", "uuid": "dispatch-result", "timestamp": TS, "cwd": "/repo/example", "sessionId": PARENT,
        "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "tool-dispatch", "content": "synthetic"}]},
        "toolUseResult": {"agentId": CHILD, "usage": {"input_tokens": 10000, "output_tokens": 5000}}}
    write(root / "project" / f"{PARENT}.jsonl", claude(), dispatch)
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert report.session_count == 2
    assert report.coverage["missing_usage_sessions"] == 1
    assert "dispatched_child_source_unavailable" in report.warnings


def test_missing_ids_remain_distinct_and_request_count_unavailable(corpus):
    root, _ = corpus
    first = claude(None)
    first["uuid"] = "distinct-first"
    second = claude(None)
    second["uuid"] = "distinct-second"
    write(root / "project" / f"{PARENT}.jsonl", first, second)
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 10
    assert report.totals.model_requests is None


def test_codex_model_changes_and_turn_counts_follow_window(corpus):
    _, codex = corpus
    message = {"type": "response_item", "ordinal": 3, "timestamp": TS,
        "payload": {"type": "message", "id": "assistant-1", "role": "assistant", "content": [{"text": "synthetic"}]}}
    roll(codex, PARENT, None, meta(), context(), response(), message,
         context("model-b", "high", ordinal=4, timestamp="2026-10-06T12:01:00Z"),
         response("response-2", ordinal=5, timestamp="2026-10-06T12:01:00Z"))
    report = get_report(harnesses=["codex"])
    assert {r.key["model"] for r in report.rollups["model_effort"]} == {"model-a", "model-b"}
    window = get_report(harnesses=["codex"], start="2026-10-06T12:01:00Z")
    assert window.totals.model_requests == 1
    assert window.sessions[0].counts["assistant_turns"] == 0


def test_malformed_counter_gap_prevents_exact_attribution(corpus):
    _, codex = corpus
    path = roll(codex, PARENT, None, meta(), context(), cumulative(100), cumulative(200, ordinal=3))
    lines = path.read_text().splitlines()
    lines.insert(3, "{broken}")
    path.write_text("\n".join(lines) + "\n")
    detail = get_observations(session=PARENT, harnesses=["codex"])
    assert detail.observations[-1].quality == "unattributed"
    assert detail.observations[-1].model is None
    assert detail.coverage["malformed_records"] == 1


def test_fork_with_unrecoverable_cumulative_baseline_does_not_charge_parent(corpus):
    _, codex = corpus
    roll(codex, CHILD, None, meta(CHILD, parent_thread_id=PARENT), context(), cumulative(100))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 0
    assert "inherited_cumulative_baseline_unavailable" in report.warnings


def test_prefix_collision_rejected_and_harness_qualified_identity_resolves(corpus):
    root, _ = corpus
    second = PARENT[:8] + "-9999-4999-8999-999999999999"
    write(root / "project" / f"{PARENT}.jsonl", claude())
    write(root / "project" / f"{second}.jsonl", claude("request-2", sid=second))
    with pytest.raises(ValueError, match="Ambiguous"):
        get_report(sessions=[PARENT[:8]], harnesses=["claude"])
    assert get_report(sessions=["claude:" + PARENT], harnesses=["claude"]).session_count == 1


def test_tool_errors_interrupts_and_completion_are_distinct(corpus):
    _, codex = corpus
    items = [meta(), context(), response(),
        {"type": "response_item", "ordinal": 3, "timestamp": TS, "payload": {"type": "function_call", "call_id": "call-1", "name": "synthetic"}},
        {"type": "response_item", "ordinal": 4, "timestamp": TS, "payload": {"type": "function_call_output", "call_id": "call-1", "is_error": True, "output": "synthetic error"}},
        {"type": "event_msg", "ordinal": 5, "timestamp": TS, "payload": {"type": "turn_aborted"}},
        {"type": "event_msg", "ordinal": 6, "timestamp": TS, "payload": {"type": "task_complete"}}]
    roll(codex, PARENT, None, *items)
    report = get_report(harnesses=["codex"])
    row = report.sessions[0]
    assert row.counts["tool_invocations"] == 1
    assert row.lifecycle_counts == {"interruption": 1, "tool_error": 1, "turn_completed": 1}
    assert row.completion_state == "workflow_completion_unavailable"
    assert report.bounds.active_work_ms is None


def test_elapsed_span_sum_and_overlap_are_separate(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), response(timestamp="2026-10-06T12:02:00Z"))
    roll(codex, CHILD, None, meta(CHILD), context(), response("child", timestamp="2026-10-06T12:01:00Z"))
    report = get_report(harnesses=["codex"])
    assert report.bounds.elapsed_span_ms == 120000
    assert report.bounds.sum_session_spans_ms == 180000
    assert report.bounds.overlap_ms == 60000


def test_structured_attribution_is_not_string_coerced(corpus):
    from cc_explorer.param_repair import repair_arguments
    async def schema():
        return (await mcp.get_tool("get_usage_report")).parameters
    data = {"attribution": '[{"session": "synthetic"}]'}
    assert repair_arguments(data, asyncio.run(schema())) == data


def test_inconsistent_codex_categories_excluded(corpus):
    _, codex = corpus
    bad = response()
    bad["payload"]["usage"]["cached_input_tokens"] = 101
    roll(codex, PARENT, None, meta(), context(), bad)
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 0
    assert report.coverage["conflicting_sessions"] == 1


def test_retained_cumulative_branch_uses_cutoff_baseline(corpus):
    _, codex = corpus
    base_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    new_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    old = roll(codex, PARENT, base_id, meta(), context(), cumulative(100), cumulative(200, ordinal=3))
    cutoff = sum(len(line) for line in old.read_bytes().splitlines(keepends=True)[:3])
    roll(codex, PARENT, new_id, meta(history_base={"thread_id": base_id, "end_byte_offset": cutoff}), context(), cumulative(150))
    report = get_report(harnesses=["codex"])
    # Old tail remains consumed; new branch adds only 150 - cutoff baseline 100.
    assert subtotal(report, "output") == 125
    assert report.coverage["complete_observed_usage"] is False


def test_copy_and_baseline_reads_share_one_snapshot(corpus, monkeypatch):
    from cc_explorer import usage_sources
    _, codex = corpus
    base_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    new_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    old = roll(codex, PARENT, base_id, meta(), context(), cumulative(100))
    roll(codex, PARENT, new_id, meta(history_base={"thread_id": base_id, "end_byte_offset": old.stat().st_size}), context(), cumulative(200))
    calls = []
    original = usage_sources.snapshot
    def record(path, root):
        calls.append(path)
        return original(path, root)
    monkeypatch.setattr(usage_sources, "snapshot", record)
    get_report(harnesses=["codex"])
    assert calls.count(old) == 1


def test_recoverable_missing_stream_fragment_does_not_hide_coverage(corpus):
    root, _ = corpus
    first = claude()
    del first["message"]["usage"]
    first["message"]["stop_reason"] = None
    write(root / "project" / f"{PARENT}.jsonl", first, claude())
    report = get_report(harnesses=["claude"])
    assert report.coverage["missing_usage_records"] == 1
    assert report.coverage["complete_observed_usage"] is True
    assert subtotal(report, "output") == 5


def test_excluded_source_counts_cover_workload_selection(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude())
    write(root / "project" / f"{CHILD}.jsonl", claude("other", sid=CHILD))
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    assert report.coverage["discovered_sources"] == 2
    assert report.coverage["included_sources"] == 1
    assert report.coverage["excluded_sources"] == 1


def test_fork_new_requests_do_not_require_recovered_parent_history(corpus):
    _, codex = corpus
    roll(codex, CHILD, None, meta(CHILD, forked_from_id=PARENT, forked_from_ordinal_exclusive=3),
         context(ordinal=1), response("inherited", ordinal=2), context(ordinal=3), response("new-fork", ordinal=4))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 50
    assert report.totals.model_requests == 1


def test_explicit_active_duration_is_lower_bound_separate_from_elapsed(corpus):
    root, _ = corpus
    duration = {"type": "system", "uuid": "duration-1", "timestamp": "2026-10-06T12:01:00Z", "cwd": "/repo/example", "sessionId": PARENT,
                "subtype": "turn_duration", "durationMs": 1200}
    write(root / "project" / f"{PARENT}.jsonl", claude(), duration, duration)
    report = get_report(harnesses=["claude"])
    assert report.bounds.elapsed_span_ms == 60000
    assert report.bounds.recorded_active_session_work_ms == 1200
    assert report.bounds.active_work_ms is None


def test_synthetic_assistant_does_not_establish_request(corpus):
    root, _ = corpus
    row = claude()
    row["message"]["model"] = "<synthetic>"
    write(root / "project" / f"{PARENT}.jsonl", row)
    report = get_report(harnesses=["claude"])
    assert report.totals.model_requests is None
    assert report.totals.model_requests_lower_bound == 0


def test_unmeasured_failure_attempt_does_not_claim_exact_request_count(corpus):
    root, _ = corpus
    error = {"type": "system", "uuid": "error-1", "timestamp": TS, "cwd": "/repo/example", "sessionId": PARENT,
             "subtype": "api_error", "content": "synthetic"}
    write(root / "project" / f"{PARENT}.jsonl", claude(), error)
    report = get_report(harnesses=["claude"])
    assert report.totals.model_requests is None
    assert report.totals.model_requests_lower_bound == 1
    assert report.coverage["unmeasured_failure_attempts_present"] is True
    assert report.coverage["complete_observed_usage"] is False


def test_nested_only_agent_report_identity_roundtrips_to_observations(corpus):
    root, _ = corpus
    nested = claude("nested-only", sid=PARENT)
    progress = {"type": "progress", "timestamp": TS, "cwd": "/repo/example",
                "data": {"type": "agent_progress", "agentId": CHILD, "message": nested}}
    write(root / "project" / f"{PARENT}.jsonl", claude(), progress)
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    child = next(s for s in report.sessions if s.identity == "claude:" + CHILD)
    detail = get_observations(session=child.identity, harnesses=["claude"])
    assert detail.totals == child.totals
    assert len(detail.observations) == 1
    assert detail.observations[0].identity == "claude:request:nested-only"


def test_shared_claude_history_has_unknown_owner_and_scope_sensitive_increment(corpus):
    root, _ = corpus
    # A copy's path or timestamp cannot prove which session incurred the request.
    write(root / "z-original" / f"{PARENT}.jsonl", claude("shared"))
    write(root / "a-copy" / f"{CHILD}.jsonl", claude("shared", sid=CHILD), claude("new", sid=CHILD))
    copy = get_report(sessions=[CHILD], harnesses=["claude"])
    assert subtotal(copy, "output") == 5
    assert copy.coverage["selection_attribution_uncertain_observations"] == 1
    assert not copy.coverage["complete_observed_usage"]
    both = get_report(sessions=[PARENT, CHILD], harnesses=["claude"])
    assert subtotal(both, "output") == 10
    assert both.coverage["complete_observed_usage"]
    assert not both.coverage["ownership_complete"]
    unknown = next(row for row in both.rollups["role"] if row.key["role"] is None)
    assert unknown.totals.categories["output"].observed_subtotal == 10
    detail = get_observations(session=CHILD, harnesses=["claude"])
    shared = next(o for o in detail.observations if o.request_id == "shared")
    assert shared.session is None and shared.increment is None
    assert shared.candidate_sessions == ["claude:" + PARENT, "claude:" + CHILD]
    assert len(shared.sources) == 2


def test_request_holder_index_refreshes_changed_and_removed_copies(corpus):
    root, _ = corpus
    first = write(root / "project" / f"{PARENT}.jsonl", claude("indexed"))
    assert subtotal(get_report(sessions=[PARENT], harnesses=["claude"]), "output") == 5
    copy = write(root / "project" / f"{CHILD}.jsonl", claude("indexed", sid=CHILD))
    assert subtotal(get_report(sessions=[PARENT], harnesses=["claude"]), "output") == 0
    copy.unlink()
    assert subtotal(get_report(sessions=[PARENT], harnesses=["claude"]), "output") == 5
    assert first.exists()


def test_mixed_codex_native_formats_retain_preupgrade_consumption(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), cumulative(1000), response("post-upgrade", 100, 3), cumulative(1100, 4))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 550
    assert subtotal(report, "uncached_input") == 770
    assert "cumulative_before_response_record_format" in report.warnings
    assert not report.coverage["complete_observed_usage"]
    detail = get_observations(session=PARENT, harnesses=["codex"])
    assert [o.increment.output if o.increment else None for o in detail.observations] == [500, 50, None]


def test_discovery_noise_does_not_poison_narrow_coverage(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude("complete"))
    write(root / "unrelated" / f"{CHILD}.jsonl", {"type": "system"})
    write(root / "project" / PARENT / "workflow" / "journal.jsonl", {"type": "workflow_step", "cwd": "/repo/example"})
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    assert report.coverage["complete_observed_usage"]
    assert report.coverage["excluded_source_reasons"]["discovery_identity_or_layout_unavailable"] == 2
    assert not get_report(harnesses=["claude"]).coverage["complete_observed_usage"]


def test_large_report_is_bounded_and_summary_first(corpus):
    root, _ = corpus
    for index in range(300):
        sid = f"{index:08x}-0000-4000-8000-000000000000"
        write(root / "project" / f"{sid}.jsonl", claude(f"request-{index}", sid=sid))
    report = get_report(harnesses=["claude"], limit=1, rollup_limit=1)
    payload = report.model_dump_json()
    assert report.session_count == 300 and len(report.sessions) == 1
    assert len(payload) < 20000
    assert "members" not in report.scope and "session" not in report.rollups
    assert payload.index('"next_offset"') < payload.index('"rollups"')
    assert subtotal(report, "output") == 1500


def test_configuration_tier_speed_tool_counts_and_category_semantics(corpus):
    root, codex = corpus
    settings = {"type": "event_msg", "ordinal": 1, "timestamp": TS,
        "payload": {"type": "thread_settings_applied", "thread_id": PARENT,
                    "thread_settings": {"model": "model-b", "reasoning_effort": "high", "service_tier": "fast"}}}
    turn = context("model-b", "high", 2)
    turn["payload"]["turn_id"] = "turn-before-change"
    settings2 = {**settings, "ordinal": 3, "payload": {**settings["payload"], "thread_settings": {"model": "model-c", "reasoning_effort": "low", "service_tier": "default"}}}
    roll(codex, PARENT, None, meta(), settings, turn, settings2, response("configured", ordinal=4, turn_id="turn-before-change"))
    c = claude("configured-claude")
    c["message"]["usage"].update(service_tier="standard", speed="fast", server_tool_use={"web_search_requests": 2})
    write(root / "project" / f"{CHILD}.jsonl", c)
    report = get_report()
    row = next(row for row in report.rollups["configuration"] if row.key["harness"] == "codex")
    assert row.key == {"harness": "codex", "model": "model-b", "effort": "high", "service_tier": "fast", "speed": None}
    assert report.totals.server_tool_use["web_search_requests"].observed_subtotal == 2
    assert report.category_semantics["included_breakdowns"]["output"] == ["reasoning_output"]
    assert sum(subtotal(report, k) for k in report.category_semantics["disjoint_token_categories"]) == 215


def test_malformed_counter_gap_is_local_and_zero_does_not_erase_uncertainty(corpus):
    _, codex = corpus
    path = roll(codex, PARENT, None, meta(), context(), cumulative(100), cumulative(200, 3))
    with path.open("a") as stream:
        stream.write('[]\n')
    detail = get_observations(session=PARENT, harnesses=["codex"])
    assert detail.observations[1].model == "model-a" and detail.observations[1].increment.output == 50
    with path.open("a") as stream:
        stream.write(json.dumps(cumulative(200, 5)) + "\n")
    detail = get_observations(session=PARENT, harnesses=["codex"])
    assert detail.observations[-1].quality == "unattributed"
    assert detail.observations[-1].increment.output == 0
    assert detail.coverage["malformed_records"] == 1


def test_window_ignores_sessions_without_in_window_activity_and_excludes_end(corpus):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude("earlier", timestamp="2026-10-06T11:00:00Z"))
    write(root / "project" / f"{CHILD}.jsonl", claude("included"), claude("end", timestamp="2026-10-06T13:00:00Z"))
    report = get_report(harnesses=["claude"], start=TS, end="2026-10-06T13:00:00Z")
    assert subtotal(report, "output") == 5
    assert report.coverage["complete_observed_usage"]
    assert report.coverage["missing_usage_sessions"] == 0
    assert report.coverage["no_in_window_activity_sessions"] == 1


@pytest.mark.parametrize("parent,child", [(PARENT, CHILD), (CHILD, PARENT)])
def test_dispatch_evidence_labels_children_independent_of_load_order(corpus, parent, child):
    root, _ = corpus
    dispatch = {"type": "user", "timestamp": TS, "cwd": "/repo/example", "uuid": "dispatch", "sessionId": parent,
        "message": {"role": "user", "content": [{"type": "text", "text": "synthetic"}]}, "toolUseResult": {"agentId": child}}
    write(root / "project" / f"{parent}.jsonl", claude("parent", sid=parent), dispatch)
    write(root / "project" / parent / "subagents" / f"agent-{child}.jsonl", claude("child", sid=parent))
    report = get_report(sessions=[parent], harnesses=["claude"])
    child_row = next(row for row in report.sessions if row.identity == "claude:" + child)
    assert child_row.relationship == "dispatched"
    assert "dispatched_child_source_unavailable" not in child_row.reasons
    assert subtotal(report, "output") == 10 and report.coverage["complete_observed_usage"]


def test_progress_dispatch_is_measured_and_parent_drilldown_excludes_descendants(corpus):
    root, _ = corpus
    child = claude("nested-child", sid=PARENT)
    progress = {"type": "progress", "timestamp": TS, "cwd": "/repo/example", "data": {"agentId": CHILD, "message": child}}
    dispatch = {"type": "user", "timestamp": TS, "cwd": "/repo/example", "uuid": "dispatch", "sessionId": PARENT,
        "message": {"role": "user", "content": "synthetic"}, "toolUseResult": {"agentId": CHILD}}
    write(root / "project" / f"{PARENT}.jsonl", claude("parent"), dispatch, progress)
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    assert subtotal(report, "output") == 10 and report.coverage["complete_observed_usage"]
    assert get_report(sessions=[PARENT, CHILD], harnesses=["claude"]).totals == report.totals
    detail = get_observations(session=PARENT, harnesses=["claude"])
    assert subtotal(detail, "output") == 5
    assert any("descendants_disabled" in o.reasons for o in detail.observations)


@pytest.mark.parametrize("bad", [[], "bad", 7])
def test_malformed_claude_messages_and_conversion_markers_do_not_crash(corpus, bad):
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", [],
        {"type": "x-converter-provenance", "x_converter": bad},
        {"type": "assistant", "cwd": "/repo/example", "message": bad}, claude("valid"))
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert report.coverage["malformed_records"] >= 2
    assert not report.coverage["complete_observed_usage"]


@pytest.mark.parametrize("bad", [[], {}, "wrong", True])
def test_invalid_history_cutoff_and_payload_shapes_do_not_crash(corpus, bad):
    _, codex = corpus
    base_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    roll(codex, PARENT, base_id, meta(), {"type": "event_msg", "payload": []}, cumulative(100))
    roll(codex, CHILD, None, meta(CHILD, history_base={"thread_id": base_id, "end_byte_offset": bad}, agent_role=[]),
        {"type": "event_msg", "payload": {"type": []}}, response("safe", thread_id=CHILD))
    report = get_report(sessions=[CHILD], harnesses=["codex"])
    assert subtotal(report, "output") == 50
    assert "invalid_history_cutoff" in report.warnings
    assert report.coverage["malformed_records"] >= 2


def test_real_rollback_lifecycle_preserves_num_turns(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), response(),
        {"type": "event_msg", "timestamp": TS, "payload": {"type": "thread_rolled_back", "num_turns": 2}})
    detail = get_observations(session=PARENT, harnesses=["codex"])
    assert detail.lifecycle[0].kind == "explicit_revert"
    assert detail.lifecycle[0].details == {"num_turns": 2}


def test_project_scope_resolves_colliding_prefixes(corpus):
    root, _ = corpus
    other = PARENT[:-1] + "2"
    write(root / "one" / f"{PARENT}.jsonl", claude("one"))
    c = claude("two", sid=other)
    c["cwd"] = "/repo/other"
    write(root / "two" / f"{other}.jsonl", c)
    report = get_report(sessions=[PARENT[:8]], projects=["/repo/example"], harnesses=["claude"])
    assert report.sessions[0].identity == "claude:" + PARENT
    with pytest.raises(ValueError, match="Ambiguous"):
        get_report(sessions=[PARENT[:8]], harnesses=["claude"])


def test_null_counter_info_child_owner_unknown_and_copy_conflicts(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), {"type": "event_msg", "payload": {"type": "token_count", "info": None}}, response("shared"))
    copy = codex / "archived_sessions" / f"copy-{PARENT}.jsonl"
    write(copy, meta(), context(), response("shared"))
    assert subtotal(get_report(sessions=[PARENT], harnesses=["codex"]), "output") == 50
    write(copy, meta(), context(), response("shared", 200))
    report = get_report(sessions=[PARENT], harnesses=["codex"])
    assert subtotal(report, "output") == 0 and report.coverage["conflicting_sessions"] == 1
    roll(codex, CHILD, None, meta(CHILD, parent_thread_id=PARENT), context(), response("unknown-owner"))
    child = get_report(sessions=[CHILD], harnesses=["codex"])
    assert subtotal(child, "output") == 0
    assert "inherited_request_boundary_or_owner_unavailable" in child.warnings


def test_cumulative_counter_copy_conflict_is_not_first_wins(corpus):
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), cumulative(100))
    write(codex / "archived_sessions" / f"copy-{PARENT}.jsonl", meta(), context(), cumulative(200))
    report = get_report(harnesses=["codex"])
    assert subtotal(report, "output") == 0
    assert report.totals.uncertain_observations == 1


def test_inconsistent_streaming_fragments_stay_conflict(corpus):
    root, _ = corpus
    a, b = claude(output=2), claude(output=5)
    b["message"]["usage"]["input_tokens"] = 99
    write(root / "project" / f"{PARENT}.jsonl", a, b)
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 0
    assert "inconsistent_streaming_fragments" in report.warnings


def test_time_unknown_and_fallback_iterations_are_explicit_gaps(corpus):
    root, _ = corpus
    c = claude("fallback", timestamp=None)
    c["message"]["usage"]["iterations"] = [{"type": "message", "input_tokens": 50}, {"type": "fallback_message", "input_tokens": 10}]
    write(root / "project" / f"{PARENT}.jsonl", c)
    report = get_report(harnesses=["claude"], start=TS)
    assert subtotal(report, "output") == 0
    assert "window_time_unavailable" in report.warnings
    lifetime = get_report(harnesses=["claude"])
    assert subtotal(lifetime, "output") == 5 and not lifetime.coverage["complete_observed_usage"]
    assert "fallback_iteration_usage_unaccounted" in lifetime.warnings


def test_sources_merge_diagnostics_and_independent_pages(corpus, monkeypatch):
    from cc_explorer.usage_models import SourceCoverage
    from cc_explorer.usage_sources import merge_source_coverage
    merged = merge_source_coverage([SourceCoverage(path="p", root="r", reasons=["x"]),
        SourceCoverage(path="p", root="r", reasons=["x"], malformed_records=2, status="partial")])
    assert merged[0].malformed_records == 2 and merged[0].status == "partial"
    _, codex = corpus
    roll(codex, PARENT, None, meta(), context(), response("a"), response("b", ordinal=3))
    write(codex / "archived_sessions" / f"copy-{PARENT}.jsonl", meta(), context(), response("a"), response("b", ordinal=3))
    first = get_observations(session=PARENT, harnesses=["codex"], limit=1, source_limit=1)
    second = get_observations(session=PARENT, harnesses=["codex"], limit=1, source_limit=1, source_offset=1)
    assert first.observations == second.observations
    assert first.sources[0].path != second.sources[0].path
    assert first.next_offset == first.next_source_offset == 1
    assert first.totals == second.totals


def test_unreadable_selected_source_affects_report_coverage(corpus, monkeypatch):
    _, codex = corpus
    path = roll(codex, PARENT, None, meta(), context(), response())
    original = Path.open
    calls = 0
    def fail_on_snapshot(self, *args, **kwargs):
        nonlocal calls
        if self == path:
            calls += 1
            if calls > 1:
                raise PermissionError("synthetic")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "open", fail_on_snapshot)
    report = get_report(sessions=[PARENT], harnesses=["codex"])
    assert report.coverage["unreadable_sources"] == 1
    assert not report.coverage["complete_observed_usage"]


def test_prefix_and_full_id_parse_only_matching_transcripts(corpus, monkeypatch):
    root, _ = corpus
    from cc_explorer.providers.claude import ClaudeProvider
    paths = []
    for index in range(20):
        sid = f"{index:08x}-0000-4000-8000-000000000000"
        paths.append(write(root / "project" / f"{sid}.jsonl", claude(f"unique-{index}", sid=sid)))
    original = ClaudeProvider.load_usage
    parsed = []
    def record_load(self, ref, snapshots=None):
        parsed.extend(ref.paths)
        return original(self, ref, snapshots)
    monkeypatch.setattr(ClaudeProvider, "load_usage", record_load)
    full = get_report(sessions=[paths[0].stem], harnesses=["claude"])
    prefix = get_report(sessions=[paths[0].stem[:8]], harnesses=["claude"])
    assert full.totals == prefix.totals
    assert set(parsed) == {paths[0]}


def test_mcp_schema_windows_identity_and_page_contract(corpus):
    from datetime import datetime
    root, _ = corpus
    write(root / "project" / f"{PARENT}.jsonl", claude())
    async def schema():
        return (await mcp.get_tool("get_usage_report")).parameters
    properties = asyncio.run(schema())["properties"]
    assert "after" in properties and "before" in properties and "start" not in properties
    assert properties["offset"]["minimum"] == 0
    assert get_usage_report(harnesses=["claude"], after=datetime.fromisoformat(TS.replace("Z", "+00:00"))).totals.categories["output"].observed_subtotal == 5
    with pytest.raises(Exception, match="timezone"):
        get_usage_report(harnesses=["claude"], after=datetime(2026, 10, 6))


@pytest.mark.parametrize("count", [1, 2])
def test_standard_message_iterations_are_breakdowns_not_missing_work(corpus, count):
    root, _ = corpus
    c = claude("iterations")
    usage = c["message"]["usage"]
    usage["iterations"] = [{"type": "message", "input_tokens": 10//count,
        "cache_read_input_tokens": 20//count, "cache_creation_input_tokens": 30//count,
        "output_tokens": 5 if count == 1 else (2 if index == 0 else 3)} for index in range(count)]
    write(root / "project" / f"{PARENT}.jsonl", c)
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5 and report.coverage["complete_observed_usage"]


@pytest.mark.parametrize("kind", ["compaction", "advisor_message", "unknown"])
def test_separate_or_unknown_iteration_usage_remains_partial(corpus, kind):
    root, _ = corpus
    c = claude("separate-iteration")
    c["message"]["usage"]["iterations"] = [{"type": kind, "input_tokens": 1000, "output_tokens": 100}]
    write(root / "project" / f"{PARENT}.jsonl", c)
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert not report.coverage["complete_observed_usage"]



def test_branch_metadata_has_independent_complete_detail_pages(corpus):
    _, codex = corpus
    for index in range(22):
        execution = f"{index:08x}-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        roll(codex, PARENT, execution, meta(), context(), response(f"branch-{index}"))
    report = get_report(sessions=[PARENT], harnesses=["codex"])
    assert len(report.sessions[0].branches) == 20 and report.sessions[0].provenance["branch_count"] == 22
    detail = get_observations(session=PARENT, harnesses=["codex"], branch_limit=20)
    tail = get_observations(session=PARENT, harnesses=["codex"], branch_limit=20, branch_offset=20)
    assert detail.branch_count == 22 and detail.next_branch_offset == 20
    assert len(tail.branches) == 2 and tail.next_branch_offset is None
    assert {b["execution"] for b in detail.branches}.isdisjoint(b["execution"] for b in tail.branches)
    assert detail.totals == tail.totals == report.totals



@pytest.mark.parametrize("bad", [[], ["invalid"], None, 7, False, ""])
def test_malformed_baseline_payload_is_reported_with_valid_cutoff(corpus, bad):
    _, codex = corpus
    base_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    base = roll(codex, PARENT, base_id, meta(), {"type": "event_msg", "payload": bad}, cumulative(100))
    roll(codex, CHILD, None, meta(CHILD, history_base={"thread_id": base_id, "end_byte_offset": base.stat().st_size}),
         context(), cumulative(200))
    detail = get_observations(session=CHILD, harnesses=["codex"])
    baseline = next(source for source in detail.sources if source.path == str(base))
    assert baseline.malformed_records == 1
    assert detail.coverage["malformed_records"] == 1
    assert subtotal(detail, "output") == 50
    assert not detail.coverage["complete_observed_usage"]



def test_partial_holder_cache_diagnostics_are_scoped_to_requested_identities(corpus):
    root, _ = corpus
    partial = write(root / "project" / f"{PARENT}.jsonl", claude("partial-request"))
    with partial.open("a") as stream:
        stream.write('{"type":')
    assert not get_report(sessions=[PARENT], harnesses=["claude"]).coverage["complete_observed_usage"]
    write(root / "project" / f"{CHILD}.jsonl", claude("other-complete", sid=CHILD))
    complete = get_report(sessions=[CHILD], harnesses=["claude"])
    assert complete.coverage["complete_observed_usage"]
    assert subtotal(complete, "output") == 5
    assert not get_report(sessions=[PARENT], harnesses=["claude"]).coverage["complete_observed_usage"]


def test_streaming_server_tool_count_recovers_null_fragment(corpus):
    root, _ = corpus
    first, last = claude(output=2), claude(output=5)
    first["message"]["usage"]["server_tool_use"] = {"web_search_requests": None}
    last["message"]["usage"]["server_tool_use"] = {"web_search_requests": 2}
    write(root / "project" / f"{PARENT}.jsonl", first, last)
    report = get_report(harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert report.totals.server_tool_use["web_search_requests"].observed_subtotal == 2


@pytest.mark.parametrize("original,resumed", [(PARENT, CHILD), (CHILD, PARENT)])
def test_copied_dispatch_cannot_claim_original_child(corpus, original, resumed):
    root, _ = corpus
    dispatch = {"type": "user", "timestamp": TS, "cwd": "/repo/example",
        "uuid": "original-dispatch", "sessionId": original,
        "message": {"role": "user", "content": "synthetic"}, "toolUseResult": {"agentId": THIRD}}
    write(root / "project" / f"{original}.jsonl", claude("original", sid=original), dispatch)
    write(root / "project" / original / "subagents" / f"agent-{THIRD}.jsonl",
        claude("child-request", output=7, sid=original))
    write(root / "project" / f"{resumed}.jsonl", claude("original", sid=resumed),
        {**dispatch, "sessionId": resumed}, claude("new", output=3, sid=resumed))

    copy = get_report(sessions=[resumed], harnesses=["claude"])
    assert subtotal(copy, "output") == 3
    assert copy.session_count == 1
    assert "child_parent_conflicts_with_storage" in copy.warnings
    assert not copy.coverage["complete_observed_usage"]
    original_only = get_report(sessions=[original], harnesses=["claude"])
    assert subtotal(original_only, "output") == 7  # Shared original request has unknown owner.
    both = get_report(sessions=[original, resumed], harnesses=["claude"])
    assert subtotal(both, "output") == 15
    assert both.coverage["complete_observed_usage"]
    assert not both.coverage["ownership_complete"]
    child = next(row for row in both.sessions if row.identity == "claude:" + THIRD)
    assert child.parent == "claude:" + original
    assert child.relationship == "dispatched"


@pytest.mark.parametrize("progress_only", [False, True])
def test_copied_child_links_without_storage_parent_preserve_ambiguity(corpus, progress_only):
    root, _ = corpus
    child = claude("child-request", output=7)
    link = {"type": "progress", "timestamp": TS, "cwd": "/repo/example",
        "data": {"agentId": THIRD, "message": child}} if progress_only else {
        "type": "user", "uuid": "original-dispatch", "timestamp": TS, "cwd": "/repo/example",
        "message": {"role": "user", "content": "synthetic"}, "toolUseResult": {"agentId": THIRD}}
    write(root / "project" / f"{PARENT}.jsonl", claude("original"), link)
    write(root / "project" / f"{CHILD}.jsonl", claude("original", sid=CHILD), link,
        claude("new", output=3, sid=CHILD))
    if not progress_only:
        write(root / "project" / f"{THIRD}.jsonl", child)
    copy = get_report(sessions=[CHILD], harnesses=["claude"])
    assert subtotal(copy, "output") == 3
    assert copy.session_count == 1
    assert "copied_child_link_outside_selected_workload" in copy.warnings
    both = get_report(sessions=[PARENT, CHILD], harnesses=["claude"])
    assert subtotal(both, "output") == 15
    child_row = next(row for row in both.sessions if row.identity == "claude:" + THIRD)
    assert child_row.parent is None
    assert child_row.relationship == "ambiguous_dispatch_parent"
    assert any(branch.get("candidate_parents") == ["claude:" + PARENT, "claude:" + CHILD]
        for branch in child_row.branches)


def test_child_link_without_stable_identity_cannot_expand_workload(corpus):
    root, _ = corpus
    link = {"type": "user", "timestamp": TS, "cwd": "/repo/example",
        "message": {"role": "user", "content": "synthetic"}, "toolUseResult": {"agentId": THIRD}}
    write(root / "project" / f"{PARENT}.jsonl", claude(), link)
    write(root / "project" / f"{THIRD}.jsonl", claude("unattributed-child", output=7))
    report = get_report(sessions=[PARENT], harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert report.session_count == 1
    assert "child_link_identity_unavailable" in report.warnings
    assert not report.coverage["complete_observed_usage"]


def test_conflicting_child_storage_parents_cannot_be_resolved_by_one_dispatch(corpus):
    root, _ = corpus
    dispatch = {"type": "user", "timestamp": TS, "cwd": "/repo/example", "uuid": "dispatch",
        "message": {"role": "user", "content": "synthetic"}, "toolUseResult": {"agentId": THIRD}}
    write(root / "project" / f"{PARENT}.jsonl", claude("parent"), dispatch)
    write(root / "project" / f"{CHILD}.jsonl", claude("other-parent", sid=CHILD))
    for parent in (PARENT, CHILD):
        write(root / "project" / parent / "subagents" / f"agent-{THIRD}.jsonl",
            claude("child-request", output=7, sid=parent))
    one = get_report(sessions=[PARENT], harnesses=["claude"])
    assert subtotal(one, "output") == 5
    assert one.session_count == 1
    assert "copied_child_link_outside_selected_workload" in one.warnings
    both = get_report(sessions=[PARENT, CHILD], harnesses=["claude"])
    assert subtotal(both, "output") == 17
    child_row = next(row for row in both.sessions if row.identity == "claude:" + THIRD)
    assert child_row.parent is None
    assert child_row.relationship == "ambiguous_dispatch_parent"


def test_holder_cache_refresh_patterns_are_bounded_to_current_ids(corpus, monkeypatch, tmp_path):
    import cc_explorer.usage_index as usage_index

    root, _ = corpus
    monkeypatch.setenv("CC_EXPLORER_USAGE_INDEX", str(tmp_path / "bounded-cache.sqlite3"))
    history = [claude(f"historical-{index}") for index in range(50)]
    original = write(root / "project" / f"{PARENT}.jsonl", *history)
    current = write(root / "project" / f"{CHILD}.jsonl", claude("current-only", sid=CHILD))
    assert subtotal(get_report(sessions=[PARENT], harnesses=["claude"]), "output") == 250
    assert subtotal(get_report(sessions=[CHILD], harnesses=["claude"]), "output") == 5
    scanner, pattern_calls = usage_index.make_scanner, []

    class RecordedScanner:
        def files_with_match(self, patterns, files):
            pattern_calls.append(patterns)
            return scanner().files_with_match(patterns, files)

    monkeypatch.setattr(usage_index, "make_scanner", RecordedScanner)
    write(original, *history, claude("unrelated-new"))
    assert subtotal(get_report(sessions=[CHILD], harnesses=["claude"]), "output") == 5
    assert pattern_calls and all(patterns == ["current\\-only"] for patterns in pattern_calls)
    pattern_calls.clear()
    # The historic query's invalidation survives the intervening unrelated query:
    # a new copy must still find its original holder in the previously changed file.
    write(current, claude("current-only", sid=CHILD), claude("historical-0", sid=CHILD))
    report = get_report(sessions=[CHILD], harnesses=["claude"])
    assert subtotal(report, "output") == 5
    assert report.coverage["selection_attribution_uncertain_observations"] == 1
    assert pattern_calls and max(map(len, pattern_calls)) <= 2
    assert all(set(patterns) <= {"current\\-only", "historical\\-0"} for patterns in pattern_calls)
