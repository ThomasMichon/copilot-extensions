"""``agent-logger metrics``: evidence-based duration and turn counts.

Validates :mod:`agent_logger.metrics` against synthetic ``events.jsonl`` logs:
the kickoff/cutoff window, ``ask_user`` answers matched by tool-call id,
sub-agent activity, resumed processes, permission prompts, multi-session
chains, archived sessions, malformed input, and the explicit ``unknown``
report for anything the logs cannot establish.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agent_logger import cold_store, metrics, sessions
from agent_logger.__main__ import main
from agent_logger.metrics import measure_sessions, parse_timestamp, to_event

pytestmark = pytest.mark.contract("agent_logger.metrics")

T0 = datetime(2026, 1, 5, 9, 0, tzinfo=timezone.utc)

SESSION_KEYS = [
    "kickoff",
    "kickoff_origin",
    "end",
    "end_basis",
    "wall_clock_seconds",
    "working_seconds",
    "user_turns",
    "human_prompts",
    "steering_prompts",
    "ask_user_answers",
    "automated_prompts",
    "agent_turns",
    "subagent_turns",
    "tool_calls",
    "subagent_tool_calls",
    "user_interrupts",
    "open_turn_at_end",
]


# --- synthetic events.jsonl records -------------------------------------------


def at(seconds: float) -> datetime:
    return T0 + timedelta(seconds=seconds)


def stamp(seconds: float) -> str:
    moment = at(seconds)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond // 1000:03d}Z"


def record(kind: str, seconds: float, **data) -> dict:
    return {
        "type": kind,
        "data": data,
        "id": f"{kind}-{seconds}",
        "parentId": None,
        "timestamp": stamp(seconds),
    }


def prompt(seconds, text="Do the task", source=None, steering=False):
    data = {"content": text, "delivery": "steering" if steering else "idle"}
    if source:
        data["source"] = source
    return record("user.message", seconds, **data)


def answer(seconds, kind="ask_user", call_id=None):
    extra = {"toolCallId": call_id} if call_id else {}
    return record("human_response.recorded", seconds, response={"responseKind": kind}, **extra)


def complete(seconds, call_id, success=True, **extra):
    return record("tool.execution_complete", seconds, toolCallId=call_id, success=success, **extra)


def as_agent(item, agent_id):
    """Mark records as a sub-agent's the way current logs do (top-level agentId)."""
    for entry in item if isinstance(item, list) else [item]:
        entry["agentId"] = agent_id
    return item


def turn(start, end=None, parent=None):
    """A model turn; older logs mark a sub-agent's turns with parentToolCallId."""
    extra = {"parentToolCallId": parent} if parent else {}
    records = [record("assistant.turn_start", start, turnId="0", **extra)]
    if end is not None:
        records.append(record("assistant.turn_end", end, turnId="0", **extra))
    return records


def tool(seconds, name="powershell", parent=None):
    extra = {"parentToolCallId": parent} if parent else {}
    return record(
        "tool.execution_start",
        seconds,
        toolName=name,
        toolCallId=f"call-{seconds}",
        arguments={},
        **extra,
    )


def abort(seconds, reason="user_initiated"):
    return record("abort", seconds, reason=reason)


def noise(seconds, kind="hook.start"):
    return record(kind, seconds, hookType="userPromptSubmitted")


def permission(seconds, request_id, decided=None):
    """A permission request and, when ``decided`` is given, its decision."""
    records = [
        record(
            "permission.requested",
            seconds,
            requestId=request_id,
            permissionRequest={"kind": "shell", "toolCallId": f"call-{seconds}"},
        )
    ]
    if decided is not None:
        records.append(
            record(
                "permission.completed",
                decided,
                requestId=request_id,
                toolCallId=f"call-{seconds}",
                result={"kind": "approved"},
            )
        )
    return records


def subagent_done(seconds, agent_id=None, call_id=None, kind="subagent.completed", **data):
    """A sub-agent's end; current logs name both its agentId and launching call."""
    if call_id:
        data["toolCallId"] = call_id
    entry = record(kind, seconds, **data)
    if agent_id:
        entry["agentId"] = agent_id
    return entry


