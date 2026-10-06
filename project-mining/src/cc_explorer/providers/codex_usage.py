"""Codex execution accounting: native response usage first, cumulative evidence second."""
from __future__ import annotations

from dataclasses import replace
from functools import lru_cache
from pathlib import Path


from .._codex_resume import NOISE_PREFIXES
from ..usage_models import Observation, Signal, SessionUsage, UsageDiscovery
from ..usage_sources import CODEX_SEMANTICS, as_dict, native_tokens, snapshot, timestamp, walk_sources
from ..models import classify_failure
from ..utils import PrefixId
from .base import Harness, ProviderSession, project_identity


@lru_cache(maxsize=8192)
def _cached_meta(path, size, mtime):
    from .codex import CodexProvider
    meta = CodexProvider._read_meta(path)
    return meta.payload if meta else None


def _meta(path: Path) -> dict | None:
    try:
        stat = path.stat()
        return _cached_meta(path, stat.st_size, stat.st_mtime_ns)
    except OSError:
        return None


def discover(home: Path) -> UsageDiscovery:
    result = UsageDiscovery()
    grouped: dict[str, ProviderSession] = {}
    by_rollout: dict[str, list[str]] = {}
    path_roots: dict[str, str] = {}
    identity = lru_cache(None)(project_identity)
    for name in ("sessions", "archived_sessions"):
        root = home / name
        paths, coverage = walk_sources(root, "codex")
        result.roots.append(coverage)
        for path in paths:
            meta = _meta(path)
            if meta is None:
                _, source = snapshot(path, root)
                if source.status != "unreadable":
                    source.status = "excluded"
                    source.malformed_records += 1
                source.reasons.append("session_metadata_unavailable")
                result.sources.append(source)
                continue
            sid = meta.get("id") or meta["session_id"]
            project, worktree = identity(meta["cwd"])
            src = as_dict(meta.get("source"))
            subagent = as_dict(src.get("subagent"))
            spawn = as_dict(subagent.get("thread_spawn"))
            parent = meta.get("parent_thread_id") or spawn.get("parent_thread_id")
            raw_role = meta.get("agent_role") or meta.get("agent_type") or spawn.get("agent_role") or meta.get("agent_path") or spawn.get("agent_path")
            role = meta.get("agent_role") or meta.get("agent_type") or spawn.get("agent_role") or meta.get("agent_path") or spawn.get("agent_path")
            role = role if isinstance(role, str) else None
            parent = parent if isinstance(parent, str) else None
            relation = "dispatched" if parent else "subagent_parent_unavailable" if subagent or meta.get("thread_source") == "subagent" else "independent"
            rollout = path.stem[-36:]
            by_rollout.setdefault(rollout, []).append(str(path))
            path_roots[str(path)] = str(root)
            prior = grouped.get(sid)
            if prior:
                metadata = prior.metadata
                metadata["physical_meta"][str(path)] = meta
                if (project, parent and f"codex:{parent}") != (prior.project_path, prior.parent_id):
                    metadata["discovery_conflict"] = True
                grouped[sid] = replace(prior, paths=prior.paths + (path,), source_roots=tuple(dict.fromkeys(prior.source_roots + (root,))))
            else:
                grouped[sid] = ProviderSession(PrefixId(sid), (path,), project, Harness.codex, worktree,
                    f"codex:{parent}" if parent else None, role, relation, (root,), {"physical_meta": {str(path): meta}, "invalid_role": raw_role is not None and role is None})
    for ref in grouped.values():
        ref.metadata["by_rollout"] = by_rollout
        ref.metadata["path_roots"] = path_roots
    result.sessions = list(grouped.values())
    return result


def _configuration(payload: dict) -> tuple[str | None, str | None]:
    model = payload.get("model")
    mode = as_dict(payload.get("collaboration_mode"))
    settings = as_dict(mode.get("settings"))
    effort = payload.get("effort") or payload.get("reasoning_effort") or settings.get("reasoning_effort")
    return model if isinstance(model, str) else None, effort if isinstance(effort, str) else None


