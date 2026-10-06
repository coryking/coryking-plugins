"""Workload selection, shared accounting arithmetic and bounded MCP reports."""
from __future__ import annotations

from dataclasses import replace
from collections import Counter, defaultdict
from datetime import datetime
import socket
from typing import Any

from .corpus import MIN_ID_LEN, resolve_project
from .providers import providers_for
from .providers.base import ProviderSession, project_identity
from .usage_models import (
    Attribution, Bounds, CategoryTotal, Observation, Rollup, SessionSummary,
    SessionUsage, Tokens, Totals, UsageObservations, UsageReport,
)
from .usage_sources import SnapshotReader, reconcile_requests, timestamp, merge_source_coverage
from .usage_index import holders, child_link_holders
from .utils import PrefixId

CATEGORY_SEMANTICS = {
    "disjoint_token_categories": ["uncached_input", "cache_read_input", "cache_creation_input", "output"],
    "included_breakdowns": {"cache_creation_input": ["cache_creation_5m", "cache_creation_1h"], "output": ["reasoning_output"]},
    "unknown": "null is unrecorded or underivable; subtotals sum measured increments and retain unknown denominators",
    "native_input": {"claude": "input_tokens excludes cache reads and writes", "codex": "input_tokens includes cache reads and writes"},
    "server_tool_use": "native nonnegative request counts; separate from token categories",
}
SESSION_GAPS = {"dispatched_child_source_unavailable", "conflicting_session_metadata", "history_base_source_unavailable",
    "inherited_history_boundary_unrecorded", "inherited_ordinal_unavailable", "conflicting_history_baseline_copies",
    "invalid_history_cutoff", "invalid_conversion_marker", "malformed_agent_role_metadata",
    "copied_child_link_outside_selected_workload", "child_link_identity_unavailable", "child_parent_conflicts_with_storage"}
EXCLUSION_GAPS = {"window_time_unavailable", "counter_interval_crosses_window_start", "shared_request_outside_selected_workload",
    "inherited_request_boundary_or_owner_unavailable", "context_estimate_not_consumption"}
COUNT_KINDS = ("assistant_turns", "human_turns", "injected_user_messages", "tool_invocations", "tool_results")


def totals(observations: list[Observation], unidentified_attempts: bool = False) -> Totals:
    eligible = [o for o in observations if o.quality not in {"excluded", "baseline", "conflict"}]
    categories = {}
    for name in Tokens.model_fields:
        values = [getattr(o.increment, name) if o.increment else None for o in eligible]
        categories[name] = CategoryTotal(observed_subtotal=sum(v for v in values if v is not None),
            observations_measured=sum(v is not None for v in values), observations_unknown=sum(v is None for v in values))
    requests = {o.identity for o in observations if o.counter_kind == "request" and o.request_id and o.quality != "excluded"}
    request_unknown = unidentified_attempts or any(o.counter_kind == "cumulative" and o.quality != "excluded" or o.counter_kind == "request" and not o.request_id and o.quality != "excluded" for o in observations)
    tools = {key: CategoryTotal(observed_subtotal=sum(o.server_tool_use.get(key) or 0 for o in eligible),
        observations_measured=sum(o.server_tool_use.get(key) is not None for o in eligible),
        observations_unknown=sum(o.server_tool_use.get(key) is None for o in eligible))
        for key in sorted({key for o in eligible for key in o.server_tool_use})}
    return Totals(categories=categories, server_tool_use=tools, accountable_observations=len(eligible),
        excluded_observations=sum(o.quality in {"excluded", "baseline", "conflict"} for o in observations),
        uncertain_observations=sum(o.quality in {"partial", "unattributed", "conflict", "baseline"} for o in observations),
        model_requests=None if request_unknown else len(requests), model_requests_lower_bound=len(requests),
        request_count_reason="Cumulative samples, unmeasured failure attempts, or missing request IDs cannot establish model-request count" if request_unknown else None)