def write(root: Path, session_id: str, *items, raw_lines=()) -> Path:
    folder = root / session_id
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for item in items:
        for entry in item if isinstance(item, list) else [item]:
            lines.append(json.dumps(entry, separators=(",", ":")))
    lines.extend(raw_lines)
    (folder / "events.jsonl").write_bytes(("\n".join(lines) + "\n").encode("utf-8"))
    return folder


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    return tmp_path / "session-state"


def measure(state_root: Path, *session_ids: str, until=None, stores=()):
    return measure_sessions(
        session_ids,
        until=until,
        resolve=lambda session_id: sessions.resolve_ref(session_id, state_root, *stores),
    )


# --- one session --------------------------------------------------------------


def test_single_session_duration_and_turns(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        noise(-50, "session.start"),
        prompt(0),
        noise(1),
        noise(59, "hook.end"),
        turn(60, 100),
        tool(61),
        turn(101, 150),
        tool(102, "edit"),
        turn(151, 401),
        tool(160, "ask_user"),
        answer(400),
        turn(402, 500),
        prompt(1000),
        turn(1030, 1100),
        prompt(1040, steering=True),
        turn(1101, 1200),
    )
    report = measure(state_root, "s1", until=at(1300))
    assert report["status"] == "measured"
    assert report["unknown_reason"] is None
    assert report["until"] == stamp(1300)
    aggregate = report["aggregate"]
    assert aggregate == {
        "kickoff": "2026-01-05T09:00:00.000Z",
        "kickoff_origin": "human",
        "end": stamp(1200),
        "end_basis": "last_event",
        "wall_clock_seconds": 1200,
        # Busy from each prompt to the end of its work, minus the 240s spent
        # waiting on the ask_user answer and the idle gaps between turns.
        "working_seconds": 456,
        "user_turns": 4,
        "human_prompts": 3,
        "steering_prompts": 1,
        "ask_user_answers": 1,
        "automated_prompts": 0,
        "agent_turns": 6,
        "subagent_turns": 0,
        "tool_calls": 3,
        "subagent_tool_calls": 0,
        "user_interrupts": 0,
        "open_turn_at_end": False,
        "sessions_in_window": 1,
    }
    row = report["sessions"][0]
    assert row["session_id"] == "s1"
    assert row["status"] == "ok"
    assert row["archived"] is False
    assert row["malformed_lines"] == 0
    assert row["unparseable_timestamps"] == 0
    assert row["metrics"] == {key: aggregate[key] for key in SESSION_KEYS}


def test_report_shape_is_stable(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10, 20))
    report = measure(state_root, "s1")
    assert list(report) == [
        "schema_version",
        "until",
        "status",
        "unknown_reason",
        "unknown_sessions",
        "aggregate",
        "sessions",
    ]
    assert report["schema_version"] == metrics.SCHEMA_VERSION == 1
    assert report["until"] is None
    assert list(report["aggregate"]) == [*SESSION_KEYS, "sessions_in_window"]
    row = report["sessions"][0]
    assert list(row) == [
        "session_id",
        "status",
        "archived",
        "malformed_lines",
        "unparseable_timestamps",
        "metrics",
    ]
    assert list(row["metrics"]) == SESSION_KEYS


def test_report_carries_no_prompt_text(state_root: Path) -> None:
    write(state_root, "s1", prompt(0, text="confidential task wording"), turn(10, 20))
    assert "confidential" not in json.dumps(measure(state_root, "s1"))


def test_durations_are_whole_seconds_rounded_down(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10, 20.9))
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["end"] == "2026-01-05T09:00:20.900Z"
    assert aggregate["wall_clock_seconds"] == 20
    assert aggregate["working_seconds"] == 20


# --- ask_user answers ---------------------------------------------------------


