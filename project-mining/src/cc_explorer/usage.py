"""Workload selection, shared accounting arithmetic and bounded MCP reports."""
from __future__ import annotations

from dataclasses import replace
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
from .usage_sources import SnapshotReader, reconcile_requests, timestamp
from .utils import PrefixId

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
    return Totals(categories=categories, accountable_observations=len(eligible),
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
        selected = {_resolve(s, self.refs): "explicit" for s in sessions or []}
        if not sessions:
            selected.update({key: "project_scope" if project_scope else "corpus_scope" for key, ref in self.refs.items() if not project_scope or ref.project_path in project_scope})
        elif project_scope:
            for key in selected:
                if self.refs[key].project_path not in project_scope:
                    raise ValueError(f"Explicit session {key} is outside selected projects")
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
        for key in sorted(selected):
            usage = self.providers[self.refs[key].harness.value].load_usage(self.refs[key], snapshots)
            snapshots.release_records()
            self.loaded[key] = usage
            self.refs[key] = usage.ref
            if include_descendants:
                for related in usage.related_sessions:
                    child_key = f"{related.harness.value}:{related.session_id.full}"
                    if child_key in self.loaded:
                        child = self.loaded[child_key]
                        child.ref = replace(child.ref, relationship="dispatched")
                        self.refs[child_key] = child.ref
                    elif child_key not in self.membership:
                        self.refs[child_key] = related
                        self.membership[child_key] = f"dispatch_of:{key}"
                        self.loaded[child_key] = SessionUsage(related, reasons=["dispatched_child_source_unavailable"])
            for observation in usage.observations:
                if observation.session != key and observation.session not in self.membership:
                    if include_descendants:
                        child_ref = replace(usage.ref, session_id=PrefixId(observation.session.split(":", 1)[1]),
                            paths=(), parent_id=key, relationship="nested_progress", role=None)
                        self.refs[observation.session] = child_ref
                        self.membership[observation.session] = f"nested_progress_of:{key}"
                        self.loaded.setdefault(observation.session, SessionUsage(child_ref))
                    else:
                        observation.quality = "excluded"
                        observation.increment = None
                        observation.reasons.append("descendants_disabled")
                raw.append(observation)
        requests = [o for o in raw if o.counter_kind == "request"]
        counters = [o for o in raw if o.counter_kind != "request"]
        # Counter identities are execution+ordinal; replicated physical copies
        # must agree on native usage and increment, never silently first-wins.
        by_counter: dict[str, Observation] = {}
        for obs in counters:
            prior = by_counter.get(obs.identity)
            if prior is None:
                by_counter[obs.identity] = obs
            else:
                if (prior.native_usage, prior.increment, prior.model, prior.effort) != (obs.native_usage, obs.increment, obs.model, obs.effort):
                    prior.quality = "conflict"
                    prior.increment = None
                    prior.reasons.append("conflicting_copies")
                prior.sources.extend(obs.sources)
        normalized = reconcile_requests(requests) + list(by_counter.values())
        self.all_observations = sorted(normalized, key=lambda o: (o.time is None, o.time.isoformat() if o.time else "", o.execution or "", min(s.line for s in o.sources), o.identity))
        self.observations = []
        for obs in self.all_observations:
            if start is not None or end is not None:
                if obs.time is None:
                    obs.quality = "excluded"
                    obs.increment = None
                    obs.reasons.append("window_time_unavailable")
                    self.observations.append(obs)
                    continue
                if not _in_window(obs.time, self.start, self.end):
                    continue
                if obs.counter_kind == "cumulative" and self.start and obs.quality not in {"excluded", "baseline", "conflict"} and (obs.interval_start is None or obs.interval_start < self.start):
                    obs.quality = "excluded"
                    obs.increment = None
                    obs.reasons.append("counter_interval_crosses_window_start")
            self.observations.append(obs)
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
            "members": [{"identity": key, "reason": reason} for key, reason in sorted(self.membership.items())],
            "accounting_basis": "bounded_retained_execution_evidence", "snapshot_consistency": "per_source_observed_boundaries_not_atomic_corpus"}
        self.scope["storage_host"] = socket.gethostname()

    def sources(self):
        # Baseline reads and same physical files can recur; report each source
        # once while preserving the most pessimistic snapshot diagnostics.
        result = {s.path: s for s in self.discovery_sources}
        for usage in self.loaded.values():
            for source in usage.sources:
                prior = result.get(source.path)
                if prior is None or len(source.reasons) > len(prior.reasons):
                    result[source.path] = source
        return list(result.values())

    def unmeasured_attempts(self, sessions=None):
        return any(signal.kind in {"model_error", "stream_error"} and (self.start is None and self.end is None or _in_window(signal.time, self.start, self.end))
            for key, usage in self.loaded.items() if sessions is None or key in sessions for signal in usage.signals)

    def coverage(self):
        sources = self.sources()
        obs = self.observations
        missing = {o.session for o in obs if o.increment is not None and any(getattr(o.increment, k) is None for k in ("uncached_input", "cache_read_input", "cache_creation_input", "output"))}
        missing.update(k for k in self.membership if not any(o.session == k and o.quality not in {"excluded", "baseline"} for o in obs))
        conflicting = {o.session for o in obs if o.quality == "conflict"}
        uncertain = any(o.quality in {"partial", "unattributed", "baseline", "conflict"} or "window_time_unavailable" in o.reasons or "counter_interval_crosses_window_start" in o.reasons for o in obs)
        root_uncertain = any(r.status != "observed" for r in self.roots)
        source_uncertain = any(s.status not in {"observed"} or s.malformed_records for s in sources)
        session_uncertain = any(any("unavailable" in r or "unrecorded" in r or "conflicting" in r for r in s.reasons if not r.startswith("retained_observed_history")) for s in self.loaded.values())
        discovered_sources = sum(r.discovered_sources or 0 for r in self.roots)
        included_paths = {s.path for usage in self.loaded.values() for s in usage.sources}
        return {"complete_observed_usage": not (uncertain or root_uncertain or source_uncertain or session_uncertain or missing or self.unmeasured_attempts()),
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
        reasons.update(r for root in self.roots for r in root.reasons)
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
            observations = [o for o in self.observations if o.session == key]
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
                branches=usage.branches, provenance=usage.provenance, reasons=sorted(set(usage.reasons))))
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
               start=None, end=None, attribution=None, offset=0, limit=50) -> UsageReport:
    _pagination(offset, limit)
    accounting = Accounting(sessions, projects, harnesses, include_descendants, start, end, attribution)
    rows = accounting.summaries()
    rollups = {}
    for dimension in ("session", "model_effort", "project", "role", "caller_labels"):
        groups: dict[tuple, tuple[dict, list]] = {}
        for obs in accounting.observations:
            ref = accounting.refs.get(obs.session)
            if dimension == "session":
                key = {"session": obs.session}
            elif dimension == "model_effort":
                key = {"model": obs.model, "effort": obs.effort}
            elif dimension == "project":
                key = {"project": ref.project_path if ref else None}
            elif dimension == "role":
                key = {"role": ref.role if ref else None}
            else:
                key = {label: obs.labels.get(label) for label in ("run", "role", "pass", "round", "phase")}
            packed = tuple(sorted(key.items()))
            groups.setdefault(packed, (key, []))[1].append(obs)
        rollups[dimension] = [Rollup(key=key, totals=totals(observations, accounting.unmeasured_attempts({o.session for o in observations}))) for key, observations in groups.values()]
    return UsageReport(scope=accounting.scope, totals=totals(accounting.observations, accounting.unmeasured_attempts()), coverage=accounting.coverage(),
        warnings=accounting.warnings(), bounds=_bounds(rows), rollups=rollups,
        session_count=len(rows), offset=offset, next_offset=offset+limit if offset+limit<len(rows) else None,
        sessions=rows[offset:offset+limit], artifacts=accounting.artifacts)


def get_observations(*, session, projects=None, harnesses=None, start=None, end=None, offset=0, limit=100) -> UsageObservations:
    _pagination(offset, limit)
    accounting = Accounting([session], projects, harnesses, False, start, end)
    observations = accounting.observations
    sources = accounting.sources()
    lifecycle = list({signal.identity: signal for usage in accounting.loaded.values() for signal in usage.signals
        if accounting.start is None and accounting.end is None or _in_window(signal.time, accounting.start, accounting.end)}.values())
    return UsageObservations(scope=accounting.scope, totals=totals(observations, accounting.unmeasured_attempts()), coverage=accounting.coverage(),
        warnings=accounting.warnings(), observation_count=len(observations), lifecycle_count=len(lifecycle), source_count=len(sources), offset=offset,
        next_offset=offset+limit if offset+limit<max(len(observations), len(lifecycle), len(sources)) else None,
        observations=observations[offset:offset+limit], lifecycle=lifecycle[offset:offset+limit], sources=sources[offset:offset+limit])
