"""Actual ASGI/installation/DB adapter controls, not SDK or native qualification."""

from __future__ import annotations

from dataclasses import replace
import json
import os
import time
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient
import pytest

from agent_bridge import live_inbox_runtime
from agent_bridge.app_routes import configure_serving_app
from agent_bridge.db import Database
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.models import ServiceConfig
from agent_bridge.runtime_custody import RuntimeCustodyError, encode
from test_loop_governance_custody import installation as installation

CONTRACT = "retained-inbox-v1"
PREFIX = "/api/v1/live-sessions/native-a/retained-inbox"


@pytest.fixture
def serving(installation: dict[str, Any], tmp_db: Database):
    app = configure_serving_app(
        ServiceConfig(db_path=str(tmp_db.db_path), bind="127.0.0.1"),
        "synthetic-secret", lifespan=None,
    )
    app.state.db = tmp_db
    app.state.ready = True
    app.state.loop_governance = LoopGovernance()
    app.state.session_manager = SimpleNamespace(is_draining=False)
    scheme = "Bear" + "er "
    headers = {"Authorization": scheme + "synthetic-secret"}
    assert headers["Authorization"].endswith("synthetic-secret")
    client = TestClient(app, headers=headers, client=("127.0.0.1", 12345))
    consumer = InboxConsumer("native-a", "consumer-a", 102, 1.0)
    response = client.post("/api/v1/live-sessions", json={
        "session_id": consumer.session_id, "machine": "machine-a",
        "pid": consumer.pid, "process_started_at": consumer.process_started_at,
    })
    assert response.status_code == 200
    yield app, client, tmp_db, consumer
    client.close()


def request(consumer: InboxConsumer) -> dict[str, object]:
    return {"contract": CONTRACT, "consumer": consumer.document()}


def enroll(client: TestClient, consumer: InboxConsumer) -> dict[str, Any]:
    observation = client.post(PREFIX + "/negotiate", json=request(consumer))
    assert observation.status_code == 200, observation.text
    body = {**request(consumer), **observation.json()}
    response = client.post(PREFIX + "/enroll", json=body)
    assert response.status_code == 200, response.text
    return body


def current(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "expected_incarnation"}


def message(app, db: Database, consumer: InboxConsumer, **kwargs) -> int:
    admission = app.state.live_inbox_runtime._admission
    assert admission is not None
    return db.enqueue_live_message(
        consumer.session_id, "sender", "original body", time.time(),
        runtime_admission=admission, **kwargs,
    )


def poll(client: TestClient, body: dict[str, Any]) -> dict[str, Any]:
    response = client.post(PREFIX + "/poll", json=current(body))
    assert response.status_code == 200, response.text
    row, = response.json()["messages"]
    return row