@pytest.mark.parametrize(
    ("resolution", "answers"),
    [
        pytest.param([complete(400, "call-160")], 1, id="completion"),
        pytest.param(
            [answer(400, call_id="call-160"), complete(401, "call-160")],
            1,
            id="answer-and-completion-count-once",
        ),
        pytest.param([complete(400, "call-160", success=False)], 0, id="failed-completion"),
        pytest.param(
            [
                tool(170),
                # Mentions the open call's id, but completes a different call.
                complete(200, "call-170", result={"content": "call-160"}),
                complete(400, "call-160"),
            ],
            1,
            id="other-tool-completion-ignored",
        ),
    ],
)
def test_ask_user_answers_match_tool_call_ids(state_root: Path, resolution, answers) -> None:
    write(state_root, "s1", prompt(0), turn(10, 500), tool(160, "ask_user"), *resolution)
    aggregate = measure(state_root, "s1", until=at(600))["aggregate"]
    assert aggregate["ask_user_answers"] == answers
    assert aggregate["user_turns"] == 1 + answers
    # 500s of work minus the 240s spent waiting on the question.
    assert aggregate["working_seconds"] == 260


def test_parallel_tool_call_does_not_end_the_wait(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1, 300),
        tool(10, "ask_user"),
        tool(11, "view"),
        answer(290),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["working_seconds"] == 20
    assert aggregate["tool_calls"] == 2


def test_unanswered_question_waits_until_the_last_event(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10), tool(20, "ask_user"), tool(30, "view"))
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["working_seconds"] == 20
    assert aggregate["ask_user_answers"] == 0
    assert aggregate["open_turn_at_end"] is True


def test_resolve_asks_counts_each_question_once() -> None:
    events = [
        to_event(tool(10, "ask_user")),
        to_event(tool(20, "ask_user")),
        to_event(answer(30)),  # no id (older logs): resolves the oldest open question
        to_event(answer(40, call_id="call-10")),  # already resolved
        to_event(complete(50, "call-20")),
        to_event(complete(60, "call-20")),  # already resolved
        to_event(complete(70, "call-99")),  # never asked
    ]
    kept = metrics._resolve_asks(events)
    resolved = [(event.ts, event.call_id) for event in kept if event.kind == "answer"]
    assert resolved == [(at(30), "call-10"), (at(50), "call-20")]


# --- sub-agents, steering, interrupts -----------------------------------------


def test_subagent_activity_is_counted_separately(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(5, 100),
        as_agent(prompt(6, text="sub-agent task"), "agent-x"),
        as_agent(turn(10, 50), "agent-x"),
        as_agent(tool(20), "agent-x"),
    )
    aggregate = measure(state_root, "s1", until=at(200))["aggregate"]
    assert aggregate["human_prompts"] == 1
    assert aggregate["automated_prompts"] == 0
    assert aggregate["user_turns"] == 1
    assert (aggregate["agent_turns"], aggregate["subagent_turns"]) == (1, 1)
    assert (aggregate["tool_calls"], aggregate["subagent_tool_calls"]) == (0, 1)
    assert aggregate["working_seconds"] == 100


