"""Tests for the steering seam -- card + steer inbox (queue + parser)."""

from __future__ import annotations

import threading
import time

import pytest

from agent_dispatch import steering
from agent_dispatch.queue import Status, TaskError
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def _held(q, worker="w1"):
    """A started task owned by ``worker`` -- the precondition for a card."""
    t = q.create("review PR 42")
    q.claim_one(worker)
    q.start(t.id, worker)
    return t


# -- parse_request_input -----------------------------------------------------


def test_parse_empty_spec_is_no_form():
    assert steering.parse_request_input(None) == []
    assert steering.parse_request_input("") == []
    assert steering.parse_request_input("   ") == []


def test_parse_bare_name_defaults_to_text():
    assert steering.parse_request_input("feedback") == [
        {"name": "feedback", "type": "text"}
    ]


def test_parse_textarea_and_text():
    fields = steering.parse_request_input("summary:text,notes:textarea")
    assert fields == [
        {"name": "summary", "type": "text"},
        {"name": "notes", "type": "textarea"},
    ]


def test_parse_choice_with_internal_commas():
    fields = steering.parse_request_input(
        "feedback:textarea,decision:choice[revise,post-approved,hold-all]"
    )
    assert fields == [
        {"name": "feedback", "type": "textarea"},
        {
            "name": "decision",
            "type": "choice",
            "options": ["revise", "post-approved", "hold-all"],
        },
    ]


def test_parse_rejects_bad_name():
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input("1bad:text")


def test_parse_rejects_duplicate_field():
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input("a:text,a:textarea")


def test_parse_rejects_unknown_type():
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input("x:number")


def test_parse_rejects_empty_choice():
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input("d:choice[]")


def test_parse_multichoice_and_allow_other():
    fields = steering.parse_request_input(
        "tags:multichoice[perf,api,ux],severity:choice[low,high,*]"
    )
    assert fields == [
        {"name": "tags", "type": "multichoice", "options": ["perf", "api", "ux"]},
        {"name": "severity", "type": "choice", "options": ["low", "high"],
         "allow_other": True},
    ]


def test_parse_multichoice_with_other():
    [f] = steering.parse_request_input("tags:multichoice[a,b,*]")
    assert f["type"] == "multichoice"
    assert f["options"] == ["a", "b"]  # the * sentinel is stripped from options
    assert f["allow_other"] is True


def test_parse_choice_gated_followup():
    fields = steering.parse_request_input(
        "comments:choice[Accept,Reject],"
        "reason:textarea?comments=Reject,"
        "verdict:choice[Approve,Waiting for author,Reject]?comments=Accept"
    )
    assert fields == [
        {"name": "comments", "type": "choice", "options": ["Accept", "Reject"]},
        {
            "name": "reason",
            "type": "textarea",
            "show_when": {"field": "comments", "equals": "Reject"},
        },
        {
            "name": "verdict",
            "type": "choice",
            "options": ["Approve", "Waiting for author", "Reject"],
            "show_when": {"field": "comments", "equals": "Accept"},
        },
    ]


@pytest.mark.parametrize(
    "spec",
    [
        "reason:textarea?missing=Reject",
        "feedback:textarea,reason:textarea?feedback=Reject",
        "feedback:choice[Accept,Reject],reason:textarea?reason=Reject",
        "feedback:choice[Accept,Reject],reason:textarea?feedback",
        "feedback:choice[Accept,Reject],reason:textarea?feedback=Rejected",
        (
            "feedback:choice[Accept,Reject],"
            "middle:choice[Yes,No]?feedback=Accept,"
            "reason:textarea?middle=No"
        ),
    ],
)
def test_parse_rejects_bad_choice_gated_followup(spec):
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input(spec)


def test_parse_rejects_empty_multichoice():
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input("d:multichoice[*]")  # * only -> no real options


def test_parse_question_mark_inside_choice_option():
    assert steering.parse_request_input("decision:choice[Proceed,Needs another look?]") == [
        {
            "name": "decision",
            "type": "choice",
            "options": ["Proceed", "Needs another look?"],
        }
    ]


# -- build_card --------------------------------------------------------------


