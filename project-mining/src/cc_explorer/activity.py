"""Activity timeline — cross-project 5-min turn-count grid over a time window.

Ports the proven extraction logic from the fleet-timeline prototype into the
package as a pure, parameterized, PII-free core. `build_activity_timeline`
walks every selected project's transcripts (plus their subagent bodies),
buckets human/agent turns into a fixed grid, and rolls the grid up into an
attention summary for an LLM analyst.

The function is pure: it never prints, writes files, or touches the network.
All parsing goes through the cached `load_transcript`.

Vocabulary (these are the field definitions surfaced in the MCP output schema):

- A *human turn* is a non-interrupt `HumanEntry` whose timestamp falls in the
  window. Interrupt sentinels ("[Request interrupted") are NOT human turns —
  they arrive as either a HumanEntry (turn-level esc) or a ToolResultEntry (esc
  that cut off a tool call) and are counted as `interrupts` instead.
- An *agent turn* in a bucket is one distinct API request (deduped by
  `requestId`) of an AssistantTranscriptEntry, plus a marker for any
  tool-result activity in that bucket. A subagent's internal activity — INCLUDING
  its own internal human turns, which are the orchestration protocol, not human
  attention — folds into the parent as agent turns.
- In an *agent-team* session, a worker's user-role turns are mostly INJECTED BY
  TEAMMATES (an orchestrator/peer DMing the pane via `<teammate-message ...>`),
  not typed by the human. A teammate-injected turn is NOT a human turn, NOT an
  interrupt, and NOT an opening/closing candidate; it counts as agent activity
  in its bucket (one `tm_` marker per turn). A genuine human-typed turn in the
  same session survives as a human turn. `team`/`team_role` on a session row
  carry the session's `teamName`/`agentName`; `summary.interactive.team_sessions`
  counts interactive sessions with a non-null team.
- `active_min` = (# buckets with >=1 human turn in >=1 interactive session) x
  bucket_minutes. `multitask_min` requires >=2 distinct interactive sessions
  with human turns in the bucket.
- `turn_min` = sum of `turn_duration` `durationMs` (system entries) in-window,
  in minutes. It is a FLOOR: an interrupted turn emits no duration record.
- Headless sessions (entrypoint == "sdk-cli") are machine work: present in the
  sessions list and timeline grid, EXCLUDED from every interactive rollup
  (active_min, multitask_min, peaks, the per-day hour arrays).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

from .models import (
    AssistantTranscriptEntry,
    BaseTranscriptEntry,
    HumanEntry,
    SummaryTranscriptEntry,
    SystemTranscriptEntry,
    ToolResultEntry,
    UserOrigin,
    substantive_human_text,
)
from .parser import load_conversations, load_transcript
from .search import resolve_projects, session_title
from .subagents import collect_agent_files, resolve_subagents_dir


# =============================================================================
# Text helpers
# =============================================================================


def _collapse(text: str, limit: int) -> str:
    """Whitespace-collapse and truncate to ~limit chars (no marker padding)."""
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        return collapsed[:limit].rstrip()
    return collapsed


# =============================================================================
# Per-transcript scan
# =============================================================================


class _Scan:
    """In-window tallies for one transcript (a session OR a subagent body)."""

    __slots__ = ("human", "agent_req", "turn_ms", "interrupts", "branches",
                 "models", "first_human", "last_human", "summary",
                 "team", "team_role")

    def __init__(self) -> None:
        self.human: Counter[int] = Counter()           # bucket -> human turn count
        self.agent_req: dict[int, set[str]] = defaultdict(set)  # bucket -> request ids
        self.turn_ms: int = 0
        self.interrupts: int = 0
        self.branches: list[str] = []                  # gitBranch, first-appearance order
        self.models: Counter[str] = Counter()          # assistant model -> count
        self.first_human: Optional[str] = None         # first non-interrupt human text
        self.last_human: Optional[str] = None           # last non-interrupt human text
        self.summary: Optional[str] = None              # latest stored summary entry
        self.team: Optional[str] = None                 # teamName (agent-team worker)
        self.team_role: Optional[str] = None            # agentName (this worker's role)


def _scan(path: Path, lo: datetime, hi: datetime, bucket_s: int) -> Optional[_Scan]:
    """Scan one transcript, tallying only entries whose timestamp is in [lo, hi).

    Returns None if the file can't be parsed.
    """
    try:
        entries = load_transcript(path)
    except Exception:
        return None

    s = _Scan()
    seen_branches: set[str] = set()

    def in_window(t: Optional[datetime]) -> bool:
        return t is not None and lo <= t < hi

    def bucket(t: datetime) -> int:
        return int((t - lo).total_seconds() // bucket_s)

    for e in entries:
        # gitBranch (whole-file, not window-filtered — a session's branch identity
        # is a property of the work, recorded on every entry).
        if isinstance(e, BaseTranscriptEntry) and e.gitBranch and e.gitBranch not in seen_branches:
            seen_branches.add(e.gitBranch)
            s.branches.append(e.gitBranch)

        # Agent-team membership (whole-file identity, stamped on every entry).
        if isinstance(e, BaseTranscriptEntry):
            if s.team is None and e.teamName:
                s.team = e.teamName
            if s.team_role is None and e.agentName:
                s.team_role = e.agentName

        if isinstance(e, SummaryTranscriptEntry):
            # Summary entries carry no timestamp; keep the last one in file order.
            if e.summary:
                s.summary = e.summary
            continue

        if isinstance(e, SystemTranscriptEntry):
            if e.subtype == "turn_duration" and in_window(e.timestamp):
                s.turn_ms += e.durationMs or 0
            continue

        if isinstance(e, HumanEntry):
            if not in_window(e.timestamp):
                continue
            origin = e.origin
            if origin is UserOrigin.interrupt:
                s.interrupts += 1
                continue
            if origin is UserOrigin.teammate:
                # A peer/orchestrator DMed this worker's pane — orchestration
                # protocol, not human attention. Does NOT count as a human turn,
                # an interrupt, or an opening/closing candidate; it DOES count as
                # agent activity in its bucket (one marker per turn, same dedup
                # mechanism as the subagent fold's subh_ markers).
                b = bucket(e.timestamp)
                s.agent_req[b].add(f"tm_{b}_{e.uuid}")
                continue
            if origin is UserOrigin.meta:
                # System-injected (isMeta) turns are not human attention and are
                # not agent activity — fall through silently (preserves prior
                # behavior, where MetaEntry was a distinct type and never reached
                # here, and where an isMeta HumanEntry would have been treated as
                # human; routing it to "not a human turn" is the correct read).
                continue
            # origin is human OR command_scaffolding: both still count as a human
            # turn (deferred decision — see UserOrigin docstring).
            s.human[bucket(e.timestamp)] += 1
            # opening/closing track only SUBSTANTIVE turns — a bare /clear or
            # other command scaffolding still counts as a human turn but carries
            # no prompt, so it must not become the opening/closing text.
            txt = substantive_human_text(e)
            if txt:
                if s.first_human is None:
                    s.first_human = txt
                s.last_human = txt
            continue

        if isinstance(e, AssistantTranscriptEntry):
            if not in_window(e.timestamp):
                continue
            b = bucket(e.timestamp)
            rid = e.requestId or f"_{e.timestamp.isoformat()}"
            s.agent_req[b].add(rid)
            if e.message.model:
                s.models[e.message.model] += 1
            continue

        if isinstance(e, ToolResultEntry):
            if not in_window(e.timestamp):
                continue
            if e.origin is UserOrigin.interrupt:
                s.interrupts += 1
                continue
            b = bucket(e.timestamp)
            s.agent_req[b].add(f"tr_{b}")
            continue

    return s


def _has_activity(s: _Scan) -> bool:
    return bool(s.human or s.agent_req)


# =============================================================================
# Label formatting
# =============================================================================


def _local(t: datetime, tz: timezone | ZoneInfo) -> datetime:
    return t.astimezone(tz)


def _bucket_dt(b: int, lo: datetime, bucket_s: int) -> datetime:
    return lo + timedelta(seconds=b * bucket_s)


# =============================================================================
# Core
# =============================================================================


def build_activity_timeline(
    projects: Optional[list[str]] = None,
    after: Optional[datetime] = None,
    before: Optional[datetime] = None,
    bucket_minutes: int = 5,
    tz: Optional[str] = None,
) -> dict[str, Any]:
    """Build a cross-project activity timeline over a time window.

    Args:
        projects: project selector (paths or bare names; worktrees flattened).
            None/empty => ALL projects (cross-project attention is the point).
        after, before: half-open window [after, before). Naive datetimes are
            interpreted in `tz`. Defaults to the last 7 days ending now.
        bucket_minutes: grid grain in minutes.
        tz: IANA tz name for ALL day/hour bucketing and displayed labels.
            None => system local time.

    Returns the payload dict documented in the get_activity_timeline output
    schema. Pure: no I/O beyond reading transcripts through the cached parser.
    """
    zone: timezone | ZoneInfo = ZoneInfo(tz) if tz else _system_tz()

    lo, hi = _resolve_window(after, before, zone)
    bucket_s = bucket_minutes * 60
    n_buckets = int((hi - lo).total_seconds() // bucket_s)
    # Whole days in the window, DST-robust: a 7-local-day window that straddles a
    # DST transition spans 7*86400 ± 3600 wall-clock seconds, so floor division
    # would report 6 or 8; round() recovers the intended day count.
    n_days = max(1, round((hi - lo).total_seconds() / 86400))

    # Precompute the local datetime of every bucket once — reused by every label
    # form below and by the day grouping/sort/hour loops in _build_days.
    labels = [_local(_bucket_dt(b, lo, bucket_s), zone) for b in range(n_buckets)]

    def label_dt(b: int) -> datetime:
        return labels[b]

    def day_label(b: int) -> str:
        return labels[b].strftime("%a %m-%d %H:%M")

    def key_label(b: int) -> str:
        return labels[b].strftime("%m-%d %H:%M")

    # ------------------------------------------------------------------ scan
    proj_paths = resolve_projects(projects)
    # Per-session record built up during the walk. sid (8-char) -> record dict.
    records: dict[str, dict[str, Any]] = {}
    grid: dict[str, dict[int, list[int]]] = {}  # sid -> {bucket: [human, agent]}
    seen: set[str] = set()

    for proj_path in proj_paths:
        proj_name = Path(proj_path).name
        for full_uuid, ref in load_conversations(proj_path).items():
            uuid = full_uuid.full
            if uuid in seen:
                continue
            scan = _scan(ref.path, lo, hi, bucket_s)
            if scan is None:
                continue

            # Fold subagents: their agent activity unions into the parent, their
            # internal human turns become parent agent work, their turn_min adds.
            # Conversion artifacts are skipped — they preserve original timestamps
            # and would double-count the source's history as the parent's agent activity.
            n_sub = 0
            for af in collect_agent_files(resolve_subagents_dir(ref.path)):
                if af.is_conversion_artifact:
                    continue
                cs = _scan(af.path, lo, hi, bucket_s)
                if cs is None or not _has_activity(cs):
                    continue
                n_sub += 1
                for b, reqs in cs.agent_req.items():
                    scan.agent_req[b] |= reqs
                for b, n in cs.human.items():
                    scan.agent_req[b].add(f"subh_{af.agent_id[:8]}_{b}")
                scan.turn_ms += cs.turn_ms

            active = sorted(set(scan.human) | set(scan.agent_req))
            if not active:
                continue
            seen.add(uuid)
            sid = uuid[:8]

            grid[sid] = {
                b: [scan.human.get(b, 0), len(scan.agent_req.get(b, ()))]
                for b in active
            }

            # entrypoint / headless / title need the parsed entries; reuse cache.
            entries = load_transcript(ref.path)
            entrypoint, headless = _entrypoint(entries)
            title = session_title(entries)

            human_turns = sum(scan.human.values())
            agent_turns = sum(len(v) for v in scan.agent_req.values())
            human_buckets = sum(1 for b in active if scan.human.get(b))
            agent_only_buckets = sum(
                1 for b in active if not scan.human.get(b) and scan.agent_req.get(b)
            )

            records[sid] = {
                "id": sid,
                "project": proj_name,
                "headless": headless,
                "entrypoint": entrypoint,
                "team": scan.team,
                "team_role": scan.team_role,
                "model": (scan.models.most_common(1)[0][0] if scan.models else None),
                "branches": scan.branches,
                "start": day_label(active[0]),
                "end": day_label(active[-1]),
                "human_turns": human_turns,
                "agent_turns": agent_turns,
                "amplification": (
                    round(agent_turns / human_turns, 1) if human_turns else None
                ),
                "n_sub": n_sub,
                "turn_min": round(scan.turn_ms / 60000),
                "human_active_min": human_buckets * bucket_minutes,
                "agent_only_min": agent_only_buckets * bucket_minutes,
                "interrupts": scan.interrupts,
                "title": title,
                "opening": (
                    _collapse(scan.first_human, 300) if scan.first_human else None
                ),
                "closing": (
                    _collapse(scan.last_human, 200) if scan.last_human else None
                ),
                "summary": scan.summary,
            }

    # ------------------------------------------------------- timeline pivot
    # Invert grid (sid -> {bucket: cell}) into bucket -> {sid: cell} by walking
    # each session's active cells once, then emit rows in ascending bucket order
    # (byte-identical to the prior dense scan over range(n_buckets)).
    by_bucket: dict[int, dict[str, list[int]]] = defaultdict(dict)
    for sid, cells in grid.items():
        for b, cell in cells.items():
            by_bucket[b][sid] = cell
    timeline: dict[str, dict[str, list[int]]] = {
        key_label(b): by_bucket[b] for b in sorted(by_bucket)
    }

    # -------------------------------------- interactive vs headless rollups
    interactive_sids = {sid for sid, r in records.items() if not r["headless"]}

    # Per-bucket interactive attention vectors, computed ONCE here and reused by
    # the summary rollup AND _build_days. Headless sessions are machine work and
    # excluded from every one of these (driven, autonomous, the by-bucket sums).
    driven = [0] * n_buckets        # distinct interactive sessions with a human turn
    autonomous = [0] * n_buckets    # distinct interactive sessions: agent activity, no human turn
    human_by_bucket = [0] * n_buckets   # summed interactive human turns
    agent_by_bucket = [0] * n_buckets   # summed interactive agent turns
    for sid, cells in grid.items():
        if records[sid]["headless"]:
            continue
        for b, (h, a) in cells.items():
            if h:
                driven[b] += 1
                human_by_bucket[b] += h
            elif a:
                autonomous[b] += 1
            agent_by_bucket[b] += a

    def peak(vec: list[int]) -> tuple[int, Optional[int]]:
        if not vec:
            return 0, None
        v = max(vec)
        return v, (vec.index(v) if v else None)

    peak_driven, peak_driven_b = peak(driven)
    peak_auto, peak_auto_b = peak(autonomous)

    interactive_active_min = sum(1 for x in driven if x) * bucket_minutes
    interactive_multitask_min = sum(1 for x in driven if x >= 2) * bucket_minutes

    inter_records = [records[sid] for sid in interactive_sids]
    head_records = [r for sid, r in records.items() if r["headless"]]

    summary = {
        "interactive": {
            "sessions": len(inter_records),
            "active_min": interactive_active_min,
            "multitask_min": interactive_multitask_min,
            "peak_sessions_driven": peak_driven,
            "peak_at": (day_label(peak_driven_b) if peak_driven_b is not None else None),
            "peak_autonomous_sessions": peak_auto,
            "peak_autonomous_at": (
                day_label(peak_auto_b) if peak_auto_b is not None else None
            ),
            "human_turns": sum(r["human_turns"] for r in inter_records),
            "interrupts": sum(r["interrupts"] for r in inter_records),
            "machine_hours": round(
                sum(r["turn_min"] for r in inter_records) / 60, 1
            ),
            "team_sessions": sum(1 for r in inter_records if r["team"]),
        },
        "headless": {
            "sessions": len(head_records),
            "human_turns": sum(r["human_turns"] for r in head_records),
            "machine_hours": round(sum(r["turn_min"] for r in head_records) / 60, 1),
        },
        "by_project": _by_project(records, bucket_minutes, grid),
    }

    # ----------------------------------------------------------- days array
    days = _build_days(
        grid,
        records,
        bucket_minutes,
        n_buckets,
        labels,
        driven,
        human_by_bucket,
        agent_by_bucket,
    )

    # -------------------------------------------------------- sessions list
    sessions_out = _sorted_sessions(records)

    return {
        "window": {
            "after": _local(lo, zone).isoformat(),
            "before": _local(hi, zone).isoformat(),
            "tz": str(getattr(zone, "key", zone)),
            "bucket_minutes": bucket_minutes,
            "days": n_days,
        },
        "summary": summary,
        "days": days,
        "sessions": sessions_out,
        "timeline": timeline,
    }


# =============================================================================
# Helpers
# =============================================================================


def _system_tz() -> timezone:
    """System local tz as a fixed-offset timezone (good enough for labeling)."""
    local = datetime.now().astimezone().tzinfo
    if isinstance(local, timezone):
        return local
    # astimezone() always yields a tzinfo with a fixed utcoffset for "now".
    offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    return timezone(offset)


def _resolve_window(
    after: Optional[datetime],
    before: Optional[datetime],
    zone: timezone | ZoneInfo,
) -> tuple[datetime, datetime]:
    """Resolve [after, before) to UTC-aware bounds.

    Naive datetimes are interpreted in `zone`. Default: last 7 days ending now.
    """
    def aware(dt: datetime) -> datetime:
        return dt.replace(tzinfo=zone) if dt.tzinfo is None else dt

    if before is None:
        hi = datetime.now(tz=zone)
    else:
        hi = aware(before)
    if after is None:
        lo = hi - timedelta(days=7)
    else:
        lo = aware(after)
    return lo.astimezone(timezone.utc), hi.astimezone(timezone.utc)


def _entrypoint(entries: list[Any]) -> tuple[Optional[str], bool]:
    """First entrypoint value seen and whether the session is headless."""
    for e in entries:
        if isinstance(e, BaseTranscriptEntry) and e.entrypoint:
            return e.entrypoint, e.is_headless
    return None, False


def _by_project(
    records: dict[str, dict[str, Any]],
    bucket_minutes: int,
    grid: dict[str, dict[int, list[int]]],
) -> list[dict[str, Any]]:
    """Per-project rollup. active_min counts buckets with an interactive human
    turn anywhere in the project (union across that project's sessions)."""
    by_proj: dict[str, dict[str, Any]] = {}
    proj_active_buckets: dict[str, set[int]] = defaultdict(set)

    for sid, r in records.items():
        p = r["project"]
        agg = by_proj.setdefault(
            p,
            {
                "project": p,
                "sessions": 0,
                "headless_sessions": 0,
                "active_min": 0,
                "human_turns": 0,
                "turn_min": 0,
            },
        )
        if r["headless"]:
            agg["headless_sessions"] += 1
        else:
            agg["sessions"] += 1
            agg["human_turns"] += r["human_turns"]
            for b, (h, _a) in grid[sid].items():
                if h:
                    proj_active_buckets[p].add(b)
        agg["turn_min"] += r["turn_min"]

    for p, agg in by_proj.items():
        agg["active_min"] = len(proj_active_buckets[p]) * bucket_minutes

    out = list(by_proj.values())
    out.sort(key=lambda a: (a["active_min"], a["human_turns"]), reverse=True)
    return out


def _build_days(
    grid: dict[str, dict[int, list[int]]],
    records: dict[str, dict[str, Any]],
    bucket_minutes: int,
    n_buckets: int,
    labels: list[datetime],
    driven: list[int],
    human_by_bucket: list[int],
    agent_by_bucket: list[int],
) -> list[dict[str, Any]]:
    """One row per local calendar day in the window, interactive sessions only.

    The per-bucket vectors (driven / human_by_bucket / agent_by_bucket) are
    computed once in build_activity_timeline (headless already excluded) and
    passed in. `sessions_driven_by_hour` needs session identity, not just counts,
    so it unions interactive session ids across each hour's buckets — recovered
    from `grid` here.
    """
    # Group buckets by local date.
    buckets_by_date: dict[str, list[int]] = defaultdict(list)
    for b in range(n_buckets):
        buckets_by_date[labels[b].strftime("%a %m-%d")].append(b)

    # Per-bucket set of interactive session ids with >=1 human turn in that bucket.
    driven_sids_by_bucket: dict[int, set[str]] = defaultdict(set)
    for sid, cells in grid.items():
        if records[sid]["headless"]:
            continue
        for b, (h, _a) in cells.items():
            if h:
                driven_sids_by_bucket[b].add(sid)

    days_out: list[dict[str, Any]] = []
    for date_label in sorted(
        buckets_by_date, key=lambda dl: labels[buckets_by_date[dl][0]]
    ):
        bs = buckets_by_date[date_label]
        active_min = sum(1 for b in bs if driven[b]) * bucket_minutes
        multitask_min = sum(1 for b in bs if driven[b] >= 2) * bucket_minutes
        peak_v = max((driven[b] for b in bs), default=0)
        peak_b = next((b for b in bs if driven[b] == peak_v and peak_v), None)

        human_by_hour = [0] * 24
        agent_by_hour = [0] * 24
        # Distinct interactive sessions driven in each local hour (union of the
        # hour's per-bucket session sets), then counted — NOT a sum of per-bucket
        # counts, so a session active across several buckets of the hour counts once.
        driven_sids_by_hour: list[set[str]] = [set() for _ in range(24)]
        for b in bs:
            hour = labels[b].hour
            human_by_hour[hour] += human_by_bucket[b]
            agent_by_hour[hour] += agent_by_bucket[b]
            driven_sids_by_hour[hour] |= driven_sids_by_bucket.get(b, set())

        days_out.append(
            {
                "date": date_label,
                "active_min": active_min,
                "multitask_min": multitask_min,
                "peak": peak_v,
                "peak_at": (labels[peak_b].strftime("%H:%M") if peak_b is not None else None),
                "human_turns_by_hour": human_by_hour,
                "sessions_driven_by_hour": [len(s) for s in driven_sids_by_hour],
                "agent_turns_by_hour": agent_by_hour,
            }
        )
    return days_out


def _sorted_sessions(records: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Interactive first (turn_min desc), then headless (turn_min desc)."""
    inter = [r for r in records.values() if not r["headless"]]
    head = [r for r in records.values() if r["headless"]]
    inter.sort(key=lambda r: r["turn_min"], reverse=True)
    head.sort(key=lambda r: r["turn_min"], reverse=True)
    return inter + head