def attempt(body: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {**current(body), "message_id": row["id"], "handoff_id": row["handoff_id"]}


def report(body: dict[str, Any], row: dict[str, Any], outcome: str) -> dict[str, Any]:
    payload = attempt(body, row)
    payload.pop("binding")
    return {**payload, "outcome": outcome}


def test_explicit_negotiation_only_enrolls_actual_frontend_and_cas_consumer(serving) -> None:
    app, client, db, consumer = serving
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []
    body = enroll(client, consumer)
    assert body["expected_incarnation"] is None
    installation = app.state.loop_governance.runtime_installation(db)
    assert body["binding"]["installation"] == installation.document()
    producer, = db.execute_read("SELECT * FROM bridge_runtime_producers")
    observed = json.loads(producer["producer_json"])
    assert observed == {
        "pid": os.getpid(), "process_start": live_inbox_runtime.process_start_time(os.getpid()),
        "incarnation": app.state.live_inbox_runtime._producer.incarnation, "role": "frontend",
    }
    before = db.runtime_epoch(installation)
    assert client.post(PREFIX + "/enroll", json=body).status_code == 200
    response = client.post(PREFIX + "/negotiate", json=request(consumer))
    assert response.json()["expected_incarnation"] == consumer.incarnation
    assert db.runtime_epoch(installation) == before
    # A new observation is not an exact retry of the earlier enrollment.
    changed_retry = {**body, "expected_incarnation": consumer.incarnation}
    assert client.post(PREFIX + "/enroll", json=changed_retry).status_code == 409


def test_poll_reply_loss_preserves_original_and_final_admission_is_one_shot(serving) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    mid = message(app, db, consumer)
    first = poll(client, body)
    assert first["id"] == mid and first["body"] == "original body"
    assert first["handoff_stage"] == "offered"
    assert poll(client, body) == first
    assert client.post(PREFIX + "/begin", json=attempt(body, first)).json() == {"begin": True}
    # Includes a lost *true* reply: the retry can never authorize another SDK call.
    assert client.post(PREFIX + "/begin", json=attempt(body, first)).json() == {"begin": False}
    assert poll(client, body)["handoff_stage"] == "sending"
    payload = report(body, first, "unknown")
    assert client.post(PREFIX + "/outcome", json=payload).json() == {"stage": "unknown"}
    assert client.post(PREFIX + "/outcome", json=payload).json() == {"stage": "unknown"}
    assert client.post(PREFIX + "/begin", json=attempt(body, first)).json() == {"begin": False}
    retained, = db.execute_read("SELECT * FROM live_messages")
    assert retained["body"] == "original body" and retained["consumed_at"] is None


def test_distinct_outcomes_survive_closure_retirement_cleanup_and_lost_reply(serving) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
    owner = app.state.loop_governance.runtime_installation(db)
    runtime = app.state.live_inbox_runtime
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="transition-a",
        target_assembly="retained-a", predecessor=runtime._producer,
    )
    db.deregister_live_session(consumer.session_id)
    app.state.ready = False
    app.state.session_manager.is_draining = True
    app.state.loop_governance = None
    accepted = report(body, row, "sdk_accepted")
    assert client.post(PREFIX + "/outcome", json=accepted).json() == {"stage": "sdk_accepted"}
    retained, = db.execute_read("SELECT * FROM live_messages")
    assert retained["delivered_at"] is not None and retained["consumed_at"] is None
    consumed = report(body, row, "consumed")
    assert client.post(PREFIX + "/outcome", json=consumed).json() == {"stage": "consumed"}
    snapshot = dict(db.execute_read("SELECT * FROM live_messages")[0])
    assert client.post(PREFIX + "/outcome", json=consumed).json() == {"stage": "consumed"}
    assert client.post(PREFIX + "/outcome", json=report(body, row, "unknown")).json() == {
        "stage": "consumed",
    }
    assert dict(db.execute_read("SELECT * FROM live_messages")[0]) == snapshot
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 503


def test_reload_cas_never_rewrites_original_handoff_or_inherits_final_permission(serving) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    successor = replace(consumer, incarnation="consumer-b")
    replacement = enroll(client, successor)
    assert replacement["expected_incarnation"] == consumer.incarnation
    assert client.post(PREFIX + "/enroll", json=body).status_code == 409
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 409
    assert client.post(PREFIX + "/poll", json=current(replacement)).status_code == 409
    assert client.post(PREFIX + "/begin", json=attempt(replacement, row)).status_code == 409
    retained, = db.execute_read("SELECT * FROM live_messages")
    assert retained["handoff_started_at"] is None and retained["body"] == "original body"
    assert json.loads(retained["handoff_json"])["consumer"] == consumer.document()