def test_subagents_marked_by_parent_tool_call(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(5, 8),
        tool(6, "task"),
        turn(10, 30, parent="call-A"),
        turn(31, 50, parent="call-A"),
        tool(11, "grep", parent="call-A"),
        turn(12, 40, parent="call-B"),
        turn(55, 60),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert (aggregate["agent_turns"], aggregate["subagent_turns"]) == (2, 3)
    assert (aggregate["tool_calls"], aggregate["subagent_tool_calls"]) == (1, 1)
    # Main 0-8 and 55-60 plus concurrent sub-agent work 10-50, counted once.
    assert aggregate["working_seconds"] == 53


def test_steering_prompt_is_absorbed_by_the_open_turn(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(10, 20),
        prompt(15, steering=True),
        prompt(1000),
        turn(1010, 1020),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["steering_prompts"] == 1
    assert aggregate["human_prompts"] == 3
    assert aggregate["working_seconds"] == 40


def test_aborts_end_turns_and_count_user_interrupts(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(10),
        abort(20),
        prompt(500),
        turn(510, 520),
        prompt(700),
        abort(705, reason="system"),
        prompt(800),
        turn(810, 820),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["human_prompts"] == 4
    assert aggregate["agent_turns"] == 3
    assert aggregate["user_interrupts"] == 1
    assert aggregate["working_seconds"] == 60
    assert aggregate["open_turn_at_end"] is False


def test_open_turn_is_closed_at_the_last_event(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10), tool(40))
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["open_turn_at_end"] is True
    assert aggregate["end"] == stamp(40)
    assert aggregate["working_seconds"] == 40


@pytest.mark.parametrize(
    ("sub_turn", "done"),
    [
        pytest.param(
            as_agent(turn(3, parent="call-2"), "agent-x"),
            subagent_done(50, agent_id="agent-x", call_id="call-2"),
            id="agent-id",
        ),
        pytest.param(
            turn(3, parent="call-2"),
            subagent_done(50, agent_id="agent-x", call_id="call-2"),
            id="launching-call",
        ),
        pytest.param(
            turn(3, parent="call-2"),
            subagent_done(50, call_id="call-2", kind="subagent.failed"),
            id="failed-older-log",
        ),
    ],
)
def test_subagent_end_closes_its_open_turn(state_root: Path, sub_turn, done) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1, 5),
        tool(2, "task"),
        sub_turn,
        done,
        prompt(1000),
        turn(1001, 1010),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["subagent_turns"] == 1
    # Main 0-5 and 1000-1010 plus the sub-agent's 3-50, not 3-1010.
    assert aggregate["working_seconds"] == 60
    assert aggregate["open_turn_at_end"] is False


# --- resumed processes and waits on the user ----------------------------------


def test_resume_closes_what_the_previous_process_left_open(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1),
        tool(10),  # the process ends inside a turn ...
        noise(3600, "session.resume"),
        prompt(3610),
        turn(3611, 3620),
        prompt(3700),  # ... and before any turn takes this prompt up
        noise(7200, "session.resume"),
        prompt(7210),
        turn(7211, 7220),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    # Each process's own 10s; the hours the session lay dead are not work.
    assert aggregate["working_seconds"] == 30
    assert aggregate["wall_clock_seconds"] == 7220
    assert aggregate["human_prompts"] == 4
    assert aggregate["open_turn_at_end"] is False


def test_trailing_markers_do_not_move_the_end(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(10, 20),
        noise(500, "session.resume"),
        subagent_done(600, agent_id="agent-x", call_id="call-1"),
        permission(700, "req-1", decided=800),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["end"] == stamp(20)
    assert aggregate["wall_clock_seconds"] == 20
    assert aggregate["working_seconds"] == 20


def test_permission_waits_are_not_work(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1, 4000),
        tool(10),
        permission(11, "req-1", decided=3611),
        permission(12, "req-2", decided=12.5),
        tool(3620),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    # 4000s of work minus the hour the prompt waited on its decision.
    assert aggregate["working_seconds"] == 400
    assert aggregate["user_turns"] == 1
    assert aggregate["tool_calls"] == 2


def test_undecided_permission_waits_until_the_turn_ends(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1, 500),
        permission(11, "req-1"),
        record("permission.completed", 100, requestId="req-other"),
        prompt(1000),
        turn(1001, 1010),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["working_seconds"] == 21


def test_subagent_permission_wait_holds_up_the_main_turn(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1, 5000),
        tool(2, "task"),
        as_agent(turn(3, 4990), "agent-x"),
        as_agent(permission(10, "req-x", decided=3610), "agent-x"),
        as_agent(turn(100, 200), "agent-y"),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    # 0-10 and 3610-5000, plus agent-y's work while agent-x waited.
    assert aggregate["working_seconds"] == 1500


def test_subagent_question_holds_up_the_main_turn(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(1, 1000),
        tool(2, "task"),
        as_agent(turn(3, 990), "agent-x"),
        as_agent(tool(10, "ask_user"), "agent-x"),
        as_agent(answer(910, call_id="call-10"), "agent-x"),
    )
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["ask_user_answers"] == 1
    assert aggregate["user_turns"] == 2
    assert aggregate["working_seconds"] == 100


# --- chains and the --until cutoff --------------------------------------------


def test_chain_aggregates_sessions_in_order(state_root: Path) -> None:
    write(state_root, "a", prompt(0), turn(10, 20))
    write(state_root, "b", prompt(100, source="agent-1234"), turn(110, 150))
    report = measure(state_root, "a", "b")
    aggregate = report["aggregate"]
    assert aggregate["kickoff"] == stamp(0)
    assert aggregate["kickoff_origin"] == "human"
    assert aggregate["end"] == stamp(150)
    assert aggregate["wall_clock_seconds"] == 150
    assert aggregate["working_seconds"] == 70
    assert aggregate["sessions_in_window"] == 2
    assert (aggregate["human_prompts"], aggregate["automated_prompts"]) == (1, 1)
    assert aggregate["user_turns"] == 1
    assert aggregate["agent_turns"] == 2
    first, successor = (row["metrics"] for row in report["sessions"])
    assert (first["wall_clock_seconds"], first["working_seconds"]) == (20, 20)
    assert successor["kickoff"] == stamp(100)
    assert successor["kickoff_origin"] == "agent"
    assert (successor["wall_clock_seconds"], successor["working_seconds"]) == (50, 50)
    assert successor["user_turns"] == 0


def test_repeated_session_ids_are_measured_once(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10, 20))
    report = measure(state_root, "s1", "s1")
    assert [row["session_id"] for row in report["sessions"]] == ["s1"]
    assert report["aggregate"]["agent_turns"] == 1


def test_automated_kickoff_when_no_human_prompt(state_root: Path) -> None:
    write(state_root, "s1", prompt(0, source="schedule-1"), turn(5, 10))
    aggregate = measure(state_root, "s1")["aggregate"]
    assert aggregate["kickoff_origin"] == "schedule"
    assert aggregate["user_turns"] == 0
    assert aggregate["automated_prompts"] == 1


def test_until_cuts_off_later_events(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(10, 20),
        tool(15),
        prompt(100),
        turn(110, 150),
    )
    report = measure(state_root, "s1", until=at(50))
    aggregate = report["aggregate"]
    assert report["until"] == stamp(50)
    assert (aggregate["end"], aggregate["end_basis"]) == (stamp(50), "until")
    assert aggregate["wall_clock_seconds"] == 50
    assert aggregate["human_prompts"] == 1
    assert aggregate["agent_turns"] == 1
    assert aggregate["tool_calls"] == 1
    assert aggregate["working_seconds"] == 20
    assert report["sessions"][0]["metrics"]["end_basis"] == "until"


def test_until_clips_a_turn_in_progress(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10, 100))
    aggregate = measure(state_root, "s1", until=at(50))["aggregate"]
    assert aggregate["working_seconds"] == 50
    assert aggregate["open_turn_at_end"] is False


