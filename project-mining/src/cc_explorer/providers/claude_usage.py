"""Claude accounting discovery and wire interpretation, separate from browsing."""
from __future__ import annotations

from functools import lru_cache
from dataclasses import replace
from pathlib import Path


from ..conversion import validate_provenance
from ..parser import _STRUCTURAL_LINE_TYPES, create_transcript_entry
from ..models import UserOrigin, classify_failure
from ..usage_models import ChildLink, Observation, Signal, SourceCoverage, SessionUsage, UsageDiscovery
from ..usage_sources import CLAUDE_SEMANTICS, as_dict, native_tokens, snapshot, timestamp, walk_sources, iteration_reasons
from ..utils import PrefixId
from .base import Harness, ProviderSession, project_identity


def discover(selectors=None) -> UsageDiscovery:
    from .._claude_paths import _get_projects_dir
    from ..subagents import _read_agent_meta
    root = _get_projects_dir()
    paths, coverage = walk_sources(root, "claude")
    result = UsageDiscovery(roots=[coverage])
    by_directory: dict[Path, list[Path]] = {}
    for path in paths:
        if path.parent.parent == root:
            by_directory.setdefault(path.parent, []).append(path)
    projects = {directory: _cached_cwd(tuple(files), tuple(_version(p) for p in files)) for directory, files in by_directory.items()}
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
            cwd = _cached_cwd((path,), (_version(path),))
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
            role = meta.get("agentType") if isinstance(meta.get("agentType"), str) else None
            relation = "nested_file_dispatch_unverified"
        elif path.parent == encoded:
            sid = path.stem
        else:
            result.sources.append(SourceCoverage(path=str(path), root=str(root), status="excluded", project=project, owning_session=f"claude:{relative.parts[1]}" if len(relative.parts)>2 else None, reasons=["unsupported_source_layout"]))
            continue
        prior = grouped.get(sid)
        if prior is None:
            grouped[sid] = ProviderSession(PrefixId(sid), (path,), project, Harness.claude,
                worktree, parent, role, relation, (root,), {"cwd": cwd, "storage_parents": [parent] if parent else [], "invalid_role": relation == "nested_file_dispatch_unverified" and meta.get("agentType") is not None and role is None})
        else:
            parents = sorted(set(prior.metadata.get("storage_parents", [])) | ({parent} if parent else set()))
            prior.metadata["storage_parents"] = parents
            grouped[sid] = replace(prior, paths=prior.paths + (path,), parent_id=parents[0] if len(parents) == 1 else None,
                relationship="ambiguous_storage_parent" if len(parents) > 1 else prior.relationship)
            if (project, parent) != (prior.project_path, prior.parent_id):
                grouped[sid].metadata["discovery_conflict"] = True
    # A progress-only agent has no standalone filename. Explicit accounting
    # drilldown can locate its evidenced identity in parent progress records.
    wanted = []
    for selector in selectors or []:
        harness, sep, raw = selector.partition(":")
        if sep and harness != "claude":
            continue
        value = raw if sep else selector
        if not any(sid.startswith(value) for sid in grouped) and len(value) >= 6:
            wanted.append(value)
    if wanted:
        from ..corpus import make_scanner, ScannerError
        import re
        parent_paths = {path: parent for parent in grouped.values() if not parent.parent_id for path in parent.paths}
        try:
            hits = make_scanner().files_with_match([re.escape(value) for value in wanted], list(parent_paths))
        except ScannerError:
            hits = parent_paths
        for path in hits:
            parent = parent_paths[path]
            records, _ = snapshot(path, root)
            for record, _ in records:
                if record.get("type") != "progress":
                    continue
                data = as_dict(record.get("data"))
                nested = as_dict(data.get("message"))
                agent = data.get("agentId") or record.get("agentId")
                if nested.get("type") == "assistant" and isinstance(agent, str) and any(agent.startswith(value) for value in wanted) and agent not in grouped:
                    grouped[agent] = replace(parent, session_id=PrefixId(agent), parent_id=f"claude:{parent.session_id.full}",
                        relationship="nested_progress", role=None, metadata={"nested_only_agent_id": agent, "parent_session_id": parent.session_id.full})
    unsupported = [Path(s.path) for s in result.sources if "unsupported_source_layout" in s.reasons]
    if unsupported:
        from ..corpus import make_scanner, ScannerError
        try:
            possible = make_scanner().files_with_match([r'"type"\s*:\s*"assistant"'], unsupported)
        except ScannerError:
            possible = unsupported
        for source in result.sources:
            if Path(source.path) in possible:
                source.reasons.append("unsupported_layout_usage_possible")
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
    reasons.extend(iteration_reasons(raw))
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
        service_tier=raw.get("service_tier") if isinstance(raw.get("service_tier"), str) else None,
        speed=raw.get("speed") if isinstance(raw.get("speed"), str) else None,
        server_tool_use={k: v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None for k, v in as_dict(raw.get("server_tool_use")).items()}, sources=[loc], lifecycle=[f"request_stop:{stop}"] if stop else [])
    usage.observations.append(obs)
    return bool(raw)