@pytest.mark.parametrize("change", ["installation", "epoch", "session", "pid", "birth", "incarnation"])
def test_wrong_observations_refuse_before_body_or_final_admission(serving, change: str) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    if change in ("installation", "epoch"):
        body["binding"][change] = "foreign" if change == "installation" else 2
    else:
        key = {"session": "session_id", "birth": "process_started_at"}.get(change, change)
        body["consumer"][key] = 103 if key == "pid" else 2.0 if change == "birth" else "foreign"
    assert client.post(PREFIX + "/poll", json=current(body)).status_code == 409
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 409
    assert db.execute_read("SELECT handoff_started_at FROM live_messages")[0][0] is None


@pytest.mark.parametrize("exposure", ["remote", "proxy", "forwarded-prefix", "public-bind", "no-auth"])
def test_unsupported_ingress_never_initializes_or_exposes_inbox(serving, exposure: str) -> None:
    app, client, db, consumer = serving
    if exposure == "public-bind":
        app.state.config.bind = "0.0.0.0"
    elif exposure == "remote":
        headers = dict(client.headers)
        client.close()
        client = TestClient(
            app, headers=headers, client=("192.0.2.1", 12345),
        )
    elif exposure == "no-auth":
        client.headers.pop("authorization")
    else:
        client.headers["Forwarded" if exposure == "proxy" else "X-Forwarded-For"] = "127.0.0.1"
    try:
        response = client.post(PREFIX + "/negotiate", json=request(consumer))
        assert response.status_code == (401 if exposure == "no-auth" else 409)
        assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
        assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []
    finally:
        if exposure == "remote":
            client.close()


@pytest.mark.parametrize("problem", ["missing", "expired", "remote-venue", "different-pid"])
def test_unqualified_registry_cannot_enroll_consumer(serving, problem: str) -> None:
    app, client, db, consumer = serving
    if problem == "missing":
        db.deregister_live_session(consumer.session_id)
    elif problem == "expired":
        db.execute_write("UPDATE live_sessions SET updated_at=?", (time.time() - 1000,))
    elif problem == "remote-venue":
        db.execute_write("UPDATE live_sessions SET venue='{}'")
    else:
        consumer = replace(consumer, pid=103)
    assert client.post(PREFIX + "/negotiate", json=request(consumer)).status_code == 409
    assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []


@pytest.mark.parametrize("change", ["process", "database", "governance", "draining", "readonly"])
def test_current_owner_change_refuses_fresh_effects(serving, monkeypatch, change: str) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    other = None
    if change == "process":
        monkeypatch.setattr(live_inbox_runtime, "process_start_time", lambda _: "different")
    elif change in ("database", "readonly"):
        other = Database(db.db_path, read_only=change == "readonly")
        app.state.db = other
    elif change == "governance":
        monkeypatch.setattr(
            app.state.loop_governance, "runtime_installation",
            lambda _: (_ for _ in ()).throw(RuntimeCustodyError("installation changed")),
        )
    else:
        app.state.session_manager.is_draining = True
    try:
        response = client.post(PREFIX + "/begin", json=attempt(body, row))
        assert response.status_code == (503 if change in ("draining", "readonly") else 409)
        assert db.execute_read("SELECT handoff_started_at FROM live_messages")[0][0] is None
    finally:
        if other is not None:
            other.close()


@pytest.mark.parametrize("message_kind", ["legacy", "interrupt"])
def test_unsupported_message_rolls_back_entire_poll_without_partial_handoffs(
    serving, message_kind: str,
) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    if message_kind == "legacy":
        db.enqueue_live_message(consumer.session_id, "sender", "legacy", time.time())
    else:
        message(app, db, consumer, delivery="interrupt")
    assert client.post(PREFIX + "/poll", json=current(body)).status_code == 409
    assert all(row[0] is None for row in db.execute_read("SELECT handoff_json FROM live_messages"))