def test_until_is_normalized_to_utc(state_root: Path) -> None:
    write(state_root, "s1", prompt(0), turn(10, 100))
    expected = measure(state_root, "s1", until=at(50))
    naive = measure(state_root, "s1", until=at(50).replace(tzinfo=None))
    offset = measure(state_root, "s1", until=at(50).astimezone(timezone(timedelta(hours=2))))
    assert naive == expected
    assert offset == expected


def test_session_outside_the_window_contributes_nothing(state_root: Path) -> None:
    write(state_root, "a", prompt(0), turn(10, 20))
    write(state_root, "b", prompt(100), turn(110, 120))
    report = measure(state_root, "a", "b", until=at(50))
    assert report["status"] == "measured"
    aggregate = report["aggregate"]
    assert aggregate["sessions_in_window"] == 1
    assert (aggregate["end"], aggregate["end_basis"]) == (stamp(50), "until")
    assert aggregate["human_prompts"] == 1
    later = report["sessions"][1]["metrics"]
    assert later["kickoff"] is None
    assert later["end"] is None
    assert later["wall_clock_seconds"] is None
    assert later["working_seconds"] == 0
    assert later["human_prompts"] == 0


# --- unknown is never zero ----------------------------------------------------


def test_missing_session_makes_the_report_unknown(state_root: Path) -> None:
    write(state_root, "a", prompt(0), turn(10, 20))
    report = measure(state_root, "a", "gone")
    assert report["status"] == "unknown"
    assert report["unknown_reason"] == "session_unavailable"
    assert report["unknown_sessions"] == ["gone"]
    assert report["aggregate"] is None
    assert [row["status"] for row in report["sessions"]] == ["ok", "missing"]
    assert all(row["metrics"] is None for row in report["sessions"])
    gone = report["sessions"][1]
    assert gone["archived"] is None
    assert gone["malformed_lines"] is None
    assert gone["unparseable_timestamps"] is None