def test_build_card_omits_empty_and_clips_title():
    card = steering.build_card(title="  ", status="ok", body=None)
    assert card == {"status": "ok"}
    long = "x" * (steering.CARD_TITLE_MAX + 50)
    card = steering.build_card(title=long)
    assert len(card["title"]) == steering.CARD_TITLE_MAX


def test_validate_steer_rejects_bad_choice():
    form = steering.parse_request_input("decision:choice[a,b]")
    steering.validate_steer_fields({"decision": "a"}, form)  # ok
    steering.validate_steer_fields({"other": "z"}, form)  # unknown passes
    with pytest.raises(steering.SteeringError):
        steering.validate_steer_fields({"decision": "nope"}, form)


def test_validate_steer_allow_other_accepts_free_text():
    form = steering.parse_request_input("severity:choice[low,high,*]")
    # A value outside the options is a valid "Other…" answer when allow_other.
    steering.validate_steer_fields({"severity": "somewhere in between"}, form)


def test_validate_steer_multichoice_members():
    import json as _json

    form = steering.parse_request_input("tags:multichoice[perf,api]")
    steering.validate_steer_fields({"tags": _json.dumps(["perf", "api"])}, form)  # ok
    with pytest.raises(steering.SteeringError):
        steering.validate_steer_fields({"tags": _json.dumps(["perf", "nope"])}, form)


def test_validate_steer_multichoice_allow_other_free_member():
    import json as _json

    form = steering.parse_request_input("tags:multichoice[perf,api,*]")
    # A free-text member survives (JSON array preserves commas) and is accepted.
    steering.validate_steer_fields(
        {"tags": _json.dumps(["perf", "a custom, comma'd tag"])}, form)


def test_validate_steer_rejects_hidden_conditional_field():
    form = steering.parse_request_input(
        "comments:choice[Accept,Reject],reason:textarea?comments=Reject"
    )
    steering.validate_steer_fields(
        {"comments": "Reject", "reason": "Revise comment 2."}, form)
    with pytest.raises(steering.SteeringError):
        steering.validate_steer_fields(
            {"comments": "Accept", "reason": "This must not pass."}, form)


# -- queue: set_card ---------------------------------------------------------


def test_set_card_with_form_marks_awaiting_steer(q):
    t = _held(q)
    form = steering.parse_request_input("feedback:textarea")
    card = steering.build_card(title="Recommend Approve", status="4 comments", request_input=form)
    task = q.set_card(t.id, "w1", card=card, now=1234.0)
    assert task.awaiting_steer is True
    assert task.card["title"] == "Recommend Approve"
    assert task.card["ts"] == 1234.0
    assert task.card["request_input"] == form
    assert task.status == Status.SUSPENDED
    assert task.lease_expires_at is None


def test_set_card_without_form_is_not_awaiting(q):
    t = _held(q)
    card = steering.build_card(status="just an FYI")
    task = q.set_card(t.id, "w1", card=card)
    assert task.awaiting_steer is False
    assert task.card["status"] == "just an FYI"


def test_set_card_requires_ownership(q):
    t = _held(q, worker="w1")
    with pytest.raises(TaskError):
        q.set_card(t.id, "someone-else", card={"status": "x"})


def test_set_card_requires_held(q):
    t = q.create("queued task")
    with pytest.raises(TaskError):
        q.set_card(t.id, "w1", card={"status": "x"})


# -- queue: submit_steer + take_steer ---------------------------------------


def test_steer_roundtrip_clears_awaiting_and_delivers(q):
    t = _held(q)
    form = steering.parse_request_input("decision:choice[revise,post-approved]")
    q.set_card(t.id, "w1", card=steering.build_card(request_input=form))

    task = q.submit_steer(t.id, fields={"decision": "post-approved"}, sender="tmichon")
    assert task.awaiting_steer is False  # operator answered -> no longer blocked

    taken = q.take_steer(t.id, "w1")
    assert taken is not None
    assert taken["fields"] == {"decision": "post-approved"}
    assert taken["sender"] == "tmichon"

    # inbox is now drained
    assert q.take_steer(t.id, "w1") is None


def test_take_steer_is_owner_gated(q):
    t = _held(q, worker="w1")
    q.set_card(t.id, "w1", card=steering.build_card(request_input=[{"name": "f", "type": "text"}]))
    q.submit_steer(t.id, fields={"f": "hi"})
    with pytest.raises(TaskError):
        q.take_steer(t.id, "intruder")