def test_historical_interrupt_cannot_gain_final_admission(serving) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    mid = message(app, db, consumer, delivery="interrupt")
    admission = app.state.live_inbox_runtime._admission
    raw = encode({
        "version": 1, "id": "historical-attempt", "message_id": mid,
        "consumer": consumer.document(), "admission": admission.document(),
    })
    db.execute_write("UPDATE live_messages SET handoff_json=?", (raw,))
    response = client.post(PREFIX + "/begin", json={
        **current(body), "message_id": mid, "handoff_id": "historical-attempt",
    })
    assert response.status_code == 409
    assert db.execute_read("SELECT handoff_started_at FROM live_messages")[0][0] is None


def test_reopened_database_retains_lost_final_reply_and_original_outcome_identity(serving) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    reopened = Database(db.db_path)
    app.state.db = reopened
    app.state.live_inbox_runtime = live_inbox_runtime.LiveInboxRuntime()
    app.state.loop_governance = LoopGovernance()
    try:
        # The same consumer can recover enrollment under a new frontend without
        # treating a newer predecessor observation as an exact lost-reply retry.
        observation = client.post(PREFIX + "/negotiate", json=request(consumer))
        assert observation.status_code == 200
        assert observation.json()["binding"] == body["binding"]
        assert client.post(PREFIX + "/enroll", json=body).status_code == 200
        assert poll(client, body)["handoff_stage"] == "sending"
        assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}
        assert client.post(PREFIX + "/outcome", json=report(body, row, "unknown")).json() == {
            "stage": "unknown",
        }
        retained = dict(reopened.execute_read("SELECT * FROM live_messages")[0])
        assert {k: retained[k] for k in original if k != "outcome"} == {
            k: v for k, v in original.items() if k != "outcome"
        }
        db.deregister_live_session(consumer.session_id)
        app.state.loop_governance = None
        assert client.post(PREFIX + "/outcome", json=report(body, row, "consumed")).json() == {
            "stage": "consumed",
        }
    finally:
        reopened.close()
        app.state.db = db


@pytest.mark.parametrize("change", ["session", "consumer", "message", "handoff"])
def test_outcome_cannot_settle_another_original_handoff(serving, change: str) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
    payload = report(body, row, "consumed")
    if change in ("session", "consumer"):
        key = "session_id" if change == "session" else "incarnation"
        payload["consumer"] = {**payload["consumer"], key: "foreign"}
    else:
        payload["message_id" if change == "message" else "handoff_id"] = (
            row["id"] + 1 if change == "message" else "foreign"
        )
    assert client.post(PREFIX + "/outcome", json=payload).status_code == 409
    assert db.execute_read("SELECT consumed_at FROM live_messages")[0][0] is None


@pytest.mark.parametrize("problem", ["contract", "extra-owner", "coerced-pid", "nan-birth"])
def test_malformed_negotiation_refuses_without_epoch_mutation(serving, problem: str) -> None:
    app, client, db, consumer = serving
    payload = request(consumer)
    if problem == "contract":
        payload["contract"] = "retained-inbox-v2"
    elif problem == "extra-owner":
        payload["database"] = "/untrusted/client-selected.db"
    else:
        payload["consumer"] = {
            **payload["consumer"],
            **({"pid": "102"} if problem == "coerced-pid" else {"process_started_at": "NaN"}),
        }
    assert client.post(PREFIX + "/negotiate", json=payload).status_code == 422
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


def test_current_operations_do_not_implicitly_initialize_or_report_before_final_begin(serving) -> None:
    app, client, db, consumer = serving
    owner = app.state.loop_governance.runtime_installation(db)
    payload = {**request(consumer), "binding": {"installation": owner.document(), "epoch": 1}}
    assert client.post(PREFIX + "/poll", json=payload).status_code == 409
    assert client.post(PREFIX + "/enroll", json={
        **payload, "expected_incarnation": None,
    }).status_code == 409
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    body = enroll(client, consumer)
    message(app, db, consumer)
    row = poll(client, body)
    assert client.post(PREFIX + "/outcome", json=report(body, row, "consumed")).status_code == 409
    assert db.execute_read("SELECT consumed_at FROM live_messages")[0][0] is None
