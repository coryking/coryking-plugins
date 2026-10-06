"""Claude accounting discovery and wire interpretation, separate from browsing."""
from __future__ import annotations

from functools import lru_cache
from dataclasses import replace
from pathlib import Path

import orjson

from ..parser import _STRUCTURAL_LINE_TYPES, create_transcript_entry
from ..models import UserOrigin, classify_failure
from ..usage_models import Observation, Signal, SourceCoverage, SessionUsage, UsageDiscovery
from ..usage_sources import CLAUDE_SEMANTICS, as_dict, native_tokens, snapshot, timestamp, walk_sources
from ..utils import PrefixId
from .base import Harness, ProviderSession, project_identity


def discover() -> UsageDiscovery:
    from .._claude_paths import _get_projects_dir
    from ..corpus import _cwd_from_transcripts
    from ..subagents import _read_agent_meta
    root = _get_projects_dir()
    paths, coverage = walk_sources(root, "claude")
    result = UsageDiscovery(roots=[coverage])
    projects: dict[Path, str] = {}
    for path in paths:
        if path.parent.parent == root:
            projects.setdefault(path.parent, "")
    for directory in projects:
        projects[directory] = _cwd_from_transcripts([p for p in paths if p.parent == directory]) or ""
    grouped: dict[str, ProviderSession] = {}
    identity = lru_cache(None)(project_identity)
    for path in paths:
        relative = path.relative_to(root)
        if not relative.parts:
            continue
        encoded = root / relative.parts[0]
        cwd = projects.get(encoded, "")
        # Standalone orphans may be the only retained source for a project.
        if not cwd:
            cwd = _cwd_from_transcripts([path]) or ""
        if not cwd:
            result.sources.append(SourceCoverage(path=str(path), root=str(root), status="excluded", reasons=["project_metadata_unavailable"]))
            continue
        project, worktree = identity(cwd)
        parent = None
        role = None
        relation = "independent"
        if path.name.startswith("agent-") and "subagents" in relative.parts:
            sid = path.stem[len("agent-"):]
            parent = f"claude:{relative.parts[1]}"
            meta = _read_agent_meta(path)
            role = meta.get("agentType") or None
            relation = "nested_file_dispatch_unverified"
        elif path.parent == encoded:
            sid = path.stem
        else:
            result.sources.append(SourceCoverage(path=str(path), root=str(root), status="excluded", reasons=["unsupported_source_layout"]))
            continue
        prior = grouped.get(sid)
        if prior is None:
            grouped[sid] = ProviderSession(PrefixId(sid), (path,), project, Harness.claude,
                worktree, parent, role, relation, (root,), {"cwd": cwd})
        else:
            from dataclasses import replace
            grouped[sid] = replace(prior, paths=prior.paths + (path,))
            if (project, parent) != (prior.project_path, prior.parent_id):
                grouped[sid].metadata["discovery_conflict"] = True
    result.sessions = list(grouped.values())
    return result


def _assistant(usage: SessionUsage, record: dict, loc, *, excluded: str | None = None, nested_id: str | None = None):
    message = record.get("message") or {}
    if not isinstance(message, dict):
        return False
    mid = message.get("id")
    fallback = record.get("uuid")
    request = mid if isinstance(mid, str) and mid else None
    identity = f"claude:request:{request}" if request else f"{usage.identity}:event:{fallback or loc.line}"
    session = f"claude:{nested_id}" if nested_id else usage.identity
    raw = message.get("usage")
    raw = raw if isinstance(raw, dict) else {}
    tokens, prompt, reasons = native_tokens(raw, "claude")
    if not raw:
        reasons.append("missing_usage")
    if not request:
        reasons.append("request_identity_unavailable")
    model = message.get("model")
    model = model if isinstance(model, str) and model else None
    if model == "<synthetic>":
        model = None
        request = None
        reasons.append("synthetic_assistant_not_request_evidence")
    effort = record.get("effort") or record.get("perTurnEffort")
    effort = effort if isinstance(effort, str) else None
    stop = message.get("stop_reason")
    quality = "partial" if reasons else "exact"
    if any(r.startswith("invalid_") or r.startswith("inconsistent_") for r in reasons):
        quality = "conflict"
    if excluded:
        quality = "excluded"
        reasons.append(excluded)
    time = timestamp(record.get("timestamp"))
    if time is None:
        reasons.append("observation_time_unavailable")
    obs = Observation(identity=identity, session=session, harness="claude", execution=usage.ref.session_id.full,
        request_id=request, identity_quality="request_id" if request else "record_uuid" if fallback else "logical_session_line_fallback",
        time=time, model=model, effort=effort,
        configuration_reasons=(["model_unrecorded"] if model is None else []) + (["effort_unrecorded"] if effort is None else []),
        counter_kind="request", native_usage=raw, tokens=tokens, increment=tokens if quality in {"exact", "partial"} else None,
        quality=quality, reasons=reasons, semantics=CLAUDE_SEMANTICS, input_observation=prompt,
        service_tier=raw.get("service_tier") if isinstance(raw.get("service_tier"), str) else None, sources=[loc], lifecycle=[f"request_stop:{stop}"] if stop else [])
    usage.observations.append(obs)
    return bool(raw)


