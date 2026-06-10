"""Tests for build_activity_timeline — the activity-timeline core.

All fixtures are SYNTHETIC. This is a public repo: no real conversation text,
paths, or project names from the live corpus appear here.

The core walks projects via resolve_projects / load_conversations / collect_agent_files.
We monkeypatch those three seams (in the cc_explorer.activity namespace) so the
walk consumes synthetic JSONL files we write to tmp_path, exercising the real
parser, scan, bucketing, folding, and rollup math end to end.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import cc_explorer.activity as activity
from cc_explorer.activity import build_activity_timeline
from cc_explorer.parser import ConversationRef
from cc_explorer.subagents import AgentFile
from cc_explorer.utils import PrefixId

# Window: 2026-06-02 00:00 -> 2026-06-09 00:00 in America/Los_Angeles (UTC-7).
TZ = "America/Los_Angeles"
AFTER = datetime(2026, 6, 2)
BEFORE = datetime(2026, 6, 9)

# A reference UTC instant inside the window. 2026-06-03 17:00 PDT == 00:00 UTC next day.
# We work in UTC for the raw entries; the core converts to tz for labels.
def utc(y, mo, d, h, mi=0, s=0):
    return datetime(y, mo, d, h, mi, s, tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


SESSION_A = "aaaaaaaa-1111-2222-3333-444444444444"
SESSION_B = "bbbbbbbb-1111-2222-3333-444444444444"
SESSION_H = "cccccccc-1111-2222-3333-444444444444"  # headless
SUB_ID = "dddddddd-1111-2222-3333-444444444444"


# --------------------------------------------------------------------------- #
# JSONL builders (synthetic)
# --------------------------------------------------------------------------- #


def human(ts, text, session=SESSION_A, branch=None, entrypoint="cli", uuid="u",
          team=None, team_role=None):
    e = {
        "type": "user",
        "uuid": f"{uuid}-{ts}",
        "timestamp": ts,
        "sessionId": session,
        "entrypoint": entrypoint,
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }
    if branch:
        e["gitBranch"] = branch
    if team:
        e["teamName"] = team
    if team_role:
        e["agentName"] = team_role
    return e


def teammate_human(ts, session=SESSION_A, team="cef-integration", team_role="reviewer-3",
                   uuid="tm", body="do the thing"):
    """A teammate-injected user turn — string content opening <teammate-message.

    Mostly what a worker session's user-role turns are: an orchestrator/peer
    DMing the pane. NOT a human turn. Content is always a bare string.
    """
    return {
        "type": "user",
        "uuid": f"{uuid}-{ts}",
        "timestamp": ts,
        "sessionId": session,
        "entrypoint": "cli",
        "teamName": team,
        "agentName": team_role,
        "message": {
            "role": "user",
            "content": f'<teammate-message teammate_id="orch" color="blue">{body}</teammate-message>',
        },
    }


def interrupt_human(ts, session=SESSION_A):
    return human(ts, "[Request interrupted by user]", session=session, uuid="int")


def interrupt_toolresult(ts, session=SESSION_A):
    return {
        "type": "user",
        "uuid": f"intr-{ts}",
        "timestamp": ts,
        "sessionId": session,
        "entrypoint": "cli",
        "toolUseResult": "[Request interrupted by user for tool use]",
        "message": {
            "role": "user",
            "content": [
                {"type": "text", "text": "[Request interrupted by user for tool use]"}
            ],
        },
    }


def command_human(ts, name, args="", session=SESSION_A, uuid="cmd"):
    """A human turn that is a slash-command stanza (scaffolding, not a prompt).

    Empty args is pure noise (e.g. /clear); non-empty args carries real user
    words inside <command-args>.
    """
    body = (
        f"<command-name>/{name}</command-name> "
        f"<command-message>{name}</command-message> "
        f"<command-args>{args}</command-args>"
    )
    return human(ts, body, session=session, uuid=uuid)


def stdout_human(ts, text, session=SESSION_A, uuid="stdout"):
    """A human turn that is a <local-command-stdout> echo (scaffolding)."""
    return human(
        ts, f"<local-command-stdout>{text}</local-command-stdout>",
        session=session, uuid=uuid,
    )


def assistant(ts, request_id, session=SESSION_A, model="claude-test-1", entrypoint="cli",
              team=None, team_role=None):
    e = {
        "type": "assistant",
        "uuid": f"a-{request_id}",
        "timestamp": ts,
        "sessionId": session,
        "entrypoint": entrypoint,
        "requestId": request_id,
        "message": {
            "id": f"m-{request_id}",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": "ok"}],
        },
    }
    if team:
        e["teamName"] = team
    if team_role:
        e["agentName"] = team_role
    return e


def turn_duration(ts, ms, session=SESSION_A):
    return {
        "type": "system",
        "uuid": f"sys-{ts}",
        "timestamp": ts,
        "sessionId": session,
        "subtype": "turn_duration",
        "durationMs": ms,
        "messageCount": 3,
    }


def summary_entry(text):
    return {
        "type": "summary",
        "summary": text,
        "leafUuid": "a9529cc1-b576-5fd3-9f1a-1234567890ab",
    }


def write_jsonl(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


# --------------------------------------------------------------------------- #
# Walk-seam patching
# --------------------------------------------------------------------------- #


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """A controllable synthetic corpus.

    Returns a `register(project_name, session_id, entries, subagents=...)` helper
    and wires resolve_projects / load_conversations / collect_agent_files so the
    core walks exactly what we register.
    """
    projects: dict[str, dict[str, ConversationRef]] = {}
    agent_files: dict[Path, list[AgentFile]] = {}

    def register(project, session_id, entries, subagents=None):
        proj_dir = tmp_path / project
        path = proj_dir / f"{session_id}.jsonl"
        write_jsonl(path, entries)
        projects.setdefault(str(proj_dir), {})[PrefixId(session_id)] = ConversationRef(
            path=path, worktree=None
        )
        files = []
        for sub_id, sub_entries in (subagents or {}).items():
            sub_path = path.with_suffix("") / "subagents" / f"agent-{sub_id}.jsonl"
            write_jsonl(sub_path, sub_entries)
            files.append(AgentFile(agent_id=sub_id, path=sub_path))
        agent_files[path] = files

    def fake_resolve_projects(sel):
        return list(projects.keys())

    def fake_load_conversations(proj_path):
        return projects.get(proj_path, {})

    def fake_collect_agent_files(subdir: Path):
        # subdir is <session>/subagents; map back to the session file.
        session_file = subdir.parent.with_suffix(".jsonl")
        return agent_files.get(session_file, [])

    monkeypatch.setattr(activity, "resolve_projects", fake_resolve_projects)
    monkeypatch.setattr(activity, "load_conversations", fake_load_conversations)
    monkeypatch.setattr(activity, "collect_agent_files", fake_collect_agent_files)

    return register


def run(corpus_unused=None, **kwargs):
    kwargs.setdefault("after", AFTER)
    kwargs.setdefault("before", BEFORE)
    kwargs.setdefault("tz", TZ)
    return build_activity_timeline(**kwargs)


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #


class TestWindowAndBucketing:
    def test_window_edges_half_open(self, corpus):
        # before-edge entry (exactly 2026-06-09 00:00 PDT == 07:00 UTC) is EXCLUDED;
        # after-edge entry (2026-06-02 00:00 PDT == 07:00 UTC) is INCLUDED.
        corpus(
            "proj",
            SESSION_A,
            [
                human(utc(2026, 6, 2, 7, 0), "in: after edge"),       # 06-02 00:00 PDT, included
                assistant(utc(2026, 6, 2, 7, 1), "r1"),
                human(utc(2026, 6, 9, 7, 0), "out: before edge"),     # 06-09 00:00 PDT, excluded
            ],
        )
        out = run()
        assert out["summary"]["interactive"]["human_turns"] == 1
        assert out["window"]["days"] == 7

    def test_bucket_minutes_is_unit(self, corpus):
        # Two human turns 12 min apart => 2 distinct 5-min buckets => active_min=10.
        corpus(
            "proj",
            SESSION_A,
            [
                human(utc(2026, 6, 3, 18, 0), "a"),
                human(utc(2026, 6, 3, 18, 12), "b"),
            ],
        )
        out = run()
        assert out["summary"]["interactive"]["active_min"] == 10

    def test_tz_drives_hour_bucketing(self, corpus):
        # 2026-06-03 18:00 UTC == 11:00 PDT. Hour array index 11 must carry it.
        corpus("proj", SESSION_A, [human(utc(2026, 6, 3, 18, 0), "x")])
        out = run()
        day = next(d for d in out["days"] if d["human_turns_by_hour"][11] > 0)
        assert day["human_turns_by_hour"][11] == 1
        # And not in UTC hour 18.
        assert day["human_turns_by_hour"][18] == 0


class TestInterrupts:
    def test_interrupt_on_both_entry_types(self, corpus):
        corpus(
            "proj",
            SESSION_A,
            [
                human(utc(2026, 6, 3, 18, 0), "real prompt"),
                interrupt_human(utc(2026, 6, 3, 18, 1)),
                assistant(utc(2026, 6, 3, 18, 2), "r1"),
                interrupt_toolresult(utc(2026, 6, 3, 18, 3)),
            ],
        )
        out = run()
        sess = out["sessions"][0]
        # Two interrupts (one per entry type); only the one real prompt counts as human.
        assert sess["interrupts"] == 2
        assert sess["human_turns"] == 1

    def test_interrupts_not_counted_as_human(self, corpus):
        corpus(
            "proj",
            SESSION_A,
            [
                interrupt_human(utc(2026, 6, 3, 18, 0)),
                interrupt_toolresult(utc(2026, 6, 3, 18, 1)),
                assistant(utc(2026, 6, 3, 18, 2), "r1"),
            ],
        )
        out = run()
        assert out["summary"]["interactive"]["human_turns"] == 0
        assert out["sessions"][0]["interrupts"] == 2


class TestHeadlessSegregation:
    def test_headless_excluded_from_interactive_present_in_sessions_and_timeline(self, corpus):
        corpus(
            "proj",
            SESSION_A,
            [human(utc(2026, 6, 3, 18, 0), "interactive"), assistant(utc(2026, 6, 3, 18, 1), "r1")],
        )
        corpus(
            "proj",
            SESSION_H,
            [
                human(utc(2026, 6, 3, 18, 0), "headless prompt", session=SESSION_H, entrypoint="sdk-cli"),
                assistant(utc(2026, 6, 3, 18, 1), "rh", session=SESSION_H, entrypoint="sdk-cli"),
                turn_duration(utc(2026, 6, 3, 18, 2), 600000, session=SESSION_H),  # 10 min
            ],
        )
        out = run()
        s = out["summary"]
        # Interactive rollup sees only SESSION_A.
        assert s["interactive"]["sessions"] == 1
        assert s["interactive"]["human_turns"] == 1
        assert s["interactive"]["machine_hours"] == 0
        # Headless rollup sees SESSION_H.
        assert s["headless"]["sessions"] == 1
        assert s["headless"]["human_turns"] == 1
        assert round(s["headless"]["machine_hours"], 1) == round(10 / 60, 1)
        # Both present in sessions list.
        ids = {x["id"] for x in out["sessions"]}
        assert SESSION_A[:8] in ids and SESSION_H[:8] in ids
        # Headless is flagged and present in the timeline grid.
        headless_row = next(x for x in out["sessions"] if x["id"] == SESSION_H[:8])
        assert headless_row["headless"] is True
        in_timeline = any(SESSION_H[:8] in row for row in out["timeline"].values())
        assert in_timeline

    def test_headless_not_in_day_arrays(self, corpus):
        corpus(
            "proj",
            SESSION_H,
            [
                human(utc(2026, 6, 3, 18, 0), "h", session=SESSION_H, entrypoint="sdk-cli"),
                assistant(utc(2026, 6, 3, 18, 1), "rh", session=SESSION_H, entrypoint="sdk-cli"),
            ],
        )
        out = run()
        assert all(sum(d["human_turns_by_hour"]) == 0 for d in out["days"])
        assert out["summary"]["interactive"]["active_min"] == 0


class TestSubagentFolding:
    def test_sub_human_turns_become_parent_agent_work(self, corpus):
        # Parent has 1 human turn + 1 agent turn. Subagent has 3 internal human
        # turns in the same bucket — they fold in as PARENT agent work, not human.
        corpus(
            "proj",
            SESSION_A,
            [
                human(utc(2026, 6, 3, 18, 0), "drive the sub"),
                assistant(utc(2026, 6, 3, 18, 0), "rp"),
            ],
            subagents={
                SUB_ID: [
                    human(utc(2026, 6, 3, 18, 0), "sub work 1", session=SUB_ID),
                    human(utc(2026, 6, 3, 18, 0), "sub work 2", session=SUB_ID),
                    human(utc(2026, 6, 3, 18, 0), "sub work 3", session=SUB_ID),
                    turn_duration(utc(2026, 6, 3, 18, 0), 120000, session=SUB_ID),  # 2 min
                ]
            },
        )
        out = run()
        sess = out["sessions"][0]
        assert sess["human_turns"] == 1          # parent human only
        assert sess["n_sub"] == 1
        # parent agent (1) + ONE sub-human marker per bucket folded in == 2.
        # (The fold adds a single subh_<agent>_<bucket> marker per bucket, so N
        # sub-human turns in one bucket collapse to one agent-turn marker —
        # ported exactly from the prototype.)
        assert sess["agent_turns"] == 2
        assert sess["turn_min"] == 2             # folded subagent turn_min

    def test_subagent_with_no_window_activity_not_counted(self, corpus):
        corpus(
            "proj",
            SESSION_A,
            [human(utc(2026, 6, 3, 18, 0), "p"), assistant(utc(2026, 6, 3, 18, 0), "rp")],
            subagents={
                SUB_ID: [human(utc(2026, 1, 1, 0, 0), "out of window", session=SUB_ID)]
            },
        )
        out = run()
        assert out["sessions"][0]["n_sub"] == 0


class TestPerSessionMinutes:
    def test_human_active_and_agent_only_min(self, corpus):
        # Bucket A (18:00): human + agent. Bucket B (18:10): agent only.
        corpus(
            "proj",
            SESSION_A,
            [
                human(utc(2026, 6, 3, 18, 0), "p"),
                assistant(utc(2026, 6, 3, 18, 0), "r1"),
                assistant(utc(2026, 6, 3, 18, 10), "r2"),
            ],
        )
        out = run()
        sess = out["sessions"][0]
        assert sess["human_active_min"] == 5
        assert sess["agent_only_min"] == 5

    def test_amplification_null_when_no_human(self, corpus):
        corpus(
            "proj",
            SESSION_H,
            [assistant(utc(2026, 6, 3, 18, 0), "r1", session=SESSION_H, entrypoint="sdk-cli")],
        )
        out = run()
        assert out["sessions"][0]["amplification"] is None


class TestMultitaskAndPeaks:
    def test_multitask_and_peak(self, corpus):
        # Same bucket (18:00) driven by both A and B => multitask + peak 2.
        corpus("proj", SESSION_A, [human(utc(2026, 6, 3, 18, 0), "a", session=SESSION_A)])
        corpus("proj", SESSION_B, [human(utc(2026, 6, 3, 18, 0), "b", session=SESSION_B)])
        out = run()
        s = out["summary"]["interactive"]
        assert s["peak_sessions_driven"] == 2
        assert s["multitask_min"] == 5
        assert s["active_min"] == 5  # one bucket lit by >=1 session
        assert s["peak_at"] is not None

    def test_peak_autonomous(self, corpus):
        # Agent-only activity (no human) in a bucket => autonomous peak.
        corpus(
            "proj",
            SESSION_A,
            [assistant(utc(2026, 6, 3, 18, 0), "r1"), assistant(utc(2026, 6, 3, 18, 1), "r2")],
        )
        out = run()
        assert out["summary"]["interactive"]["peak_autonomous_sessions"] == 1


class TestDaysHourArrays:
    def test_hour_arrays_have_24_slots(self, corpus):
        corpus("proj", SESSION_A, [human(utc(2026, 6, 3, 18, 0), "x")])
        out = run()
        for d in out["days"]:
            assert len(d["human_turns_by_hour"]) == 24
            assert len(d["sessions_driven_by_hour"]) == 24
            assert len(d["agent_turns_by_hour"]) == 24


class TestDedupeByUuid:
    def test_same_uuid_across_projects_counted_once(self, corpus):
        entries = [human(utc(2026, 6, 3, 18, 0), "dup"), assistant(utc(2026, 6, 3, 18, 0), "r")]
        corpus("proj_one", SESSION_A, entries)
        corpus("proj_two", SESSION_A, entries)  # same UUID under a second project
        out = run()
        ids = [x["id"] for x in out["sessions"]]
        assert ids.count(SESSION_A[:8]) == 1
        assert out["summary"]["interactive"]["sessions"] == 1


class TestSessionFields:
    def test_branches_model_title_opening_summary(self, corpus):
        corpus(
            "myproj",
            SESSION_A,
            [
                summary_entry("a stored summary"),
                human(utc(2026, 6, 3, 18, 0), "  first   prompt  ", branch="feat/x"),
                assistant(utc(2026, 6, 3, 18, 0), "r1", model="claude-test-1"),
                assistant(utc(2026, 6, 3, 18, 5), "r2", model="claude-test-1"),
                assistant(utc(2026, 6, 3, 18, 6), "r3", model="claude-test-2"),
                human(utc(2026, 6, 3, 18, 10), "last prompt", branch="feat/x"),
            ],
        )
        out = run()
        sess = out["sessions"][0]
        assert sess["project"] == "myproj"
        assert sess["branches"] == ["feat/x"]
        assert sess["model"] == "claude-test-1"  # dominant (2 vs 1)
        assert sess["opening"] == "first prompt"  # whitespace-collapsed
        assert sess["closing"] == "last prompt"
        # title reuses session_title verbatim — it does NOT collapse internal
        # whitespace (only opening/closing do), so the doubled spaces survive.
        assert sess["title"] == "first   prompt"
        assert sess["summary"] == "a stored summary"
        assert sess["entrypoint"] == "cli"

    def test_sessions_sorted_interactive_then_headless_by_turn_min(self, corpus):
        corpus(
            "proj",
            SESSION_A,
            [
                human(utc(2026, 6, 3, 18, 0), "a"),
                turn_duration(utc(2026, 6, 3, 18, 0), 60000),  # 1 min
            ],
        )
        corpus(
            "proj",
            SESSION_B,
            [
                human(utc(2026, 6, 3, 18, 0), "b", session=SESSION_B),
                turn_duration(utc(2026, 6, 3, 18, 0), 600000, session=SESSION_B),  # 10 min
            ],
        )
        corpus(
            "proj",
            SESSION_H,
            [
                human(utc(2026, 6, 3, 18, 0), "h", session=SESSION_H, entrypoint="sdk-cli"),
                turn_duration(utc(2026, 6, 3, 18, 0), 6000000, session=SESSION_H),  # 100 min
            ],
        )
        out = run()
        ids = [x["id"] for x in out["sessions"]]
        # Interactive first (B with 10min before A with 1min), headless last despite 100min.
        assert ids == [SESSION_B[:8], SESSION_A[:8], SESSION_H[:8]]


class TestOpeningClosingSubstance:
    """opening/closing must surface real prompts, never command scaffolding."""

    def test_command_scaffolding_skipped_for_opening_and_closing(self, corpus):
        # First and last turns are bare /clear and a stdout echo (noise); the
        # real prompts are in the middle. opening/closing must land on those.
        corpus(
            "proj",
            SESSION_A,
            [
                command_human(utc(2026, 6, 3, 18, 0), "clear"),         # noise (first)
                human(utc(2026, 6, 3, 18, 5), "hey load up issue 17"),  # real opening
                assistant(utc(2026, 6, 3, 18, 5), "r1"),
                human(utc(2026, 6, 3, 18, 10), "now ship it"),          # real closing
                stdout_human(utc(2026, 6, 3, 18, 15), "build output noise"),  # noise (last)
            ],
        )
        sess = run()["sessions"][0]
        assert sess["opening"] == "hey load up issue 17"
        assert sess["closing"] == "now ship it"
        # Every scaffolding turn still counts as a human turn (the user acted).
        assert sess["human_turns"] == 4

    def test_command_args_recovered_as_text(self, corpus):
        # A session whose ONLY turns are commands: bare /clear (skipped) and a
        # /wrapup carrying real words in <command-args> (recovered, not skipped).
        corpus(
            "proj",
            SESSION_A,
            [
                command_human(utc(2026, 6, 3, 18, 0), "clear"),
                command_human(
                    utc(2026, 6, 3, 18, 5), "wrapup",
                    args="just fyi -- the build is green", uuid="wrap",
                ),
            ],
        )
        sess = run()["sessions"][0]
        assert sess["opening"] == "just fyi -- the build is green"
        assert sess["closing"] == "just fyi -- the build is green"

    def test_all_scaffolding_yields_null_opening(self, corpus):
        # Only noise turns, no recoverable args => no opening/closing text.
        corpus(
            "proj",
            SESSION_A,
            [
                command_human(utc(2026, 6, 3, 18, 0), "clear"),
                stdout_human(utc(2026, 6, 3, 18, 5), "some output"),
            ],
        )
        sess = run()["sessions"][0]
        assert sess["opening"] is None
        assert sess["closing"] is None
        assert sess["human_turns"] == 2  # still real human turns


class TestTeammateInjection:
    """Agent-team worker sessions: teammate-injected user turns are orchestration,
    not human attention. They reclassify as bucket agent activity, never as human
    turns / interrupts / opening-closing. team/team_role surface; team_sessions
    counts them. A genuine human-typed turn in the same session survives."""

    def test_teammate_turn_not_human_but_is_agent_activity(self, corpus):
        # A pure-worker session: 3 teammate DMs + 1 assistant reply, no human.
        corpus(
            "proj",
            SESSION_A,
            [
                teammate_human(utc(2026, 6, 3, 18, 0)),
                assistant(utc(2026, 6, 3, 18, 0), "r1", team="cef-integration",
                          team_role="reviewer-3"),
                teammate_human(utc(2026, 6, 3, 18, 1), uuid="tm2"),
                teammate_human(utc(2026, 6, 3, 18, 2), uuid="tm3"),
            ],
        )
        out = run()
        sess = out["sessions"][0]
        assert sess["human_turns"] == 0
        assert sess["interrupts"] == 0
        assert sess["opening"] is None
        assert sess["closing"] is None
        # Each teammate turn is one agent marker in its bucket; plus the assistant
        # request in bucket 0 (which dedups with the tm marker into 2 markers).
        assert sess["agent_turns"] >= 3
        # Not counted as interactive attention.
        assert out["summary"]["interactive"]["human_turns"] == 0
        assert out["summary"]["interactive"]["active_min"] == 0

    def test_teammate_turn_is_bucket_agent_activity(self, corpus):
        # One teammate turn alone lights its bucket as agent (autonomous) activity.
        corpus("proj", SESSION_A, [teammate_human(utc(2026, 6, 3, 18, 0))])
        out = run()
        sess = out["sessions"][0]
        assert sess["agent_turns"] == 1
        assert out["summary"]["interactive"]["peak_autonomous_sessions"] == 1

    def test_team_and_team_role_surface(self, corpus):
        corpus(
            "proj",
            SESSION_A,
            [
                teammate_human(utc(2026, 6, 3, 18, 0), team="alpha-team",
                               team_role="builder-1"),
                assistant(utc(2026, 6, 3, 18, 0), "r1", team="alpha-team",
                          team_role="builder-1"),
            ],
        )
        out = run()
        sess = out["sessions"][0]
        assert sess["team"] == "alpha-team"
        assert sess["team_role"] == "builder-1"
        assert out["summary"]["interactive"]["team_sessions"] == 1

    def test_non_team_session_has_null_team(self, corpus):
        corpus("proj", SESSION_A, [human(utc(2026, 6, 3, 18, 0), "normal prompt")])
        out = run()
        sess = out["sessions"][0]
        assert sess["team"] is None
        assert sess["team_role"] is None
        assert out["summary"]["interactive"]["team_sessions"] == 0

    def test_mixed_session_one_genuine_human_turn(self, corpus):
        # The real-world case: a worker session that is mostly teammate DMs but
        # has exactly ONE genuine human-typed pane touch (e.g. /code-review).
        corpus(
            "proj",
            SESSION_A,
            [
                teammate_human(utc(2026, 6, 3, 18, 0), uuid="tm1"),
                teammate_human(utc(2026, 6, 3, 18, 1), uuid="tm2"),
                human(utc(2026, 6, 3, 18, 5), "real human pane touch",
                      team="cef-integration", team_role="reviewer-3"),
                teammate_human(utc(2026, 6, 3, 18, 10), uuid="tm3"),
            ],
        )
        out = run()
        sess = out["sessions"][0]
        assert sess["human_turns"] == 1
        assert sess["opening"] == "real human pane touch"
        assert sess["closing"] == "real human pane touch"
        assert sess["team"] == "cef-integration"
        assert sess["team_role"] == "reviewer-3"
        assert out["summary"]["interactive"]["human_turns"] == 1
        assert out["summary"]["interactive"]["team_sessions"] == 1