def test_unsafe_session_ids_are_never_resolved() -> None:
    requested = []

    def resolve(session_id):
        requested.append(session_id)

    report = measure_sessions(["../escape"], resolve=resolve)
    assert requested == []
    assert report["status"] == "unknown"
    assert report["sessions"][0]["status"] == "invalid_id"
    assert report["unknown_sessions"] == ["../escape"]


def test_unreadable_sessions_are_unknown(tmp_path: Path) -> None:
    empty = tmp_path / "s1"
    empty.mkdir()

    def resolve(session_id):
        if session_id == "s1":
            return sessions.SessionRef(id="s1", kind="live", path=empty)
        raise ValueError("ambiguous archives")

    report = measure_sessions(["s1", "s2"], resolve=resolve)
    assert report["status"] == "unknown"
    assert report["unknown_sessions"] == ["s1", "s2"]
    assert [row["status"] for row in report["sessions"]] == ["unreadable", "unreadable"]
    assert [row["archived"] for row in report["sessions"]] == [False, None]


def test_no_prompt_means_no_kickoff(state_root: Path) -> None:
    write(state_root, "s1", turn(10, 20), tool(15))
    report = measure(state_root, "s1")
    assert report["status"] == "unknown"
    assert report["unknown_reason"] == "no_kickoff"
    assert report["unknown_sessions"] == []
    assert report["aggregate"] is None
    assert report["sessions"][0]["status"] == "ok"
    assert report["sessions"][0]["metrics"] is None


def test_until_before_the_first_prompt_is_unknown(state_root: Path) -> None:
    write(state_root, "s1", prompt(100), turn(110, 120))
    report = measure(state_root, "s1", until=at(50))
    assert report["status"] == "unknown"
    assert report["unknown_reason"] == "no_kickoff"


def test_requires_a_session_id() -> None:
    with pytest.raises(ValueError, match="session id"):
        measure_sessions([])


# --- malformed input ----------------------------------------------------------


def test_malformed_lines_are_counted_not_fatal(state_root: Path) -> None:
    write(
        state_root,
        "s1",
        prompt(0),
        turn(10, 20),
        raw_lines=(
            "",
            "{not json",
            '{"type":"session.info","data":{"broken": ',  # unmeasured type: never parsed
            json.dumps(tool(15)),  # spaced separators still parse
            '{"type":"tool.execution_start","data":',
            "[1, 2]",
        ),
    )
    row = measure(state_root, "s1")["sessions"][0]
    assert row["status"] == "ok"
    assert row["malformed_lines"] == 3
    assert row["metrics"]["tool_calls"] == 1
    assert row["metrics"]["agent_turns"] == 1


def test_unparseable_timestamps_are_counted_and_skipped(state_root: Path) -> None:
    bad_offset = tool(15)
    bad_offset["timestamp"] = "2026-01-05T09:00:15+25:00"
    garbage = abort(30)
    garbage["timestamp"] = "not a time"
    missing = tool(16)
    del missing["timestamp"]
    unmeasured = noise(5)
    unmeasured["timestamp"] = "garbage"
    write(state_root, "s1", prompt(0), turn(10, 20), bad_offset, garbage, missing, unmeasured)
    row = measure(state_root, "s1")["sessions"][0]
    assert row["unparseable_timestamps"] == 3
    assert row["metrics"]["tool_calls"] == 0
    assert row["metrics"]["user_interrupts"] == 0
    assert row["metrics"]["working_seconds"] == 20