def load(ref: ProviderSession, snapshots=None) -> SessionUsage:
    result = SessionUsage(ref)
    result.provenance = {"recorded_execution_host": None, "account": None, "unknown_reasons": {"recorded_execution_host": "unrecorded", "account": "unrecorded"}, "corpus_roots": [str(p) for p in ref.source_roots]}
    for path in ref.paths:
        root = next((r for r in ref.source_roots if path.is_relative_to(r)), path.parent)
        records, cov = snapshots.read(path, root) if snapshots else snapshot(path, root)
        result.sources.append(cov)
        boundary = None
        marker = next((r for r, _ in records[:8] if r.get("type") == "x-converter-provenance"), None)
        if marker:
            provenance = marker.get("x_converter") or {}
            candidate = provenance.get("lines_at_creation")
            if isinstance(candidate, int) and not isinstance(candidate, bool) and candidate > 0:
                boundary = candidate
                result.provenance["conversion"] = provenance
                result.reasons.append("conversion_prefix_excluded")
            else:
                result.reasons.append("conversion_boundary_unavailable")
        for record, loc in records:
            kind = record["type"]
            time = timestamp(record.get("timestamp"))
            excluded = "conversion_copied_prefix" if boundary is not None and loc.line <= boundary else "conversion_boundary_unavailable" if marker and boundary is None else None
            if time is not None and excluded is None:
                result.times.append(time)
            if kind == "assistant":
                has_usage = _assistant(result, record, loc, excluded=excluded)
                if excluded is None:
                    if not has_usage:
                        cov.missing_usage_records += 1
                    result.count("assistant_turns", str((record.get("message") or {}).get("id") or record.get("uuid") or f"line:{loc.line}"), time)
            elif kind == "progress":
                data = as_dict(record.get("data"))
                nested = data.get("message") or {}
                if isinstance(nested, dict) and nested.get("type") == "assistant":
                    _assistant(result, nested, loc, excluded=excluded, nested_id=data.get("agentId") or record.get("agentId"))
            elif kind not in _STRUCTURAL_LINE_TYPES and kind not in {"user", "summary", "system", "queue-operation", "file-history-snapshot"}:
                cov.unsupported_records += 1
            if excluded:
                cov.excluded_records += 1
                continue
            for key in ("hostname", "execution_host"):
                if isinstance(record.get(key), str):
                    result.provenance["recorded_execution_host"] = record[key]
                    result.provenance["unknown_reasons"].pop("recorded_execution_host", None)
            if kind in {"assistant", "user"}:
                role = record.get("agentName") or record.get("agentType")
                if isinstance(role, str) and not result.ref.role:
                    result.ref = replace(result.ref, role=role)
                dispatch = as_dict(record.get("toolUseResult"))
                agent = dispatch.get("agentId")
                if isinstance(agent, str) and agent:
                    result.related_sessions.append(replace(ref, session_id=PrefixId(agent), paths=(),
                        parent_id=result.identity, relationship="dispatch_only", role=None))
                try:
                    entry = create_transcript_entry(record)
                except Exception:
                    cov.malformed_records += 1
                    continue
                if kind == "user":
                    origin = entry.origin
                    count_kind = "human_turns" if origin is UserOrigin.human else "injected_user_messages" if origin is not UserOrigin.tool_result else "tool_results"
                    result.count(count_kind, str(record.get("uuid") or f"line:{loc.line}"), time)
                    if origin is UserOrigin.interrupt:
                        result.signals.append(Signal(identity=f"{result.identity}:interrupt:{record.get('uuid') or loc.line}", kind="interruption", time=time, source=loc))
                for block in entry.message.content if isinstance(entry.message.content, list) else []:
                    if getattr(block, "type", None) == "tool_use":
                        result.count("tool_invocations", block.id.full, time)
                    elif getattr(block, "type", None) == "tool_result" and block.is_error:
                        failure = classify_failure(str(block.content))
                        result.signals.append(Signal(identity=f"{result.identity}:tool_error:{block.tool_use_id.full}", kind="tool_error", time=time, source=loc, category=failure.category.value))
            if kind == "system":
                subtype = record.get("subtype")
                if subtype in {"api_error", "retry", "turn_duration"}:
                    signal = "model_error" if subtype == "api_error" else "retry" if subtype == "retry" else "turn_duration_observed"
                    duration = record.get("durationMs")
                    duration = duration if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0 and subtype == "turn_duration" else None
                    result.signals.append(Signal(identity=f"{result.identity}:{signal}:{record.get('uuid') or loc.line}", kind=signal, time=time, source=loc, duration_ms=duration))
    if ref.metadata.get("discovery_conflict"):
        result.reasons.append("conflicting_session_metadata")
    return result