def load(ref: ProviderSession, snapshots=None) -> SessionUsage:
    result = SessionUsage(ref)
    result.provenance = {"recorded_execution_host": None, "account": None, "unknown_reasons": {"recorded_execution_host": "unrecorded", "account": "unrecorded"}, "corpus_roots": [str(p) for p in ref.source_roots]}
    for path in ref.paths:
        root = next((r for r in ref.source_roots if path.is_relative_to(r)), path.parent)
        records, cov = snapshots.read(path, root) if snapshots else snapshot(path, root)
        cov.project = ref.project_path
        cov.owning_session = result.identity
        result.sources.append(cov)
        boundary = None
        marker = next((r for r, _ in records[:8] if r.get("type") == "x-converter-provenance"), None)
        if marker:
            provenance = validate_provenance(marker)
            if provenance is not None:
                candidate = provenance["lines_at_creation"]
                boundary = candidate
                result.provenance["conversion"] = provenance
                result.reasons.append("conversion_prefix_excluded")
            else:
                cov.malformed_records += 1
                result.reasons.append("invalid_conversion_marker")
        for record, loc in records:
            kind = record["type"]
            nested_only = ref.metadata.get("nested_only_agent_id")
            if nested_only:
                data = as_dict(record.get("data"))
                if kind != "progress" or (data.get("agentId") or record.get("agentId")) != nested_only:
                    continue
            time = timestamp(record.get("timestamp"))
            if record.get("forkedFrom") is not None:
                result.branches.append({"kind": "recorded_fork_lineage", "forked_from": record["forkedFrom"], "path": str(path), "line": loc.line})
            excluded = "conversion_copied_prefix" if boundary is not None and loc.line <= boundary else None
            if time is not None and excluded is None:
                result.times.append(time)
            if kind == "assistant":
                has_usage = _assistant(result, record, loc, excluded=excluded)
                if excluded is None:
                    if not has_usage:
                        cov.missing_usage_records += 1
                    result.count("assistant_turns", str(as_dict(record.get("message")).get("id") or record.get("uuid") or f"line:{loc.line}"), time)
            elif kind == "progress":
                data = as_dict(record.get("data"))
                nested = data.get("message") or {}
                if isinstance(nested, dict) and nested.get("type") == "assistant":
                    agent = data.get("agentId") or record.get("agentId")
                    if not excluded and isinstance(agent, str) and agent:
                        _child_link(result, record, loc, agent, "progress", nested)
                    _assistant(result, nested, loc, excluded=excluded, nested_id=(data.get("agentId") or record.get("agentId")) if isinstance(data.get("agentId") or record.get("agentId"), str) else None)
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
                    _child_link(result, record, loc, agent, "dispatch")
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
                subtype = subtype if isinstance(subtype, str) else None
                if subtype in {"api_error", "retry", "turn_duration"}:
                    signal = "model_error" if subtype == "api_error" else "retry" if subtype == "retry" else "turn_duration_observed"
                    duration = record.get("durationMs")
                    duration = duration if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0 and subtype == "turn_duration" else None
                    result.signals.append(Signal(identity=f"{result.identity}:{signal}:{record.get('uuid') or loc.line}", kind=signal, time=time, source=loc, duration_ms=duration))
    if ref.metadata.get("invalid_role"):
        result.reasons.append("malformed_agent_role_metadata")
        for source in result.sources:
            source.malformed_records += 1
    if ref.metadata.get("discovery_conflict"):
        result.reasons.append("conflicting_session_metadata")
    return result


@lru_cache(maxsize=2048)
def _cached_cwd(paths, versions):
    from ..corpus import _cwd_from_transcripts
    return _cwd_from_transcripts(list(paths)) or ""


def _version(path):
    try:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns
    except OSError:
        return None


def _child_link(result, record, loc, agent, kind, nested=None):
    candidates = [record.get("uuid"), as_dict(record.get("message")).get("id")]
    if nested:
        candidates.extend([nested.get("uuid"), as_dict(nested.get("message")).get("id")])
    event_id = next((value for value in candidates if isinstance(value, str) and value), None)
    parent_id = result.ref.metadata.get("parent_session_id") if result.ref.metadata.get("nested_only_agent_id") else None
    result.child_links.append(ChildLink(identity=f"claude:childlink:{event_id}" if event_id else None,
        parent=f"claude:{parent_id}" if parent_id else result.identity, child=f"claude:{agent}", kind=kind, source=loc))