def test_take_steer_fifo_order(q):
    t = _held(q)
    q.submit_steer(t.id, fields={"n": "1"})
    q.submit_steer(t.id, fields={"n": "2"})
    assert q.take_steer(t.id, "w1")["fields"] == {"n": "1"}
    assert q.take_steer(t.id, "w1")["fields"] == {"n": "2"}


def test_take_steer_all_drains_pending_batch(q):
    t = _held(q)
    q.submit_steer(t.id, fields={"n": "1"})
    q.submit_steer(t.id, fields={"n": "2"})

    steers = q.take_steer(t.id, "w1", all_pending=True)

    assert [steer["fields"] for steer in steers] == [{"n": "1"}, {"n": "2"}]
    assert q.take_steer(t.id, "w1", all_pending=True) == []


def test_suspend_rejects_steer_that_arrived_after_card(q):
    t = _held(q)
    q.set_card(
        t.id,
        "w1",
        card=steering.build_card(
            request_input=[{"name": "decision", "type": "text"}]
        ),
    )
    q.submit_steer(
        t.id,
        fields={"decision": "continue"},
        sender="operator",
        wake_requested=False,
    )

    with pytest.raises(TaskError, match="pending steer"):
        q.suspend(t.id, "w1", reason="waiting for guidance")

    assert q.get(t.id).status == Status.STARTED
    q.take_steer(t.id, "w1", all_pending=True)
    assert q.suspend(
        t.id, "w1", reason="waiting for more guidance"
    ).status == Status.SUSPENDED


def test_submit_steer_rejects_terminal_task(q):
    t = _held(q)
    q.complete(t.id, "w1")
    with pytest.raises(TaskError):
        q.submit_steer(t.id, fields={"x": "y"})


def test_steer_log_records_taken_flag(q):
    t = _held(q)
    q.submit_steer(t.id, fields={"a": "1"}, sender="op")
    q.take_steer(t.id, "w1")
    log = q.steer_log(t.id)
    assert len(log) == 1
    assert log[0]["taken"] is True
    assert log[0]["sender"] == "op"
    assert log[0]["fields"] == {"a": "1"}


def test_submit_steer_not_owner_gated(q):
    """The operator (not the worker) answers -- submit takes no worker identity."""
    t = _held(q, worker="w1")
    q.set_card(t.id, "w1", card=steering.build_card(request_input=[{"name": "f", "type": "text"}]))
    task = q.submit_steer(t.id, fields={"f": "value"}, sender="operator")
    assert task.awaiting_steer is False


def test_submit_steer_reembodies_suspended_headless_owner(q):
    t = q.create("review PR 42")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key,
        session_handle="fleet-body:worker-host:bridge-session-1",
    )
    q.claim_one("fleet-owner", task_id=t.id)
    q.start(t.id, "fleet-owner")
    q.suspend(t.id, "fleet-owner", reason="waiting for guidance")

    task = q.submit_steer(
        t.id,
        fields={"decision": "continue"},
        sender="operator",
        wake_requested=True,
    )
    q.submit_steer(
        t.id,
        fields={"detail": "use option B"},
        sender="operator",
        wake_requested=True,
    )

    assert task.status == Status.SUSPENDED
    assert task.owner == "fleet-owner"
    assert task.resume_requested is True
    assert q.get_reservation(reservation.key).state == "spawned"
    assert q.list_wakes(t.id) == []
    q.release_suspended(t.id, "fleet-owner", reason="body stopped")
    q.claim_one("replacement", task_id=t.id)
    q.start(t.id, "replacement")
    steers = q.take_steer(t.id, "replacement", all_pending=True)
    assert [steer["fields"] for steer in steers] == [
        {"decision": "continue"},
        {"detail": "use option B"},
    ]