def load(ref: ProviderSession, snapshots=None) -> SessionUsage:
    result = SessionUsage(ref)
    result.provenance = {"recorded_execution_host": None, "account": None, "unknown_reasons": {"recorded_execution_host": "unrecorded", "account": "unrecorded"}, "corpus_roots": [str(r) for r in ref.source_roots]}
    for path in sorted(ref.paths):
        root = next((r for r in ref.source_roots if path.is_relative_to(r)), path.parent)
        records, cov = snapshots.read(path, root) if snapshots else snapshot(path, root)
        cov.project = ref.project_path
        cov.owning_session = result.identity
        result.sources.append(cov)
        meta = as_dict(next((r.get("payload") for r, _ in records if r.get("type") == "session_meta"), {}))
        execution = path.stem[-36:]
        start = meta.get("subagent_history_start_ordinal")
        fork_start = meta.get("forked_from_ordinal_exclusive")
        if start is None and meta.get("forked_from_id"):
            start = fork_start
        history = as_dict(meta.get("history_base"))
        if start is not None and (not isinstance(start, int) or isinstance(start, bool) or start < 0):
            result.reasons.append("inherited_ordinal_unavailable")
            start = None
        if history and (not isinstance(history.get("end_byte_offset"), int) or isinstance(history.get("end_byte_offset"), bool) or history["end_byte_offset"] < 0):
            result.reasons.append("invalid_history_cutoff")
            cov.malformed_records += 1
        branch = {"execution": execution, "path": str(path), "history_base": history or None,
                  "forked_from_id": meta.get("forked_from_id"), "forked_from_ordinal_exclusive": fork_start,
                  "inherited_start_ordinal": start, "timestamp": meta.get("timestamp"),
                  "kind": "history_lineage" if history else "physical_execution",
                  "accounting_scope": "retained_own_execution_records"}
        result.branches.append(branch)
        if history and (not isinstance(history.get("thread_id"), str) or not ref.metadata.get("by_rollout", {}).get(history.get("thread_id"))):
            result.reasons.append("history_base_source_unavailable")
        if (meta.get("forked_from_id") or meta.get("parent_thread_id")) and start is None and not history:
            result.reasons.append("inherited_history_boundary_unrecorded")
        for key in ("execution_host", "hostname"):
            if isinstance(meta.get(key), str):
                result.provenance["recorded_execution_host"] = meta[key]
                result.provenance["unknown_reasons"].pop("recorded_execution_host", None)
        result.provenance["account"] = meta.get("creator_account_id")
        if result.provenance["account"]:
            result.provenance["unknown_reasons"].pop("account", None)
        # Per-response records are authoritative. Cumulative observations remain
        # drillable context/bookkeeping evidence and never add a second charge.
        first_response_line = next((loc.line for r, loc in records if r.get("type") == "token_usage_record" and as_dict(r.get("payload")).get("thread_id", ref.session_id.full) == ref.session_id.full), None)
        response_records = first_response_line is not None
        previous = None
        previous_line = 0
        previous_time = None
        previous_config = None
        previous_tier = None
        config = (None, None)
        tier = None
        turn_configs = {}
        config_changes = 0
        have_context = not any(r.get("type") == "turn_context" for r, _ in records)
        missing_before = False
        # A retained base can supply the predecessor for a new physical branch.
        if history and "invalid_history_cutoff" not in result.reasons:
            baselines = []
            for base_name in ref.metadata.get("by_rollout", {}).get(history.get("thread_id"), []) if isinstance(history.get("thread_id"), str) else []:
                base_path = Path(base_name)
                base_root = Path(ref.metadata.get("path_roots", {}).get(str(base_path), str(base_path.parent)))
                base_records, base_cov = snapshots.read(base_path, base_root) if snapshots else snapshot(base_path, base_root)
                result.sources.append(base_cov)
                final_baseline = None
                for item, loc in base_records:
                    cutoff = history.get("end_byte_offset")
                    if not isinstance(cutoff, int) or isinstance(cutoff, bool) or cutoff < 0:
                        result.reasons.append("invalid_history_cutoff")
                        break
                    if loc.byte_offset >= cutoff:
                        break
                    payload = item.get("payload", {})
                    if not isinstance(payload, dict) or (payload.get("type") is not None and not isinstance(payload.get("type"), str)):
                        if loc.line not in base_cov.malformed_lines:
                            base_cov.malformed_records += 1
                            base_cov.malformed_lines.append(loc.line)
                        continue
                    if item.get("type") == "event_msg" and payload.get("type") == "token_count":
                        native = as_dict(payload.get("info")).get("total_token_usage")
                        if isinstance(native, dict):
                            final_baseline = (native, timestamp(item.get("timestamp")))
                if final_baseline:
                    baselines.append(final_baseline)
            if baselines and all(b[0] == baselines[0][0] for b in baselines):
                previous, previous_time = baselines[-1]
                previous_config = (None, None)
            elif baselines:
                result.reasons.append("conflicting_history_baseline_copies")
        for record, loc in records:
            kind = record["type"]
            payload = record.get("payload", {})
            if not isinstance(payload, dict):
                cov.malformed_records += 1
                cov.malformed_lines.append(loc.line)
                missing_before = True
                continue
            time = timestamp(record.get("timestamp"))
            ordinal = record.get("ordinal")
            ordinal = ordinal if isinstance(ordinal, int) and not isinstance(ordinal, bool) else None
            event_type = payload.get("type")
            if event_type is not None and not isinstance(event_type, str):
                cov.malformed_records += 1
                cov.malformed_lines.append(loc.line)
                continue
            inherited = isinstance(start, int) and isinstance(ordinal, int) and ordinal < start and kind != "session_meta"
            request_thread = payload.get("thread_id") if kind == "token_usage_record" else None
            if isinstance(request_thread, str) and request_thread != ref.session_id.full:
                inherited = True
            if isinstance(start, int) and ordinal is None and kind != "session_meta":
                result.reasons.append("inherited_ordinal_unavailable")
                inherited = True
            if kind == "event_msg" and event_type == "thread_settings_applied" and payload.get("thread_id", ref.session_id.full) == ref.session_id.full:
                settings = as_dict(payload.get("thread_settings"))
                new = _configuration(settings)
                new_tier = settings.get("service_tier")
                new_tier = new_tier if isinstance(new_tier, str) else None
                if (new, new_tier) != (config, tier):
                    config_changes += 1
                config, tier = new, new_tier
            if kind == "turn_context":
                new = _configuration(payload)
                if new != config:
                    config_changes += 1
                config = new
                have_context = True
                if isinstance(payload.get("turn_id"), str):
                    turn_configs[payload["turn_id"]] = (config, tier)
            if inherited:
                cov.excluded_records += 1
            elif time is not None:
                result.times.append(time)
            if kind == "compacted":
                result.signals.append(Signal(identity=f"codex:{execution}:compaction:{ordinal if ordinal is not None else loc.line}", kind="compaction", time=time, source=loc))
            if kind == "token_usage_record" or (kind == "event_msg" and payload.get("type") == "token_count"):
                info = as_dict(payload.get("info"))
                native = payload.get("usage") if kind == "token_usage_record" else info.get("total_token_usage")
                if not isinstance(native, dict):
                    if kind == "token_usage_record":
                        cov.malformed_records += 1
                    else:
                        cov.missing_usage_records += 1
                    missing_before = True
                    continue
                tokens, prompt, reasons = native_tokens(native, "codex")
                request = payload.get("response_id") if kind == "token_usage_record" else None
                if not isinstance(request, str) or not request:
                    request = None
                event_key = ordinal if ordinal is not None else f"line:{loc.line}"
                oid = f"codex:response:{request}" if request else f"codex:{execution}:{kind}:{event_key}"
                observed_config, observed_tier = turn_configs.get(payload.get("turn_id"), (config, tier)) if isinstance(payload.get("turn_id"), str) else (config, tier)
                model, effort = observed_config
                quality = "partial" if reasons else "exact"
                increment = tokens
                interval_start = None
                counter_kind = "request" if kind == "token_usage_record" else "cumulative"
                if inherited:
                    quality, increment = "excluded", None
                    reasons.append("inherited_context_prefix")
                elif kind == "token_usage_record":
                    if request is None:
                        reasons.append("request_identity_unavailable")
                        quality = "partial"
                    if start is None and not history and (meta.get("parent_thread_id") or meta.get("forked_from_id")) and request_thread is None:
                        quality, increment = "excluded", None
                        reasons.append("inherited_request_boundary_or_owner_unavailable")
                elif response_records and loc.line >= first_response_line:
                    quality, increment = "excluded", None
                    reasons.append("cumulative_superseded_by_response_records")
                else:
                    interval_start = previous_time
                    if first_response_line is not None and loc.line < first_response_line:
                        reasons.append("cumulative_before_response_record_format")
                    if "context_or_inconsistent_total_not_consumption" in reasons:
                        quality, increment = "excluded", None
                        reasons.append("context_estimate_not_consumption")
                    elif previous is None:
                        # Preserve observed first-counter consumption as unassigned
                        # subtotal; it may span earlier time/model configurations.
                        quality = "unattributed"
                        reasons.append("initial_cumulative_baseline_unknown")
                        model = effort = observed_tier = None
                        if history or start is not None or meta.get("parent_thread_id") or meta.get("forked_from_id"):
                            quality, increment = "baseline", None
                            reasons.append("inherited_cumulative_baseline_unavailable")
                    else:
                        keys = set(native) | set(previous)
                        delta = {key: native[key] - previous[key] for key in keys
                                 if isinstance(native.get(key), int) and not isinstance(native.get(key), bool)
                                 and isinstance(previous.get(key), int) and not isinstance(previous.get(key), bool)}
                        if any(v < 0 for v in delta.values()):
                            quality, increment = "baseline", None
                            reasons.append("counter_reset_or_rewrite")
                        else:
                            increment, _, delta_reasons = native_tokens(delta, "codex")
                            reasons.extend(delta_reasons)
                            quality = "partial" if reasons else "exact"
                            gap = missing_before or any(previous_line < bad < loc.line for bad in cov.malformed_lines)
                            if (previous_config, previous_tier) != (config, tier) or config_changes > 1 or gap:
                                quality = "unattributed"
                                model = effort = observed_tier = None
                                reasons.append("counter_interval_configuration_or_gap_unknown")
                            if not any(delta.values()) and quality != "unattributed" and not gap:
                                reasons.append("repeated_cumulative_observation")
                                quality = "exact" if not any(r.startswith("missing_category") for r in reasons) else "partial"
                    last = as_dict(info.get("last_token_usage"))
                    prompt = last.get("input_tokens") if isinstance(last.get("input_tokens"), int) and not isinstance(last.get("input_tokens"), bool) else None
                if any(r.startswith("invalid_") or r.startswith("inconsistent_") for r in reasons):
                    quality, increment = "conflict", None
                if time is None:
                    reasons.append("observation_time_unavailable")
                result.observations.append(Observation(identity=oid, session=result.identity, harness="codex", execution=execution,
                    request_id=request, identity_quality="response_id" if request else "execution_ordinal" if ordinal is not None else "execution_line_fallback",
                    time=time, model=model, effort=effort,
                    configuration_reasons=(["model_unrecorded_or_unattributed"] if model is None else []) + (["effort_unrecorded_or_unattributed"] if effort is None else []),
                    counter_kind=counter_kind, native_usage=native, tokens=tokens, increment=increment, quality=quality,
                    reasons=list(dict.fromkeys(reasons)), semantics=CODEX_SEMANTICS, input_observation=prompt,
                    interval_start=interval_start, sources=[loc], service_tier=observed_tier,
                    lifecycle=["response_completed"] if kind == "token_usage_record" else []))
                if kind != "token_usage_record" and "context_or_inconsistent_total_not_consumption" not in reasons:
                    previous, previous_time, previous_config, previous_tier = native, time, config, tier
                    config_changes = 0
                    missing_before = False
                    previous_line = loc.line
            if inherited:
                continue
            if kind == "response_item":
                item_type = event_type
                item_id = str(payload.get("id") or payload.get("call_id") or f"{execution}:{ordinal if ordinal is not None else loc.line}")
                if item_type == "message":
                    if payload.get("role") == "assistant":
                        result.count("assistant_turns", item_id, time)
                    elif payload.get("role") == "user":
                        content = payload.get("content")
                        content = content if isinstance(content, list) else []
                        text = "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and isinstance(b.get("text"), str))
                        injected = not have_context or text.lstrip().startswith(NOISE_PREFIXES)
                        result.count("injected_user_messages" if injected else "human_turns", item_id, time)
                elif item_type == "agent_message":
                    result.count("injected_user_messages", item_id, time)
                elif item_type in {"function_call", "custom_tool_call", "local_shell_call"}:
                    result.count("tool_invocations", str(payload.get("call_id") or item_id), time)
                elif item_type in {"function_call_output", "custom_tool_call_output", "local_shell_call_output"} and (payload.get("is_error") is True or payload.get("status") == "failed"):
                    failure = classify_failure(str(payload.get("output", "")))
                    result.signals.append(Signal(identity=f"{result.identity}:tool_error:{item_id}", kind="tool_error", time=time, source=loc, category=failure.category.value))
            elif kind == "event_msg":
                signal = {"turn_aborted": "interruption", "task_complete": "turn_completed", "turn_complete": "turn_completed", "error": "execution_error", "stream_error": "stream_error", "thread_rolled_back": "explicit_revert"}.get(payload.get("type"))
                if signal:
                    result.signals.append(Signal(identity=f"codex:{execution}:{signal}:{ordinal if ordinal is not None else loc.line}", kind=signal, time=time, source=loc, details={"num_turns": payload.get("num_turns")} if signal == "explicit_revert" else {}))
            elif kind not in {"session_meta", "turn_context", "compacted", "token_usage_record"}:
                cov.unsupported_records += 1
    result.reasons.append("retained_observed_history_only_deleted_or_unrecorded_work_unavailable")
    if ref.metadata.get("invalid_role"):
        result.reasons.append("malformed_agent_role_metadata")
        for source in result.sources:
            source.malformed_records += 1
    if ref.metadata.get("discovery_conflict"):
        result.reasons.append("conflicting_session_metadata")
    return result