@pytest.mark.parametrize(
    "value",
    [
        "2026-10-13T21:56:38.289Z",
        "2026-10-13T23:56:38.289+02:00",
        "2026-10-13T16:26:38.289-0530",
        "2026-10-13 21:56:38.2890000Z",
        "2026-10-13T21:56:38.289",
        "2026-10-13t21:56:38.289z",
    ],
)
def test_parse_timestamp_normalizes_to_utc(value: str) -> None:
    expected = datetime(2026, 10, 13, 21, 56, 38, 289000, tzinfo=timezone.utc)
    assert parse_timestamp(value) == expected


def test_parse_timestamp_edge_values() -> None:
    assert parse_timestamp("2026-10-13T21:56z") == datetime(
        2026, 10, 13, 21, 56, tzinfo=timezone.utc
    )
    assert parse_timestamp("2026-10-13T23:59:00+23:59") == datetime(
        2026, 10, 13, 0, 0, tzinfo=timezone.utc
    )


@pytest.mark.parametrize(
    "value",
    [
        "yesterday",
        "2026-13-01T00:00:00Z",
        "2026-02-30T00:00:00Z",
        "2026-10-13T25:00:00Z",
        "",
        None,
        1234,
        "2026-10-13T21:56:38+24:00",
        "2026-10-13T21:56:38-24:00",
        "2026-10-13T21:56:38+12:60",
        "2026-10-13T21:56:38+2400",
        "0001-01-01T00:00:00+05:00",
        "9999-12-31T23:59:59-05:00",
    ],
)
def test_parse_timestamp_rejects_malformed_values(value) -> None:
    assert parse_timestamp(value) is None


def test_to_event_classifies_prompts_and_subagents() -> None:
    assert to_event(prompt(0)).origin == "human"
    assert to_event(prompt(0, source="agent-7")).origin == "agent"
    assert to_event(prompt(0, source="schedule-daily")).origin == "schedule"
    assert to_event(prompt(0, source="webhook")).origin == "other"
    assert to_event(prompt(0, steering=True)).steering is True
    assert to_event(as_agent(tool(5), "agent-x")).agent == "agent-x"
    assert to_event(tool(5, parent="call-1")).sub is True
    assert to_event(tool(5)).sub is False
    assert to_event({"type": "abort", "data": "oops", "timestamp": stamp(0)}).reason == ""


def test_to_event_ignores_unmeasured_records() -> None:
    assert to_event(noise(0)) is None
    assert to_event(as_agent(prompt(0), "agent-x")) is None  # a sub-agent's own prompt
    assert to_event(answer(0, kind="confirm")) is None
    assert to_event(record("tool.execution_complete", 0, success=True)) is None
    assert to_event(subagent_done(0)) is None  # names no sub-agent
    assert to_event(record("permission.requested", 0, permissionRequest={})) is None
    assert to_event({"type": "user.message", "timestamp": "garbage"}) is None
    assert to_event(["user.message"]) is None


def test_to_event_classifies_markers() -> None:
    assert to_event(noise(0, "session.start")).kind == "boundary"
    assert to_event(noise(0, "session.resume")).kind == "boundary"
    done = to_event(subagent_done(0, agent_id="agent-x", call_id="call-1"))
    assert (done.kind, done.agent, done.call_id) == ("agent_done", "agent-x", "call-1")
    failed = to_event(subagent_done(0, call_id="call-1", kind="subagent.failed"))
    assert (failed.kind, failed.agent) == ("agent_done", "call-1")
    request, decision = (to_event(entry) for entry in as_agent(permission(0, "req-1", 5), "a"))
    assert (request.kind, request.agent, request.call_id) == ("permission", "a", "req-1")
    assert (decision.kind, decision.call_id) == ("permission_done", "req-1")
    assert all(event.kind in metrics._MARKERS for event in (done, failed, request, decision))


