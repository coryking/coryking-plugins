"""Report-grade accounting types. Nulls are evidence, never suppressed.

The MCP exposes tokens only. There is deliberately no price/rate model here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal, TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

if TYPE_CHECKING:
    from .providers.base import ProviderSession


class AccountingModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Tokens(AccountingModel):
    """Disjoint input components; reasoning and TTL writes are decompositions.

    Cache creation is separate from uncached input. Never add TTL breakdowns to
    cache_creation, or reasoning_output to output. Null means unrecorded.
    """
    uncached_input: StrictInt | None = None
    cache_read_input: StrictInt | None = None
    cache_creation_input: StrictInt | None = None
    cache_creation_5m: StrictInt | None = None
    cache_creation_1h: StrictInt | None = None
    output: StrictInt | None = None
    reasoning_output: StrictInt | None = None

    @model_validator(mode="after")
    def nonnegative(self):
        if any(v is not None and v < 0 for v in self.model_dump().values()):
            raise ValueError("Token counts must be nonnegative integers")
        return self


class Locator(AccountingModel):
    path: str
    root: str
    line: int
    byte_offset: int
    observed_size: int


class SourceCoverage(AccountingModel):
    path: str
    root: str
    status: str = "observed"
    observed_size: int | None = None
    observed_mtime_ns: int | None = None
    read_boundary: int = 0
    records: int = 0
    malformed_records: int = 0
    unsupported_records: int = 0
    excluded_records: int = 0
    missing_usage_records: int = 0
    reasons: list[str] = Field(default_factory=list)


class RootCoverage(AccountingModel):
    root: str
    harness: str
    status: str
    discovered_sources: int | None = None
    reasons: list[str] = Field(default_factory=list)


class Observation(AccountingModel):
    identity: str
    session: str
    harness: str
    execution: str | None
    request_id: str | None
    identity_quality: str
    time: datetime | None
    model: str | None
    effort: str | None
    configuration_reasons: list[str] = Field(default_factory=list)
    counter_kind: str
    native_usage: dict[str, Any]
    tokens: Tokens
    category_reasons: dict[str, str] = Field(default_factory=dict)
    increment: Tokens | None
    quality: Literal["exact", "partial", "unattributed", "conflict", "excluded", "baseline"]
    reasons: list[str] = Field(default_factory=list)
    semantics: dict[str, str]
    input_observation: int | None = None
    input_semantics: str = "request_input_not_context_occupancy"
    interval_start: datetime | None = None
    service_tier: str | None = None
    sources: list[Locator]
    lifecycle: list[str] = Field(default_factory=list)
    labels: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def explain_null_categories(self):
        self.category_reasons = {name: self.category_reasons.get(name, "native_category_unrecorded_or_underivable")
            for name, value in self.tokens.model_dump().items() if value is None}
        return self


class Signal(AccountingModel):
    identity: str
    kind: str
    time: datetime | None
    source: Locator
    category: str | None = None
    duration_ms: StrictInt | None = None


class ArtifactMeasurement(AccountingModel):
    name: str
    value: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(min_length=1)


class Attribution(AccountingModel):
    session: str | None = None
    observation: str | None = None
    labels: dict[Literal["run", "role", "pass", "round", "phase"], str] = Field(default_factory=dict)
    artifacts: list[ArtifactMeasurement] = Field(default_factory=list)

    @model_validator(mode="after")
    def one_target(self):
        if bool(self.session) == bool(self.observation):
            raise ValueError("Attribution requires exactly one full session or observation identity")
        return self


class CategoryTotal(AccountingModel):
    observed_subtotal: int
    observations_measured: int
    observations_unknown: int


class Totals(AccountingModel):
    categories: dict[str, CategoryTotal]
    accountable_observations: int
    excluded_observations: int
    uncertain_observations: int
    model_requests: int | None
    model_requests_lower_bound: int
    request_count_reason: str | None


class Bounds(AccountingModel):
    first: datetime | None
    latest: datetime | None
    elapsed_span_ms: int | None
    sum_session_spans_ms: int
    overlap_ms: int
    recorded_active_session_work_ms: int | None = None
    recorded_active_work_semantics: str = "Sum of explicit clean-turn session durations; can undercount interruptions and double-count concurrent session time"
    active_work_ms: int | None = None
    active_work_reason: str = "No complete active-work interval evidence; no inactivity heuristic applied"


class SessionSummary(AccountingModel):
    identity: str
    harness: str
    project: str
    worktree: str | None
    parent: str | None
    relationship: str
    role: str | None
    inclusion_reason: str
    totals: Totals
    counts: dict[str, int]
    first: datetime | None
    latest: datetime | None
    first_input: int | None
    latest_input: int | None
    peak_input: int | None
    input_semantics: str
    completion_state: str
    recorded_active_work_ms: int | None
    lifecycle_counts: dict[str, int]
    branches: list[dict[str, Any]]
    provenance: dict[str, Any]
    reasons: list[str]


class Rollup(AccountingModel):
    key: dict[str, str | None]
    totals: Totals


class UsageReport(AccountingModel):
    scope: dict[str, Any]
    totals: Totals
    coverage: dict[str, Any]
    warnings: list[str]
    bounds: Bounds
    rollups: dict[str, list[Rollup]]
    session_count: int
    offset: int
    next_offset: int | None
    sessions: list[SessionSummary]
    artifacts: list[dict[str, Any]]


class UsageObservations(AccountingModel):
    scope: dict[str, Any]
    totals: Totals
    coverage: dict[str, Any]
    warnings: list[str]
    observation_count: int
    lifecycle_count: int
    source_count: int
    offset: int
    next_offset: int | None
    observations: list[Observation]
    lifecycle: list[Signal]
    sources: list[SourceCoverage]


@dataclass
class UsageDiscovery:
    sessions: list[ProviderSession] = field(default_factory=list)
    roots: list[RootCoverage] = field(default_factory=list)
    sources: list[SourceCoverage] = field(default_factory=list)


@dataclass
class SessionUsage:
    ref: ProviderSession
    observations: list[Observation] = field(default_factory=list)
    sources: list[SourceCoverage] = field(default_factory=list)
    signals: list[Signal] = field(default_factory=list)
    counts: dict[str, set[str]] = field(default_factory=dict)
    count_times: dict[str, dict[str, datetime | None]] = field(default_factory=dict)
    times: list[datetime] = field(default_factory=list)
    branches: list[dict[str, Any]] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    provenance: dict[str, Any] = field(default_factory=dict)
    related_sessions: list[ProviderSession] = field(default_factory=list)

    @property
    def identity(self) -> str:
        return f"{self.ref.harness.value}:{self.ref.session_id.full}"

    def count(self, kind: str, identity: str, time: datetime | None = None) -> None:
        self.counts.setdefault(kind, set()).add(identity)
        self.count_times.setdefault(kind, {})[identity] = time