def test_submit_steer_reembodies_blocked_headless_without_wake_flag(q):
    t = q.create("review PR 42")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:bridge-session-1",
    )
    q.claim_one("headless-owner", task_id=t.id)
    q.start(t.id, "headless-owner")
    q.set_card(
        t.id,
        "headless-owner",
        card=steering.build_card(
            request_input=[{"name": "decision", "type": "text"}]
        ),
    )

    task = q.submit_steer(
        t.id,
        fields={"decision": "continue"},
        sender="operator",
        wake_requested=False,
    )

    assert task.status == Status.SUSPENDED
    assert task.owner == "headless-owner"
    assert task.resume_requested is True
    assert q.get_reservation(reservation.key).state == "spawned"


# -- coordinator HTTP routes -------------------------------------------------


@pytest.fixture
def api(tmp_path):
    from fastapi.testclient import TestClient

    from agent_dispatch.coordinator import create_app

    with TestClient(
        create_app(
            TaskQueue(tmp_path / "tasks.db"),
            wake_interval=0.01,
            wake_max_attempts=1,
            wake_retry_base=0.01,
        )
    ) as client:
        yield client


def _held_over_http(api, worker="w1"):
    tid = api.post("/tasks", json={"title": "review PR 42"}).json()["id"]
    api.post("/claim", json={"worker_id": worker, "repo": TEST_REPO})
    api.post(
        f"/tasks/{tid}/start",
        json={"worker_id": worker, "owner_session_id": f"session-{worker}"},
    )
    return tid


