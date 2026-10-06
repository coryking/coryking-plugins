"""Bounded physical evidence, shared validation and request reconciliation."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import orjson

from .usage_models import Locator, Observation, RootCoverage, SourceCoverage, Tokens

CORE = ("uncached_input", "cache_read_input", "cache_creation_input", "output")


def as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}
CLAUDE_SEMANTICS = {
    "input": "native input excludes cache reads and writes",
    "output": "reasoning output is included in output",
    "cache_creation": "5m and 1h are breakdowns of aggregate cache creation",
}
CODEX_SEMANTICS = {
    "input": "native input includes cached input and cache-write input",
    "output": "reasoning output is included in output",
    "cache_creation": "cache-write input is an input breakdown; TTL is unrecorded",
}


def timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo is not None else None
    except ValueError:
        return None


def walk_sources(root: Path, harness: str) -> tuple[list[Path], RootCoverage]:
    """os.walk reports enumeration failures instead of quietly treating them as empty."""
    coverage = RootCoverage(root=str(root), harness=harness, status="observed")
    paths: list[Path] = []
    if not root.exists():
        coverage.status = "missing"
        coverage.reasons.append("root_missing")
        return paths, coverage
    def onerror(exc: OSError):
        coverage.status = "partial"
        coverage.reasons.append(f"enumeration_failed:{exc.filename}")
    for directory, _, files in os.walk(root, onerror=onerror):
        paths.extend(Path(directory) / name for name in files if name.endswith(".jsonl"))
    coverage.discovered_sources = len(paths)
    return sorted(paths), coverage


def snapshot(path: Path, root: Path, observed: SourceCoverage | None = None) -> tuple[list[tuple[dict, Locator]], SourceCoverage]:
    """Read exactly the size seen on open; exclude a non-newline tail.

    No persistent parsing cache: every call captures fresh bounds and diagnostics.
    An in-place rewrite or truncation detected during reading remains partial.
    """
    cov = SourceCoverage(path=str(path), root=str(root))
    records: list[tuple[dict, Locator]] = []
    try:
        with path.open("rb") as stream:
            stat = os.fstat(stream.fileno())
            cov.observed_size = observed.observed_size if observed and observed.observed_size is not None else stat.st_size
            cov.observed_mtime_ns = observed.observed_mtime_ns if observed else stat.st_mtime_ns
            if observed and (stat.st_size, stat.st_mtime_ns) != (observed.observed_size, observed.observed_mtime_ns):
                cov.status = "partial"
                cov.reasons.append("source_changed_between_snapshot_reads")
            number = 0
            while stream.tell() < cov.observed_size:
                start = stream.tell()
                line = stream.readline(cov.observed_size - start)
                number += 1
                cov.read_boundary = stream.tell()
                if not line.endswith(b"\n"):
                    cov.status = "partial"
                    cov.reasons.append("truncated_or_live_tail")
                    cov.excluded_records += 1
                    break
                if not line.strip():
                    continue
                loc = Locator(path=str(path), root=str(root), line=number,
                              byte_offset=start, observed_size=cov.observed_size)
                try:
                    data = orjson.loads(line)
                    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
                        raise ValueError("record envelope")
                except (ValueError, orjson.JSONDecodeError):
                    cov.malformed_records += 1
                    continue
                records.append((data, loc))
                cov.records += 1
            after = os.fstat(stream.fileno())
            if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                cov.status = "partial"
                cov.reasons.append("source_changed_during_snapshot")
            if cov.read_boundary < cov.observed_size:
                cov.status = "partial"
                cov.reasons.append("source_truncated_during_snapshot")
    except OSError as exc:
        cov.status = "unreadable"
        cov.reasons.append(f"read_failed:{type(exc).__name__}")
    return records, cov


class SnapshotReader:
    """One bounded read per physical source in one report/observation call."""

    def __init__(self):
        self._snapshots = {}

    def read(self, path: Path, root: Path):
        key = (path, root)
        if key not in self._snapshots:
            self._snapshots[key] = snapshot(path, root)
        elif self._snapshots[key][0] is None:
            self._snapshots[key] = snapshot(path, root, self._snapshots[key][1])
        return self._snapshots[key]

    def release_records(self):
        """Keep boundaries, free transcript content after one logical session."""
        self._snapshots = {key: (None, value[1]) for key, value in self._snapshots.items()}


def counter(data: dict, name: str, reasons: list[str]) -> int | None:
    value = data.get(name)
    if value is None:
        reasons.append(f"missing_category:{name}")
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        reasons.append(f"invalid_category:{name}")
        return None
    return value


def native_tokens(data: dict, harness: str) -> tuple[Tokens, int | None, list[str]]:
    reasons: list[str] = []
    native_input = counter(data, "input_tokens", reasons)
    output = counter(data, "output_tokens", reasons)
    if harness == "claude":
        read = counter(data, "cache_read_input_tokens", reasons)
        write = counter(data, "cache_creation_input_tokens", reasons)
        ttl = data.get("cache_creation") or {}
        ttl = ttl if isinstance(ttl, dict) else {}
        five = counter(ttl, "ephemeral_5m_input_tokens", [])
        hour = counter(ttl, "ephemeral_1h_input_tokens", [])
        reasoning = counter(as_dict(data.get("output_tokens_details")), "thinking_tokens", [])
        uncached = native_input
        prompt = native_input + read + write if all(v is not None for v in (native_input, read, write)) else None
        if five is not None and hour is not None:
            if write is None:
                write = five + hour
                reasons = [r for r in reasons if r != "missing_category:cache_creation_input_tokens"]
                prompt = native_input + read + write if native_input is not None and read is not None else None
            elif five + hour != write:
                reasons.append("inconsistent_cache_creation_breakdown")
    else:
        read = counter(data, "cached_input_tokens", reasons)
        # Older TokenUsage shapes did not record writes. Unknown is not zero.
        write = counter(data, "cache_write_input_tokens", reasons)
        reasoning = counter(data, "reasoning_output_tokens", [])
        five = hour = None
        prompt = native_input
        if native_input is not None and read is not None and read > native_input:
            reasons.append("inconsistent_input_breakdown")
        uncached = native_input - read - write if all(v is not None for v in (native_input, read, write)) else None
        if uncached is not None and uncached < 0:
            reasons.append("inconsistent_input_breakdown")
            uncached = None
        if "total_tokens" in data:
            total = counter(data, "total_tokens", reasons)
            if all(v is not None for v in (native_input, output, total)) and total != native_input + output:
                reasons.append("context_or_inconsistent_total_not_consumption")
    if reasoning is not None and output is not None and reasoning > output:
        reasons.append("inconsistent_reasoning_breakdown")
    return Tokens(uncached_input=uncached, cache_read_input=read, cache_creation_input=write,
                  cache_creation_5m=five, cache_creation_1h=hour, output=output,
                  reasoning_output=reasoning), prompt, reasons


def reconcile_requests(observations: list[Observation]) -> list[Observation]:
    """Reconcile multipart fragments within each file, then compare copies.

    Stable request IDs are global within a harness. Fallback identities include
    execution and sequence, never counter content or a physical file path.
    """
    grouped: dict[str, list[Observation]] = {}
    for obs in observations:
        grouped.setdefault(obs.identity, []).append(obs)
    result: list[Observation] = []
    for identity, group in sorted(grouped.items()):
        by_path: dict[str, list[Observation]] = {}
        for obs in group:
            by_path.setdefault(obs.sources[0].path, []).append(obs)
        candidates: list[Observation] = []
        for fragments in by_path.values():
            fragments.sort(key=lambda o: o.sources[0].line)
            chosen = fragments[-1].model_copy(deep=True)
            merged: dict[str, int | None] = {name: None for name in Tokens.model_fields}
            malformed = False
            native: dict[str, Any] = {}
            for fragment in fragments:
                native.update(fragment.native_usage)
                for name, value in fragment.tokens.model_dump().items():
                    previous = merged[name]
                    if value is not None:
                        # Output grows across streaming blocks. Input is immutable
                        # once populated; zero initial placeholders can advance.
                        if previous not in (None, 0, value) and (
                            name != "output" or value < previous
                        ):
                            malformed = True
                        merged[name] = value
            chosen.tokens = Tokens(**merged)
            chosen.native_usage = native
            chosen.increment = chosen.tokens if chosen.quality != "excluded" else None
            chosen.sources = [loc for fragment in fragments for loc in fragment.sources]
            chosen.lifecycle = sorted({s for fragment in fragments for s in fragment.lifecycle})
            chosen.reasons = sorted({r for f in fragments for r in f.reasons
                                     if not r.startswith("missing_category:")})
            if all(merged[k] is not None for k in CORE):
                chosen.reasons = [r for r in chosen.reasons if r != "missing_usage"]
            chosen.reasons.extend(f"missing_category:{k}" for k in CORE if merged[k] is None)
            if malformed or any(r.startswith(("invalid_", "inconsistent_")) for r in chosen.reasons):
                chosen.reasons.append("inconsistent_streaming_fragments")
                chosen.quality = "conflict"
                chosen.increment = None
            elif chosen.quality != "excluded":
                chosen.quality = "partial" if chosen.reasons else "exact"
            candidates.append(chosen)
        candidates.sort(key=lambda o: (o.quality == "excluded", o.sources[0].path))
        chosen = candidates[0]
        active = [o for o in candidates if o.quality != "excluded"]
        if active:
            chosen = active[0]
            # A prefix copy without a terminal signal can be reconciled with its
            # extension. Two terminal copies disagreeing are a conflict.
            for other in active[1:]:
                a, b = chosen.tokens.model_dump(), other.tokens.model_dump()
                compatible = all(a[k] is None or b[k] is None or a[k] == b[k] for k in a if k != "output")
                if a["output"] != b["output"]:
                    compatible &= not (chosen.lifecycle and other.lifecycle)
                compatible &= all(a is None or b is None or a == b for a, b in ((chosen.model, other.model), (chosen.effort, other.effort), (chosen.service_tier, other.service_tier)))
                if not compatible or other.quality == "conflict":
                    chosen.quality = "conflict"
                    chosen.increment = None
                    chosen.reasons.append("conflicting_copies")
                elif chosen.quality != "conflict":
                    values = {k: a[k] if b[k] is None else b[k] if a[k] is None else max(a[k], b[k]) for k in a}
                    chosen.tokens = Tokens(**values)
                    chosen.increment = chosen.tokens
                    chosen.model = chosen.model or other.model
                    chosen.effort = chosen.effort or other.effort
                    chosen.service_tier = chosen.service_tier or other.service_tier
        chosen.sources = sorted({(s.path, s.line): s for o in candidates for s in o.sources}.values(), key=lambda s: (s.path, s.line))
        chosen.category_reasons = {name: "native_category_unrecorded_or_underivable" for name, value in chosen.tokens.model_dump().items() if value is None}
        result.append(chosen)
    return result