def test_span_arithmetic() -> None:
    spans = [(at(5), at(10)), (at(0), at(3)), (at(3), at(4)), (at(8), at(12)), (at(20), at(20))]
    assert metrics._union(spans) == [(at(0), at(4)), (at(5), at(12))]
    base = [(at(0), at(10)), (at(20), at(30))]
    cut = [(at(2), at(4)), (at(8), at(22)), (at(25), at(40))]
    assert metrics._subtract(base, cut) == [(at(0), at(2)), (at(4), at(8)), (at(22), at(25))]


# --- archived sessions and the default resolver -------------------------------


@pytest.mark.parametrize("codec", ["targz", "zip"])
def test_archived_session_measures_like_a_live_one(
    state_root: Path, tmp_path: Path, codec: str
) -> None:
    folder = write(
        state_root,
        "s1",
        prompt(0),
        turn(10, 60),
        tool(20, "ask_user"),
        answer(30),
        tool(40),
    )
    store = tmp_path / "archived-sessions"
    live = measure(state_root, "s1", stores=(store,))
    sessions.archive_session(folder, store, codec=codec)
    sessions.force_rmtree(folder)
    archived = measure(state_root, "s1", stores=(store,))
    assert live["sessions"][0]["archived"] is False
    assert archived["sessions"][0]["archived"] is True
    assert archived["aggregate"] == live["aggregate"]
    assert archived["aggregate"]["ask_user_answers"] == 1


@pytest.fixture
def local_sessions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Point the default resolver at temporary live and archive roots only."""
    root = tmp_path / "copilot" / "session-state"
    root.mkdir(parents=True)
    store = tmp_path / "archived-sessions"

    class _Config:
        sync_path = tmp_path / "synced"

    monkeypatch.setattr(cold_store, "_local_state_root", lambda: root)
    monkeypatch.setattr(cold_store, "session_archive_stores", lambda: [store])
    monkeypatch.setattr(cold_store, "load_config", lambda **_kw: _Config())
    return root, store


def test_default_resolver_reads_live_and_archived_sessions(local_sessions) -> None:
    root, store = local_sessions
    write(root, "live", prompt(0), turn(10, 20))
    packed = write(root, "packed", prompt(100, source="agent-1"), turn(110, 130))
    sessions.archive_session(packed, store)
    sessions.force_rmtree(packed)

    report = measure_sessions(["live", "packed"])
    assert report["status"] == "measured"
    assert [row["archived"] for row in report["sessions"]] == [False, True]
    assert report["aggregate"]["working_seconds"] == 50
    assert report["aggregate"]["sessions_in_window"] == 2

    partial = measure_sessions(["live", "packed", "absent"])
    assert [row["status"] for row in partial["sessions"]] == ["ok", "ok", "missing"]
    assert partial["unknown_sessions"] == ["absent"]


# --- CLI ----------------------------------------------------------------------


def test_cli_prints_the_report_and_exits_zero(local_sessions, capsys) -> None:
    root, _store = local_sessions
    write(root, "s1", prompt(0), turn(10, 20))
    assert main(["metrics", "--json", "s1"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "measured"
    assert report["aggregate"]["working_seconds"] == 20


def test_cli_unknown_report_exits_one(local_sessions, capsys) -> None:
    root, _store = local_sessions
    write(root, "s1", prompt(0), turn(10, 20))
    assert main(["metrics", "--json", "s1", "missing-session"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "unknown"
    assert report["unknown_sessions"] == ["missing-session"]
    assert report["aggregate"] is None


def test_cli_until_accepts_an_offset(local_sessions, capsys) -> None:
    root, _store = local_sessions
    write(root, "s1", prompt(0), turn(10, 20), prompt(100), turn(110, 150))
    assert main(["metrics", "s1", "--until", "2026-01-05T10:00:50+01:00"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["until"] == stamp(50)
    assert report["aggregate"]["end_basis"] == "until"


def test_cli_rejects_an_invalid_until(local_sessions, capsys) -> None:
    assert main(["metrics", "s1", "--until", "soon"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "invalid --until timestamp" in captured.err


def test_cli_requires_a_session_id(capsys) -> None:
    with pytest.raises(SystemExit):
        main(["metrics", "--json"])
    capsys.readouterr()