def _wait_for_wake_status(api, task_id, expected, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        notes = [event["note"] for event in api.get(
            f"/tasks/{task_id}/events"
        ).json()]
        if any(
            note and note.startswith(f"wake {expected}") for note in notes
        ):
            return
        time.sleep(0.01)
    raise AssertionError(f"wake status {expected!r} was not recorded")


def test_card_and_steer_roundtrip_over_http(api, monkeypatch):
    from agent_dispatch import bridge

    wake_calls = []
    monkeypatch.setattr(
        bridge,
        "resume_steered_owner",
        lambda owner, task_id, message=None, **_kwargs: wake_calls.append(
            (owner, task_id, message)
        ) or True,
    )
    tid = _held_over_http(api)
    card = {
        "title": "Recommend Approve",
        "status": "4 comments (2 nits)",
        "link": "https://onedrive/x",
        "request_input": [
            {"name": "feedback", "type": "textarea"},
            {"name": "decision", "type": "choice", "options": ["revise", "post-approved"]},
        ],
    }
    r = api.post(f"/tasks/{tid}/card", json={"worker_id": "w1", "card": card})
    assert r.status_code == 200
    body = r.json()
    assert body["awaiting_steer"] is True
    assert body["card"]["title"] == "Recommend Approve"

    r = api.post(
        f"/tasks/{tid}/steer",
        json={"fields": {"decision": "post-approved"}, "sender": "tmichon"},
    )
    assert r.status_code == 200
    assert r.json()["awaiting_steer"] is False
    assert r.json()["steer_woken"] is None
    assert r.json()["steer_wake_status"] == "pending"
    _wait_for_wake_status(api, tid, "delivered")
    assert len(wake_calls) == 1
    assert wake_calls[0][:2] == ("w1", tid)
    assert f"steer take {tid}" in wake_calls[0][2]
    [wake] = api.get(f"/tasks/{tid}/wakes").json()
    assert wake["status"] == "delivered"
    assert wake["attempts"] == 1
    assert api.get("/health").json()["wakes"]["delivered"] >= 1

    r = api.post(
        f"/tasks/{tid}/steer/take",
        json={"worker_id": "w1", "all_pending": True},
    )
    assert r.status_code == 200
    took = r.json()
    assert took["task_id"] == tid
    assert took["steers"][0]["fields"] == {"decision": "post-approved"}
    assert took["steers"][0]["sender"] == "tmichon"

    # inbox drained
    assert api.post(f"/tasks/{tid}/steer/take", json={"worker_id": "w1"}).json()["steer"] is None

    log = api.get(f"/tasks/{tid}/steer-log").json()
    assert len(log) == 1 and log[0]["taken"] is True


def test_card_on_missing_task_is_404(api):
    r = api.post("/tasks/nope/card", json={"worker_id": "w1", "card": {"status": "x"}})
    assert r.status_code == 404


def test_card_on_unheld_task_is_409(api):
    tid = api.post("/tasks", json={"title": "x"}).json()["id"]
    r = api.post(f"/tasks/{tid}/card", json={"worker_id": "w1", "card": {"status": "x"}})
    assert r.status_code == 409


def test_steer_take_wrong_owner_is_409(api):
    tid = _held_over_http(api, worker="w1")
    api.post(
        f"/tasks/{tid}/steer",
        json={"fields": {"a": "1"}, "wake": False},
    )
    r = api.post(f"/tasks/{tid}/steer/take", json={"worker_id": "intruder"})
    assert r.status_code == 409


def test_steer_log_missing_task_is_404(api):
    assert api.get("/tasks/nope/steer-log").status_code == 404


def test_steer_wake_failure_keeps_answer_durable(api, monkeypatch):
    from agent_dispatch import bridge

    monkeypatch.setattr(
        bridge,
        "resume_steered_owner",
        lambda owner, task_id, message=None, **_kwargs: False,
    )
    tid = _held_over_http(api)

    r = api.post(
        f"/tasks/{tid}/steer",
        json={"fields": {"decision": "revise"}},
    )

    assert r.status_code == 200
    assert r.json()["steer_woken"] is None
    assert r.json()["steer_wake_status"] == "pending"
    _wait_for_wake_status(api, tid, "failed")
    log = api.get(f"/tasks/{tid}/steer-log").json()
    assert log[0]["fields"] == {"decision": "revise"}
    assert log[0]["taken"] is False


def test_steer_atomically_resumes_suspended_task_before_wake(api, monkeypatch):
    from agent_dispatch import bridge

    wake_called = threading.Event()

    def wake(owner, task_id, message=None, **_kwargs):
        wake_called.set()
        return False

    monkeypatch.setattr(bridge, "resume_steered_owner", wake)
    tid = _held_over_http(api)
    api.post(
        f"/tasks/{tid}/suspend",
        json={"worker_id": "w1", "reason": "waiting for guidance"},
    )

    response = api.post(
        f"/tasks/{tid}/steer",
        json={"fields": {"decision": "continue"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == Status.STARTED
    assert response.json()["owner"] == "w1"
    assert response.json()["steer_woken"] is None
    assert response.json()["steer_wake_status"] == "pending"
    assert wake_called.wait(1)
    _wait_for_wake_status(api, tid, "failed")
    assert api.get(f"/tasks/{tid}/steer-log").json()[0]["taken"] is False
    assert any(
        event["from_status"] == Status.SUSPENDED
        and event["to_status"] == Status.STARTED
        for event in api.get(f"/tasks/{tid}/events").json()
    )


def test_steer_without_owner_persists_without_wake(api, monkeypatch):
    from agent_dispatch import bridge

    def unexpected_wake(*_args, **_kwargs):
        raise AssertionError("ownerless task must not be sent to agent-bridge")

    monkeypatch.setattr(bridge, "resume_steered_owner", unexpected_wake)
    tid = api.post("/tasks", json={"title": "unclaimed"}).json()["id"]

    r = api.post(f"/tasks/{tid}/steer", json={"fields": {"answer": "later"}})

    assert r.status_code == 200
    assert r.json()["steer_woken"] is None
    assert r.json()["steer_wake_status"] == "no_owner"
    log = api.get(f"/tasks/{tid}/steer-log").json()
    assert log[0]["fields"] == {"answer": "later"}


def test_slow_wake_never_blocks_durable_steer_response(api, monkeypatch):
    from agent_dispatch import bridge

    entered = threading.Event()
    release = threading.Event()

    def slow_wake(owner, task_id, message=None, **_kwargs):
        entered.set()
        assert release.wait(2)
        return False

    monkeypatch.setattr(bridge, "resume_steered_owner", slow_wake)
    tid = _held_over_http(api)

    started = time.monotonic()
    response = api.post(
        f"/tasks/{tid}/steer",
        json={"fields": {"decision": "continue"}},
    )
    elapsed = time.monotonic() - started

    assert response.status_code == 200
    assert elapsed < 1.0
    assert response.json()["steer_wake_status"] == "pending"
    assert entered.wait(1)
    log = api.get(f"/tasks/{tid}/steer-log").json()
    assert log[0]["fields"] == {"decision": "continue"}
    assert log[0]["taken"] is False
    release.set()
    _wait_for_wake_status(api, tid, "failed")