def _window(start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
    first, latest = timestamp(start), timestamp(end)
    if start is not None and first is None or end is not None and latest is None:
        raise ValueError("start/end must be ISO-8601 timestamps with timezone")
    if first is not None and latest is not None and first >= latest:
        raise ValueError("start must be before end; window is half-open [start,end)")
    return first, latest


def _in_window(time, start, end):
    return time is not None and (start is None or time >= start) and (end is None or time < end)


def _resolve(value: str, refs: dict[str, ProviderSession]) -> str:
    if value in refs:
        return value
    harness, colon, raw = value.partition(":")
    prefix = raw if colon else value
    if len(prefix) < MIN_ID_LEN:
        raise ValueError(f"Session prefix must be at least {MIN_ID_LEN} characters: {value}")
    matches = [key for key, ref in refs.items() if ref.session_id.full.startswith(prefix) and (not colon or ref.harness.value == harness)]
    if len(matches) != 1:
        raise ValueError(f"{'Ambiguous' if matches else 'Unresolved'} usage session {value!r}; matches: {matches}")
    return matches[0]


def _physical_parent(ref):
    if ref and ref.paths and not ref.metadata.get("nested_only_agent_id"):
        return ref.parent_id
    return None


class Accounting:
    """One call's provider discovery, bounded snapshots and normalized evidence."""
    def __init__(self, sessions=None, projects=None, harnesses=None, include_descendants=True,
                 start=None, end=None, attribution=None):
        self.start, self.end = _window(start, end)
        self.providers = {p.harness.value: p for p in providers_for(harnesses)}
        self.roots = []
        self.discovery_sources = []
        self.refs: dict[str, ProviderSession] = {}
        for provider in self.providers.values():
            discovery = provider.discover_usage(sessions)
            self.roots.extend(discovery.roots)
            self.discovery_sources.extend(discovery.sources)
            for ref in discovery.sessions:
                self.refs[f"{ref.harness.value}:{ref.session_id.full}"] = ref
        self.discovered_sessions = len(self.refs)
        self.discovered_keys = set(self.refs)
        project_scope = list(dict.fromkeys(project_identity(resolve_project(p))[0] for p in projects or []))
        resolvable = {k: r for k, r in self.refs.items() if not project_scope or r.project_path in project_scope}
        selected = {_resolve(s, resolvable): "explicit" for s in sessions or []}
        if not sessions:
            selected.update({key: "project_scope" if project_scope else "corpus_scope" for key in resolvable})
        if include_descendants:
            while True:
                additions = {key: f"descendant_of:{ref.parent_id}" for key, ref in self.refs.items() if key not in selected and ref.parent_id in selected}
                if not additions:
                    break
                selected.update(additions)
        self.membership = selected
        self.loaded: dict[str, SessionUsage] = {}
        snapshots = SnapshotReader()
        raw: list[Observation] = []
        dispatches = defaultdict(set)
        self.holder_reasons = []
        pending = list(sorted(selected))
        while pending:
            key = pending.pop()
            if key in self.loaded:
                continue
            usage = self.providers[self.refs[key].harness.value].load_usage(self.refs[key], snapshots)
            snapshots.release_records()
            self.loaded[key] = usage
            self.refs[key] = usage.ref
            allowed, blocked = {}, set()
            if include_descendants:
                uncertain_links = [link for link in usage.child_links
                    if _physical_parent(self.refs.get(link.child)) is None
                    and not (self.refs.get(link.child) and self.refs[link.child].metadata.get("storage_parents"))]
                copies, reasons = child_link_holders({link.identity for link in uncertain_links},
                    [r for r in self.refs.values() if r.harness.value == "claude"], self.providers["claude"]) if uncertain_links else ([], [])
                self.holder_reasons.extend(reasons)
                parent_holders = defaultdict(set)
                for link in copies + uncertain_links:
                    parent_holders[link.identity, link.child].add(link.parent)
                for link in usage.child_links:
                    child_ref = self.refs.get(link.child)
                    parent = _physical_parent(child_ref)
                    storage_parents = set(child_ref.metadata.get("storage_parents", [])) if child_ref else set()
                    parents = storage_parents or ({parent} if parent else parent_holders[link.identity, link.child])
                    reason = None
                    if parent and parent != key:
                        reason = "inherited_child_link" if parent in self.membership else "child_parent_conflicts_with_storage"
                    elif not storage_parents and parent is None and link.identity is None:
                        reason = "child_link_identity_unavailable"
                    elif not parents <= self.membership.keys():
                        reason = "copied_child_link_outside_selected_workload"
                    if reason:
                        blocked.add(link.child)
                        usage.reasons.append(reason)
                        usage.branches.append({"kind": "excluded_child_link", "child": link.child,
                            "observed_parent": link.parent, "candidate_parents": sorted(parents),
                            "identity": link.identity, "source": link.source.model_dump(), "reason": reason})
                    else:
                        allowed.setdefault(link.child, set()).update(parents)
                for child in blocked:
                    allowed.pop(child, None)
            # Relationship ownership is resolved before either dispatch or
            # progress can expand the workload. Explicit child selections remain
            # eligible, even if an unrelated copied link is rejected.
            for observation in usage.observations:
                child = observation.session
                if child != key and child not in self.membership:
                    if include_descendants and child and child in allowed:
                        self.refs.setdefault(child, replace(usage.ref, session_id=PrefixId(child.split(":", 1)[1]), paths=(),
                            parent_id=next(iter(allowed[child])) if len(allowed[child]) == 1 else None,
                            relationship="nested_progress", role=None, metadata={}))
                        self.membership[child] = f"nested_progress_of:{key}"
                        if self.refs[child].paths:
                            pending.append(child)
                        else:
                            self.loaded[child] = SessionUsage(self.refs[child])
                    else:
                        observation.quality, observation.increment = "excluded", None
                        observation.reasons.append("copied_child_link_outside_selected_workload" if include_descendants else "descendants_disabled")
                raw.append(observation)
            for child, parents in allowed.items():
                dispatches[child].update(parents)
                if child not in self.membership:
                    parent = next(iter(parents)) if len(parents) == 1 else None
                    self.membership[child] = f"dispatch_of:{key}"
                    self.refs.setdefault(child, replace(usage.ref, session_id=PrefixId(child.split(":", 1)[1]), paths=(),
                        parent_id=parent, relationship="dispatch_only", role=None, metadata={}))
                    if self.refs[child].paths:
                        pending.append(child)
                    else:
                        self.loaded[child] = SessionUsage(self.refs[child])
        observed_owners = {o.session for o in raw}
        for child, parents in dispatches.items():
            usage = self.loaded[child]
            present = bool(usage.ref.paths or child in observed_owners)
            parent = _physical_parent(usage.ref) or (next(iter(parents)) if len(parents) == 1 else None)
            usage.ref = replace(usage.ref, parent_id=parent,
                relationship="ambiguous_dispatch_parent" if parent is None else "dispatched" if present else "dispatch_only")
            self.refs[child] = usage.ref
            if parent is None:
                usage.reasons.append("child_parent_ambiguous")
                usage.branches.append({"kind": "ambiguous_child_parent", "child": child,
                    "candidate_parents": sorted(parents)})
            if not present:
                usage.reasons.append("dispatched_child_source_unavailable")
        requests = [o for o in raw if o.counter_kind == "request"]
        if "claude" in self.providers:
            copies, reasons = holders({o.identity for o in requests if o.quality != "excluded"},
                [r for r in self.refs.values() if r.harness.value == "claude"], self.providers["claude"])
            self.holder_reasons.extend(reasons)
            # Cached evidence can repeat selected locators. Collapse those before
            # stream reconciliation; ownership candidates use only active records.
            requests = list({(o.identity, o.sources[0].path, o.sources[0].line): o for o in copies + requests}.values())
        owners = defaultdict(set)
        for o in requests:
            if o.quality != "excluded" and o.session:
                owners[o.identity].add(o.session)
        normalized_requests = reconcile_requests(requests)
        for o in normalized_requests:
            candidates = sorted(owners[o.identity])
            o.candidate_sessions = candidates
            if len(candidates) > 1:
                o.session = None
                o.execution = None
                if set(candidates) <= self.membership.keys():
                    o.reasons.append("ambiguous_copied_request_owner")
                else:
                    o.quality, o.increment = "excluded", None
                    o.reasons.append("shared_request_outside_selected_workload")
        counters = [o for o in raw if o.counter_kind != "request"]
        by_counter: dict[str, Observation] = {}
        for obs in counters:
            prior = by_counter.get(obs.identity)
            if prior is None:
                by_counter[obs.identity] = obs
            else:
                if (prior.native_usage, prior.increment, prior.model, prior.effort, prior.service_tier) != (obs.native_usage, obs.increment, obs.model, obs.effort, obs.service_tier):
                    prior.quality, prior.increment = "conflict", None
                    prior.reasons.append("conflicting_copies")
                prior.sources.extend(obs.sources)
        self.all_observations = sorted(normalized_requests + list(by_counter.values()), key=lambda o: (o.time is None, o.time.isoformat() if o.time else "", o.execution or "", min(s.line for s in o.sources), o.identity))
        self.all_by_session = defaultdict(list)
        for obs in self.all_observations:
            for key in obs.candidate_sessions or [obs.session]:
                self.all_by_session[key].append(obs)
        self.observations = []
        for original in self.all_observations:
            obs = original.model_copy(deep=True)
            if start is not None or end is not None:
                if obs.time is None:
                    obs.quality, obs.increment = "excluded", None
                    obs.reasons.append("window_time_unavailable")
                elif not _in_window(obs.time, self.start, self.end):
                    continue
                elif obs.counter_kind == "cumulative" and self.start and obs.quality not in {"excluded", "baseline", "conflict"} and (obs.interval_start is None or obs.interval_start < self.start):
                    obs.quality, obs.increment = "excluded", None
                    obs.reasons.append("counter_interval_crosses_window_start")
            self.observations.append(obs)
        self.by_session = defaultdict(list)
        self.owners_in_window = set()
        for obs in self.observations:
            self.by_session[obs.session].append(obs)
            self.owners_in_window.update(obs.candidate_sessions or [obs.session])
        self.failure_sessions = {key for key, usage in self.loaded.items() if any(s.kind in {"model_error", "stream_error"}
            and (self.start is None and self.end is None or _in_window(s.time, self.start, self.end)) for s in usage.signals)}
        self.artifacts: list[dict[str, Any]] = []
        assigned: set[str] = set()
        assigned_targets: set[str] = set()
        for value in attribution or []:
            item = value if isinstance(value, Attribution) else Attribution.model_validate(value)
            if item.session:
                if item.session not in self.membership:
                    raise ValueError("Attribution session must be an included full harness-qualified identity")
                matches = [o for o in self.observations if o.session == item.session]
                target = item.session
            else:
                matches = [o for o in self.observations if o.identity == item.observation]
                if len(matches) != 1:
                    raise ValueError("Attribution observation must resolve to one included full identity")
                target = item.observation
            if target in assigned_targets:
                raise ValueError(f"Overlapping attribution for {target}")
            assigned_targets.add(target)
            for obs in matches:
                if obs.identity in assigned:
                    raise ValueError(f"Overlapping attribution for {obs.identity}")
                assigned.add(obs.identity)
                obs.labels = item.labels
            self.artifacts.extend({"target": target, **artifact.model_dump()} for artifact in item.artifacts)
        self.scope = {"requested_sessions": sessions or [], "projects": project_scope,
            "harnesses": sorted(self.providers), "start": start, "end": end,
            "include_descendants": include_descendants,
            "member_count": len(self.membership), "membership_reasons": dict(Counter(reason.split(":", 1)[0] for reason in self.membership.values())),
            "accounting_basis": "bounded_retained_execution_evidence", "snapshot_consistency": "per_source_observed_boundaries_not_atomic_corpus"}
        self.scope["storage_host"] = socket.gethostname()
        self.project_scope = project_scope
        self.corpus_scope = not sessions and not projects
        self.selected_roots = {str(r) for key in self.membership for r in self.refs[key].source_roots}
        self.selected_harnesses = {self.refs[k].harness.value for k in self.membership}
        self.selected_projects = {self.refs[k].project_path for k in self.membership}
        self.selected_paths = {path for key in self.membership for path in self.refs[key].paths}
        self.selected_directories = {str(root / path.relative_to(root).parts[0]) for key in self.membership
            for root in self.refs[key].source_roots for path in self.refs[key].paths if path.is_relative_to(root)}
        self._sources = merge_source_coverage([s for s in self.discovery_sources if self._source_in_scope(s)] +
            [s for usage in self.loaded.values() for s in usage.sources])

    def _source_in_scope(self, source):
        if "unsupported_source_layout" in source.reasons and "unsupported_layout_usage_possible" not in source.reasons:
            return False  # known non-transcript files such as workflow journals
        if self.corpus_scope or source.owning_session in self.membership:
            return True
        if self.project_scope and (source.project in self.selected_projects or any(source.path.startswith(d + "/") for d in self.selected_directories)):
            return True
        from pathlib import Path
        return any(Path(source.path).is_relative_to(p.with_suffix("")) for p in self.selected_paths)

    def sources(self):
        return self._sources

    def unmeasured_attempts(self, sessions=None):
        return bool(self.failure_sessions if sessions is None else self.failure_sessions.intersection(sessions))

    def coverage(self):
        sources = self.sources()
        obs = self.observations
        missing = {key for o in obs if o.increment is not None and any(getattr(o.increment, k) is None for k in ("uncached_input", "cache_read_input", "cache_creation_input", "output")) for key in o.candidate_sessions or ([o.session] if o.session else [])}
        missing.update(k for k, usage in self.loaded.items() if k not in self.owners_in_window
            and (self.start is None and self.end is None or not self.all_by_session[k] or
                any(_in_window(t, self.start, self.end) for t in usage.times)))
        conflicting = {key for o in obs if o.quality == "conflict" for key in o.candidate_sessions or ([o.session] if o.session else [])}
        uncertain = any(o.quality in {"partial", "unattributed", "baseline", "conflict"} or bool(EXCLUSION_GAPS.intersection(o.reasons)) for o in obs)
        root_uncertain = any(r.status != "observed" and (self.corpus_scope or r.harness in self.selected_harnesses) and not (r.status == "missing" and r.root.endswith("/archived_sessions")) for r in self.roots)
        source_uncertain = any(s.status not in {"observed"} or s.malformed_records for s in sources)
        session_uncertain = any(SESSION_GAPS.intersection(s.reasons) for s in self.loaded.values())
        discovered_sources = sum(r.discovered_sources or 0 for r in self.roots)
        included_paths = {s.path for usage in self.loaded.values() for s in usage.sources}
        return {"complete_observed_usage": not (uncertain or root_uncertain or source_uncertain or session_uncertain or missing or self.unmeasured_attempts() or {"request_holder_source_unreadable", "request_holder_snapshot_partial"}.intersection(self.holder_reasons)),
            "ownership_complete": not any(o.session is None for o in obs),
            "selection_attribution_uncertain_observations": sum("shared_request_outside_selected_workload" in o.reasons for o in obs),
            "no_in_window_activity_sessions": sum(k not in self.owners_in_window and bool(self.all_by_session[k]) and not any(_in_window(t, self.start, self.end) for t in u.times) for k, u in self.loaded.items()) if self.start is not None or self.end is not None else 0,
            "unmeasured_failure_attempts_present": self.unmeasured_attempts(),
            "lifetime_completeness": "unavailable_deleted_or_unrecorded_work_cannot_be_reconstructed",
            "discovered_sessions": self.discovered_sessions, "included_sessions": len(self.membership),
            "excluded_sessions": len(self.discovered_keys - self.membership.keys()),
            "malformed_sessions": len({key for key, usage in self.loaded.items() if any(s.malformed_records for s in usage.sources)}),
            "unreadable_sessions": len({key for key, usage in self.loaded.items() if any(s.status == "unreadable" for s in usage.sources)}),
            "missing_usage_sessions": len(missing), "conflicting_sessions": len(conflicting),
            "discovered_sources": discovered_sources,
            "included_sources": len(included_paths),
            "excluded_sources": discovered_sources - len(included_paths),
            "excluded_source_reasons": {"outside_selected_workload": max(0, discovered_sources - len(included_paths) - len(self.discovery_sources)), "discovery_identity_or_layout_unavailable": len(self.discovery_sources)},
            "malformed_sources": sum(bool(s.malformed_records) or any("malformed" in r for r in s.reasons) for s in sources),
            "unreadable_sources": sum(s.status == "unreadable" for s in sources),
            "partial_sources": sum(s.status == "partial" for s in sources),
            "conflicting_sources": len({loc.path for o in obs if o.quality == "conflict" for loc in o.sources}),
            "missing_usage_sources": sum(bool(s.missing_usage_records) for s in sources),
            "records_inspected": sum(s.records for s in sources),
            "malformed_records": sum(s.malformed_records for s in sources),
            "unsupported_records": sum(s.unsupported_records for s in sources),
            "excluded_records": sum(s.excluded_records for s in sources),
            "missing_usage_records": sum(s.missing_usage_records for s in sources),
            "observations": len(obs), "roots": [r.model_dump() for r in self.roots],
            "configuration_unknown_observations": sum(o.model is None or o.effort is None for o in obs if o.quality != "excluded"),
            "time_unknown_observations": sum(o.time is None for o in obs),
            "denominators": {"session_counts": "discovered logical sessions; included also contains nested-only or dispatch-only agents", "source_counts": "enumerated files; included includes unreadable selected files and baseline dependencies; inspected diagnostics include discovery failures", "missing_usage_records": "raw assistant or counter records without usage; recovered streaming fragments remain counted here but normalized usage coverage determines completeness", "observations": "normalized deduplicated observations in the selected window"}}

    def warnings(self):
        reasons = {r for o in self.observations for r in o.reasons}
        reasons.update(r for s in self.sources() for r in s.reasons)
        reasons.update(r for root in self.roots if self.corpus_scope or root.root in self.selected_roots for r in root.reasons)
        reasons.update(self.holder_reasons)
        reasons.update(r for s in self.loaded.values() for r in s.reasons)
        reasons.discard("cumulative_superseded_by_response_records")
        reasons.discard("repeated_cumulative_observation")
        if self.unmeasured_attempts():
            reasons.add("failure_attempt_usage_or_request_identity_unavailable")
        return sorted(reasons | {"Input observations describe recorded request input, not exact current context occupancy", "Completion of a response or turn does not establish workflow success or final completion"})

    def summaries(self):
        result = []
        for key in sorted(self.membership):
            usage = self.loaded[key]
            observations = self.by_session[key]
            times = [t for t in usage.times if self.start is None and self.end is None or _in_window(t, self.start, self.end)]
            times.extend(o.time for o in observations if o.time is not None and o.quality != "excluded")
            inputs = [o.input_observation for o in observations if o.input_observation is not None and o.quality != "excluded"]
            first = min(times) if times else None
            latest = max(times) if times else None
            signals = {s.identity: s for s in usage.signals if self.start is None and self.end is None or _in_window(s.time, self.start, self.end)}
            counts = {kind: len(usage.counts.get(kind, set())) if self.start is None and self.end is None else sum(_in_window(time, self.start, self.end) for time in usage.count_times.get(kind, {}).values()) for kind in COUNT_KINDS}
            # Conversation counts cover the full retained session; expose that
            # denominator rather than pretending they are time-window counts.
            result.append(SessionSummary(identity=key, harness=usage.ref.harness.value,
                project=usage.ref.project_path, worktree=usage.ref.worktree, parent=usage.ref.parent_id,
                relationship=usage.ref.relationship, role=usage.ref.role,
                inclusion_reason=self.membership[key], totals=totals(observations, self.unmeasured_attempts({key})), counts=counts,
                first=first, latest=latest, first_input=inputs[0] if inputs else None,
                latest_input=inputs[-1] if inputs else None, peak_input=max(inputs) if inputs else None,
                input_semantics="first/latest/peak recorded request input; not first prompt or exact context occupancy",
                recorded_active_work_ms=sum(s.duration_ms for s in signals.values() if s.duration_ms is not None) if any(s.duration_ms is not None for s in signals.values()) else None,
                completion_state="workflow_completion_unavailable", lifecycle_counts={kind: sum(s.kind == kind for s in signals.values()) for kind in sorted({s.kind for s in signals.values()})},
                branches=usage.branches[:20], provenance={**usage.provenance, "branch_count": len(usage.branches)}, reasons=sorted(set(usage.reasons))))
        return result


def _bounds(sessions):
    intervals = sorted((s.first, s.latest) for s in sessions if s.first is not None and s.latest is not None)
    if not intervals:
        return Bounds(first=None, latest=None, elapsed_span_ms=None, sum_session_spans_ms=0, overlap_ms=0)
    first = intervals[0][0]
    latest = max(end for _, end in intervals)
    summed = sum(int((b-a).total_seconds()*1000) for a,b in intervals)
    union = 0
    left, right = intervals[0]
    for a,b in intervals[1:]:
        if a <= right:
            right = max(right,b)
        else:
            union += int((right-left).total_seconds()*1000)
            left,right = a,b
    union += int((right-left).total_seconds()*1000)
    recorded = [s.recorded_active_work_ms for s in sessions if s.recorded_active_work_ms is not None]
    return Bounds(first=first, latest=latest, elapsed_span_ms=int((latest-first).total_seconds()*1000), sum_session_spans_ms=summed, overlap_ms=summed-union, recorded_active_session_work_ms=sum(recorded) if recorded else None)


def _pagination(offset, limit):
    if isinstance(offset, bool) or isinstance(limit, bool) or offset < 0 or not 1 <= limit <= 500:
        raise ValueError("offset must be >=0 and limit must be 1..500")


def get_report(*, sessions=None, projects=None, harnesses=None, include_descendants=True,
               start=None, end=None, attribution=None, offset=0, limit=20, rollup_offsets=None, rollup_limit=20) -> UsageReport:
    _pagination(offset, limit)
    _pagination(0, rollup_limit)
    accounting = Accounting(sessions, projects, harnesses, include_descendants, start, end, attribution)
    rows = accounting.summaries()
    rollups, counts, next_offsets = {}, {}, {}
    dimensions = ("model_effort", "configuration", "project", "role", "caller_labels")
    if set(rollup_offsets or {}) - set(dimensions):
        raise ValueError("Unknown rollup dimension")
    for dimension in dimensions:
        groups = {}
        for obs in accounting.observations:
            ref = accounting.refs.get(obs.session)
            if dimension == "model_effort":
                key = {"model": obs.model, "effort": obs.effort}
            elif dimension == "configuration":
                key = {"harness": obs.harness, "model": obs.model, "effort": obs.effort, "service_tier": obs.service_tier, "speed": obs.speed}
            elif dimension == "project":
                key = {"project": ref.project_path if ref else None}
            elif dimension == "role":
                key = {"role": ref.role if ref else None}
            else:
                key = {label: obs.labels.get(label) for label in ("run", "role", "pass", "round", "phase")}
            groups.setdefault(tuple(sorted(key.items())), (key, []))[1].append(obs)
        entries = sorted(groups.values(), key=lambda pair: str(pair[0]))
        page = (rollup_offsets or {}).get(dimension, 0)
        _pagination(page, rollup_limit)
        counts[dimension] = len(entries)
        next_offsets[dimension] = page+rollup_limit if page+rollup_limit<len(entries) else None
        rollups[dimension] = [Rollup(key=k, totals=totals(v, accounting.unmeasured_attempts({o.session for o in v}))) for k,v in entries[page:page+rollup_limit]]
    return UsageReport(scope=accounting.scope, totals=totals(accounting.observations, accounting.unmeasured_attempts()), coverage=accounting.coverage(),
        warnings=accounting.warnings(), bounds=_bounds(rows), rollups=rollups, rollup_counts=counts, rollup_next_offsets=next_offsets,
        category_semantics=CATEGORY_SEMANTICS, session_count=len(rows), offset=offset, next_offset=offset+limit if offset+limit<len(rows) else None,
        sessions=rows[offset:offset+limit], artifacts=accounting.artifacts)


def get_observations(*, session, projects=None, harnesses=None, start=None, end=None, offset=0, limit=100,
                     source_offset=0, source_limit=50, lifecycle_offset=0, lifecycle_limit=100, branch_offset=0, branch_limit=20) -> UsageObservations:
    for page, size in ((offset,limit), (source_offset,source_limit), (lifecycle_offset,lifecycle_limit), (branch_offset,branch_limit)):
        _pagination(page, size)
    accounting = Accounting([session], projects, harnesses, False, start, end)
    observations = accounting.observations
    sources = accounting.sources()
    branches = [branch for usage in accounting.loaded.values() for branch in usage.branches]
    lifecycle = list({signal.identity: signal for usage in accounting.loaded.values() for signal in usage.signals
        if accounting.start is None and accounting.end is None or _in_window(signal.time, accounting.start, accounting.end)}.values())
    return UsageObservations(scope=accounting.scope, totals=totals(observations, accounting.unmeasured_attempts()), coverage=accounting.coverage(),
        warnings=accounting.warnings(), observation_count=len(observations), lifecycle_count=len(lifecycle), source_count=len(sources), offset=offset,
        next_offset=offset+limit if offset+limit<len(observations) else None, source_offset=source_offset,
        next_source_offset=source_offset+source_limit if source_offset+source_limit<len(sources) else None,
        lifecycle_offset=lifecycle_offset, next_lifecycle_offset=lifecycle_offset+lifecycle_limit if lifecycle_offset+lifecycle_limit<len(lifecycle) else None,
        branch_count=len(branches), branch_offset=branch_offset, next_branch_offset=branch_offset+branch_limit if branch_offset+branch_limit<len(branches) else None,
        branches=branches[branch_offset:branch_offset+branch_limit], category_semantics=CATEGORY_SEMANTICS, observations=observations[offset:offset+limit],
        lifecycle=lifecycle[lifecycle_offset:lifecycle_offset+lifecycle_limit], sources=sources[source_offset:source_offset+source_limit])
