"""Tests for the steering seam -- card + steer inbox (queue + parser)."""

from __future__ import annotations

import pytest

from agent_dispatch import steering
from agent_dispatch.queue import Status, TaskError
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


def test_parse_rejects_empty_multichoice():
    with pytest.raises(steering.SteeringError):
        steering.parse_request_input("d:multichoice[*]")  # * only -> no real options


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
    assert task.status == Status.STARTED  # never a verdict/terminal transition


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


# -- coordinator HTTP routes -------------------------------------------------


@pytest.fixture
def api(tmp_path):
    from fastapi.testclient import TestClient

    from agent_dispatch.coordinator import create_app

    return TestClient(create_app(TaskQueue(tmp_path / "tasks.db")))


def _held_over_http(api, worker="w1"):
    tid = api.post("/tasks", json={"title": "review PR 42"}).json()["id"]
    api.post("/claim", json={"worker_id": worker})
    api.post(f"/tasks/{tid}/start", json={"worker_id": worker})
    return tid


def test_card_and_steer_roundtrip_over_http(api, monkeypatch):
    from agent_dispatch import bridge

    wake_calls = []
    monkeypatch.setattr(
        bridge,
        "resume_steered_owner",
        lambda owner, task_id, message=None: wake_calls.append(
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
    assert r.json()["steer_woken"] is True
    assert wake_calls == [("w1", tid, None)]

    r = api.post(f"/tasks/{tid}/steer/take", json={"worker_id": "w1"})
    assert r.status_code == 200
    took = r.json()
    assert took["task_id"] == tid
    assert took["steer"]["fields"] == {"decision": "post-approved"}
    assert took["steer"]["sender"] == "tmichon"

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
        bridge, "resume_steered_owner", lambda owner, task_id, message=None: False
    )
    tid = _held_over_http(api)

    r = api.post(
        f"/tasks/{tid}/steer",
        json={"fields": {"decision": "revise"}},
    )

    assert r.status_code == 200
    assert r.json()["steer_woken"] is False
    log = api.get(f"/tasks/{tid}/steer-log").json()
    assert log[0]["fields"] == {"decision": "revise"}
    assert log[0]["taken"] is False


def test_steer_without_owner_persists_without_wake(api, monkeypatch):
    from agent_dispatch import bridge

    def unexpected_wake(*_args, **_kwargs):
        raise AssertionError("ownerless task must not be sent to agent-bridge")

    monkeypatch.setattr(bridge, "resume_steered_owner", unexpected_wake)
    tid = api.post("/tasks", json={"title": "unclaimed"}).json()["id"]

    r = api.post(f"/tasks/{tid}/steer", json={"fields": {"answer": "later"}})

    assert r.status_code == 200
    assert r.json()["steer_woken"] is None
    log = api.get(f"/tasks/{tid}/steer-log").json()
    assert log[0]["fields"] == {"answer": "later"}
