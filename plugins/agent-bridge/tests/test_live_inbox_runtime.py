"""Actual ASGI/installation/DB adapter controls, not SDK or native qualification."""

from __future__ import annotations

from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Iterator
import json
import os
import sqlite3
import time
from types import SimpleNamespace
from typing import Any, cast

from fastapi.testclient import TestClient
from fastapi import FastAPI
import pytest
from zdd.diagnostics import process_start_time

from agent_bridge import live_inbox_runtime
from agent_bridge.app_routes import configure_serving_app
from agent_bridge.db import Database
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.models import ServiceConfig
from agent_bridge.runtime_custody import RuntimeCustodyError, encode
from agent_bridge.message_contracts import disposition_guards
from agent_bridge.live_message_schema import INDEX as UNRESOLVED_INDEX
from test_loop_governance_custody import installation as installation

CONTRACT = "retained-inbox-v1"
PREFIX = "/api/v1/live-sessions/native-a/retained-inbox"
Serving = tuple[FastAPI, TestClient, Database, InboxConsumer]


@pytest.mark.parametrize("native", [True, False])
def test_versioned_enrollment_migrates_24_legacy_replays_without_effects(serving: Serving, native: bool) -> None:
    _, client, db, consumer = serving
    ids = [
        db.enqueue_live_message(consumer.session_id, "bridge", "", time.time(), kind="control:replay-history")
        for _ in range(24)
    ]
    db.execute_write("UPDATE live_messages SET message_contract_json=NULL")
    consumer = replace(consumer, message_base=1)
    body = (native_enroll if native else enroll)(client, consumer)
    response = client.post(PREFIX + "/poll", json=current(body))
    assert response.status_code == 200, response.text
    rows = response.json()["messages"]
    assert [row["id"] for row in rows] == ids
    for row in rows:
        assert row["message_contract"]["type_version"] == 1
        assert row["message_contract"]["legacy"] is True
        assert row["control"] is True
    assert all(row["claimed_at"] is None for row in db.execute_read("SELECT * FROM live_messages"))
    assert client.post(PREFIX + "/poll", json=current(body)).json() == response.json()


@pytest.mark.parametrize("native", [True, False])
@pytest.mark.parametrize("kind,version,requires", [
    ("future:unknown", 1, ()), ("prompt", 9, ()), ("prompt", 1, ("future-required",)),
])
def test_versioned_rejection_is_terminal_and_sender_correlated(
    serving: Serving, native: bool, kind: str, version: int, requires: tuple[str, ...],
) -> None:
    app, client, db, consumer = serving
    consumer = replace(consumer, message_base=1)
    body = (native_enroll if native else enroll)(client, consumer)
    options = {} if native else {"runtime_admission": app.state.live_inbox_runtime._admission}
    message_id = db.enqueue_live_message(
        consumer.session_id, "original-sender", "never execute", time.time(),
        kind=kind, message_version=version, message_requires=requires, **options,
    )
    row = poll(client, body)
    assert row["message_contract"]["sender"] == "original-sender"
    payload = {
        **current(body), "message_id": message_id, "state": "rejected",
        "code": "unsupported_message", "reason": "The receiver cannot enforce this contract.",
    }
    rejected = client.post(PREFIX + "/disposition", json=payload)
    assert rejected.status_code == 200, rejected.text
    assert client.post(PREFIX + "/disposition", json=payload).json() == rejected.json()
    assert client.post(PREFIX + "/poll", json=current(body)).json()["messages"] == []
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None
    assert receipt["stage"] == "rejected"
    assert receipt["disposition"]["code"] == "unsupported_message"
    assert receipt["disposition"]["message"]["message_id"] == message_id
    assert receipt["sdk_accepted_at"] is None and receipt["consumed_at"] is None
    assert db.list_pending_live_messages(consumer.session_id) == []
    assert db.inspect_live_inbox(consumer.session_id, 10) == []
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 409


@pytest.mark.parametrize("native", [True, False])
def test_versioned_control_start_is_one_shot_and_unknown_cannot_be_rejected(serving: Serving, native: bool) -> None:
    _, client, db, consumer = serving
    message_id = db.enqueue_live_message(
        consumer.session_id, "bridge", "", time.time(), kind="control:replay-history",
    )
    consumer = replace(consumer, message_base=1)
    body = (native_enroll if native else enroll)(client, consumer)
    payload = {
        **current(body), "message_id": message_id, "state": "started",
        "code": "history_replay_started", "reason": "Bounded reconstruction admitted.",
    }
    first = client.post(PREFIX + "/disposition", json=payload)
    assert first.status_code == 200, first.text
    assert first.json()["begin"] is True
    assert client.post(PREFIX + "/disposition", json=payload).json()["begin"] is False
    rejected = {**payload, "state": "rejected", "code": "unsupported", "reason": "Do not replay."}
    assert client.post(PREFIX + "/disposition", json=rejected).status_code == 409
    unknown = {**payload, "state": "unknown", "code": "history_unknown", "reason": "Effect not observed."}
    assert client.post(PREFIX + "/disposition", json=unknown).status_code == 200
    assert client.post(PREFIX + "/poll", json=current(body)).json()["messages"] == []
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None
    assert receipt["stage"] == "unknown" and receipt["sdk_accepted_at"] is None
    successor = (native_enroll if native else enroll)(client, replace(consumer, incarnation="new-reader"))
    assert client.post(PREFIX + "/disposition", json=payload).status_code == 409
    assert client.post(PREFIX + "/poll", json=current(successor)).json()["messages"] == []


def test_versioned_old_database_first_touch_and_original_key_survive_rejection(serving: Serving) -> None:
    app, client, db, consumer = serving
    message_id = db.enqueue_live_message(
        consumer.session_id, "sender", "body", time.time(),
        idempotency_key="original-operation", kind="future:message",
    )
    path = db.db_path
    db.close()
    with sqlite3.connect(path) as old:
        for name in disposition_guards():
            old.execute(f"DROP TRIGGER {name}")
        old.execute(f"DROP INDEX {UNRESOLVED_INDEX}")
        old.execute("ALTER TABLE live_messages DROP COLUMN message_contract_json")
        old.execute("ALTER TABLE live_messages DROP COLUMN disposition_json")
    reopened = Database(path)
    app.state.db = reopened
    try:
        consumer = replace(consumer, message_base=1)
        body = native_enroll(client, consumer)
        row = poll(client, body)
        assert row["id"] == message_id and row["message_contract"]["legacy"] is True
        payload = {
            **current(body), "message_id": message_id, "state": "rejected",
            "code": "unsupported_message", "reason": "Unknown stored type.",
        }
        assert client.post(PREFIX + "/disposition", json=payload).status_code == 200
        retry = client.post("/api/v1/live-sessions/native-a/messages", json={
            "sender": "sender", "body": "body", "kind": "future:message", "delivery": "queue",
            "idempotency_key": "original-operation",
        })
        assert retry.status_code == 200, retry.text
        assert retry.json()["message_id"] == message_id
        assert retry.json()["receipt"]["stage"] == "rejected"
        assert reopened.ack_live_messages(consumer.session_id, [message_id], time.time()) == 0
        reopened.deregister_live_session(consumer.session_id)
        retained = reopened.live_message_receipt(consumer.session_id, message_id)
        assert retained is not None and retained["stage"] == "rejected"
    finally:
        reopened.close()


def test_versioned_control_begin_race_and_authentication_preserve_one_effect_grant(serving: Serving) -> None:
    _, client, db, consumer = serving
    message_id = db.enqueue_live_message(
        consumer.session_id, "sender", "", time.time(), kind="control:replay-history",
    )
    body = native_enroll(client, replace(consumer, message_base=1))
    payload = {
        **current(body), "message_id": message_id, "state": "started",
        "code": "history_replay_started", "reason": "Bounded reconstruction admitted.",
    }
    unauthorized = client.post(PREFIX + "/disposition", json=payload, headers={"Authorization": "invalid"})
    assert unauthorized.status_code == 401
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: client.post(PREFIX + "/disposition", json=payload), range(2)))
    assert all(result.status_code == 200 for result in results)
    assert sorted(result.json()["begin"] for result in results) == [False, True]
    retained = db.live_message_receipt(consumer.session_id, message_id)
    assert retained is not None and retained["stage"] == "control_started"


@pytest.fixture
def serving(installation: dict[str, Any], tmp_db: Database) -> Iterator[Serving]:
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
    return {"contract": CONTRACT, "consumer": {
        **consumer.document(),
        **({"message_base": consumer.message_base} if consumer.message_base is not None else {}),
    }}


def test_legacy_request_serialization_does_not_send_a_new_selector() -> None:
    from agent_bridge.live_inbox_models import InboxNegotiateRequest

    original = request(InboxConsumer("native-a", "consumer-a", 102, 1.0))
    assert InboxNegotiateRequest.model_validate(original).model_dump() == original


def enroll(client: TestClient, consumer: InboxConsumer) -> dict[str, Any]:
    observation = client.post(PREFIX + "/negotiate", json=request(consumer))
    assert observation.status_code == 200, observation.text
    body = {**request(consumer), **observation.json()}
    response = client.post(PREFIX + "/enroll", json=body)
    assert response.status_code == 200, response.text
    return body


def current(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "expected_incarnation"}


def message(app: FastAPI, db: Database, consumer: InboxConsumer, **kwargs: Any) -> int:
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
    return cast(dict[str, Any], row)


def attempt(body: dict[str, Any], row: dict[str, Any]) -> dict[str, Any]:
    return {**current(body), "message_id": row["id"], "handoff_id": row["handoff_id"]}


def report(body: dict[str, Any], row: dict[str, Any], outcome: str) -> dict[str, Any]:
    payload = attempt(body, row)
    payload.pop("binding")
    return {**payload, "outcome": outcome}


def native_enroll(client: TestClient, consumer: InboxConsumer) -> dict[str, Any]:
    owner = {**request(consumer), "receiving_api": "native-sdk-v1"}
    observation = client.post(PREFIX + "/negotiate", json=owner)
    assert observation.status_code == 200, observation.text
    body = {**owner, **observation.json()}
    response = client.post(PREFIX + "/enroll", json=body)
    assert response.status_code == 200, response.text
    return body


def test_native_sdk_receiving_needs_no_installation_or_process_custody(serving: Serving) -> None:
    app, client, db, consumer = serving
    app.state.loop_governance = None
    app.state.live_inbox_runtime = None
    consumer = replace(consumer, pid=None, process_started_at=None)
    original = db.enqueue_live_message(consumer.session_id, "peer", "original", time.time())
    body = native_enroll(client, consumer)
    row = poll(client, body)
    assert row["id"] == original and row["body"] == "original"
    assert row["consumer"] == consumer.document()
    assert poll(client, body) == row
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}
    assert client.post(PREFIX + "/outcome", json=report(body, row, "unknown")).json() == {
        "stage": "unknown",
    }
    successor = native_enroll(client, replace(consumer, incarnation="reloaded"))
    assert client.post(PREFIX + "/poll", json=current(successor)).json()["messages"] == []
    assert client.get(PREFIX + f"/recovery/{original}").json()["stage"] == "unknown"
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 409
    assert client.post(PREFIX + "/outcome", json=report(body, row, "consumed")).json() == {
        "stage": "consumed",
    }
    fresh = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "fresh", "delivery": "steer",
    })
    assert fresh.status_code == 200, fresh.text
    assert poll(client, successor)["body"] == "fresh"
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    assert db.execute_read("SELECT * FROM bridge_runtime_producers") == []


def test_native_reload_keeps_offered_original_consumer_and_never_resends_begun_work(serving: Serving) -> None:
    _, client, db, consumer = serving
    consumer = replace(consumer, message_base=1)
    first = native_enroll(client, consumer)
    message_id = db.enqueue_live_message(consumer.session_id, "peer", "retained", time.time())
    offered = poll(client, first)
    successor = native_enroll(client, replace(consumer, incarnation="reloaded"))
    migrated = poll(client, successor)
    assert migrated["handoff_id"] == offered["handoff_id"]
    assert migrated["consumer"] == offered["consumer"]
    assert migrated["message_contract"]["receiver_incarnation"] == "reloaded"
    assert client.post(PREFIX + "/begin", json=attempt(first, offered)).status_code == 409
    original = attempt(successor, offered)
    assert client.post(PREFIX + "/begin", json=original).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=original).json() == {"begin": False}
    assert offered["id"] == message_id
    assert client.post(PREFIX + "/begin", json=attempt(first, offered)).status_code == 409


def test_idle_successor_reattempts_unsubmitted_original_until_bounded_failure(serving: Serving) -> None:
    _, client, db, consumer = serving
    consumer = replace(consumer, message_base=1)
    first = native_enroll(client, consumer)
    message_id = db.enqueue_live_message(consumer.session_id, "peer", "retained", time.time())
    offered = poll(client, first)
    assert client.post(PREFIX + "/begin", json=attempt(first, offered)).json() == {"begin": True}
    assert client.post(PREFIX + "/outcome", json=report(first, offered, "unknown")).json() == {
        "stage": "unknown",
    }
    successor = native_enroll(client, replace(consumer, incarnation="reloaded"))
    assert client.post(PREFIX + "/poll", json=current(successor)).json()["messages"] == []
    listed = client.post(PREFIX + "/poll", json={**current(successor), "reattempt": [message_id]})
    row, = listed.json()["messages"]
    assert (row["id"], row["handoff_id"], row["body"]) == (message_id, offered["handoff_id"], "retained")
    assert row["consumer"] == offered["consumer"] and row["attempt"] == 1
    assert row["handoff_stage"] == "unknown"
    assert row["message_contract"]["receiver_incarnation"] == "reloaded"

    again = {**attempt(successor, row), "attempt": 1}
    assert client.post(PREFIX + "/begin", json=again).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=again).json() == {"begin": False}
    assert client.post(PREFIX + "/begin", json=attempt(first, row)).status_code == 409
    ledger = db.execute_read("SELECT message_id,attempt,incarnation FROM live_message_attempts")
    assert [tuple(entry) for entry in ledger] == [(message_id, 2, "reloaded")]
    assert client.get(PREFIX + f"/recovery/{message_id}").json()["stage"] == "sending"

    assert client.post(PREFIX + "/begin", json={**again, "attempt": 2}).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json={**again, "attempt": 3}).json() == {"begin": False}
    stored, = db.execute_read("SELECT disposition_json,handoff_json FROM live_messages WHERE id=?", (message_id,))
    disposition = json.loads(stored["disposition_json"])
    assert (disposition["state"], disposition["code"], disposition["attempts"]) == (
        "failed", "delivery_attempts_exhausted", 3,
    )
    assert json.loads(stored["handoff_json"])["id"] == offered["handoff_id"]
    exhausted = client.post(PREFIX + "/poll", json={**current(successor), "reattempt": [message_id]})
    assert exhausted.json()["messages"] == []
    assert client.post(PREFIX + "/begin", json={**again, "attempt": 3}).status_code == 409
    # Late consumption evidence still settles the report; the disposition stays.
    late = report(first, row, "consumed")
    assert client.post(PREFIX + "/outcome", json=late).json() == {"stage": "consumed"}
    kept, = db.execute_read("SELECT disposition_json FROM live_messages WHERE id=?", (message_id,))
    assert json.loads(kept["disposition_json"])["code"] == "delivery_attempts_exhausted"
    assert client.post(PREFIX + "/outcome", json=late).json() == {"stage": "consumed"}
    late_ledger = db.execute_read(
        "SELECT attempt,late_outcome FROM live_message_attempts WHERE late_outcome IS NOT NULL")
    assert [tuple(entry) for entry in late_ledger] == [(3, "consumed")]


@pytest.mark.parametrize("stage", ["sending", "unknown"])
def test_native_interrupt_cannot_receive_another_attempt(serving: Serving, stage: str) -> None:
    _, client, db, consumer = serving
    consumer = replace(consumer, message_base=1)
    first = native_enroll(client, consumer)
    message_id = db.enqueue_live_message(
        consumer.session_id, "peer", "original interrupt", time.time(), delivery="interrupt",
    )
    offered = poll(client, first)
    assert client.post(PREFIX + "/begin", json=attempt(first, offered)).json() == {"begin": True}
    if stage == "unknown":
        assert client.post(PREFIX + "/outcome", json=report(first, offered, "unknown")).status_code == 200
    successor = native_enroll(client, replace(consumer, incarnation="reloaded"))
    listed = client.post(PREFIX + "/poll", json={**current(successor), "reattempt": [message_id]})
    assert listed.status_code == 200, listed.text
    assert listed.json()["messages"] == []
    regrant = {**attempt(successor, offered), "attempt": 1}
    response = client.post(PREFIX + "/begin", json=regrant)
    assert response.status_code == 200, response.text
    assert response.json() == {"begin": False}
    assert db.execute_read("SELECT * FROM live_message_attempts") == []
    retained = client.get(PREFIX + f"/recovery/{message_id}")
    assert retained.status_code == 200, retained.text
    assert retained.json()["stage"] == stage
    assert retained.json()["handoff_id"] == offered["handoff_id"]


def test_reattempt_requires_a_message_base_receiver(serving: Serving) -> None:
    _, client, db, consumer = serving
    first = native_enroll(client, consumer)
    message_id = db.enqueue_live_message(consumer.session_id, "peer", "retained", time.time())
    offered = poll(client, first)
    assert client.post(PREFIX + "/begin", json=attempt(first, offered)).json() == {"begin": True}
    successor = native_enroll(client, replace(consumer, incarnation="reloaded"))
    listed = client.post(PREFIX + "/poll", json={**current(successor), "reattempt": [message_id]})
    assert listed.json()["messages"] == []
    regrant = {**attempt(successor, offered), "attempt": 1}
    assert client.post(PREFIX + "/begin", json=regrant).json() == {"begin": False}
    assert db.execute_read("SELECT * FROM live_message_attempts") == []


@pytest.mark.parametrize("change", ["no-auth", "proxy", "dormant", "wrong-session", "read-only"])
def test_native_inbox_preserves_real_target_and_ingress_boundaries(serving: Serving, change: str) -> None:
    app, client, db, consumer = serving
    other = None
    if change == "no-auth":
        client.headers.pop("authorization")
    elif change == "proxy":
        client.headers["Forwarded"] = "127.0.0.1"
    elif change == "dormant":
        db.deregister_live_session(consumer.session_id)
    elif change == "wrong-session":
        consumer = replace(consumer, session_id="foreign")
    else:
        other = Database(db.db_path, read_only=True)
        app.state.db = other
    try:
        response = client.post(PREFIX + "/negotiate", json={
            **request(consumer), "receiving_api": "native-sdk-v1",
        })
        assert response.status_code == (
            401 if change == "no-auth" else 503 if change == "read-only" else 409
        )
        assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []
        assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    finally:
        if other is not None:
            other.close()


def test_native_mode_is_explicit_and_unknown_selectors_cannot_downgrade(serving: Serving) -> None:
    app, client, _, consumer = serving
    app.state.loop_governance = None
    assert client.post(PREFIX + "/negotiate", json=request(consumer)).status_code == 503
    assert client.post(PREFIX + "/negotiate", json={
        **request(consumer), "receiving_api": "unknown",
    }).status_code == 422
    assert client.post(PREFIX + "/negotiate", json=request(
        replace(consumer, pid=None, process_started_at=None),
    )).status_code == 422


def test_native_inbox_survives_closed_installation_epoch_without_relaxing_controls(serving: Serving) -> None:
    app, client, db, consumer = serving
    enroll(client, consumer)
    runtime = app.state.live_inbox_runtime
    owner = app.state.loop_governance.runtime_installation(db)
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="transition-a",
        target_assembly="retained-a", predecessor=runtime._producer,
    )
    body = native_enroll(client, replace(consumer, incarnation="native-sdk"))
    response = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "ordinary", "delivery": "steer", "idempotency_key": "ordinary",
    })
    assert response.status_code == 200, response.text
    row = poll(client, body)
    assert row["body"] == "ordinary"
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}
    additive = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "ordinary additive kind", "kind": "reply", "delivery": "queue",
    })
    assert additive.status_code == 200, additive.text
    assert poll(client, body)["kind"] == "reply"
    interrupt = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "unsupported by the old receiver", "delivery": "interrupt",
    })
    assert interrupt.status_code == 409
    for changes in ({"kind": "control:set-mode"}, {"kind": "startup:prompt:1"}):
        response = client.post("/api/v1/live-sessions/native-a/messages", json={
            "sender": "peer", "body": "unsupported", "delivery": "steer",
            "idempotency_key": str(changes), **changes,
        })
        assert response.status_code == 400, response.text
    assert len(db.execute_read("SELECT * FROM live_messages")) == 2
    app.state.ready = False
    assert client.post(PREFIX + "/poll", json=current(body)).status_code == 503
    assert client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "not serving", "delivery": "steer",
    }).status_code == 503


def test_native_poll_does_not_promote_unverified_legacy_startup_input(serving: Serving) -> None:
    _, client, db, consumer = serving
    message_id = db.enqueue_live_message(
        consumer.session_id, "peer", "unverified startup", time.time(),
        kind="startup:prompt:1",
    )
    body = native_enroll(client, consumer)
    response = client.post(PREFIX + "/poll", json=current(body))
    assert response.status_code == 200 and response.json()["messages"] == []
    assert db.execute_read("SELECT handoff_started_at FROM live_messages")[0][0] is None
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None
    assert receipt["stage"] == "rejected"
    assert receipt["disposition"]["code"] == "unsupported_reader"
    assert receipt["sdk_accepted_at"] is None


@pytest.mark.parametrize("old_state", ["claimed", "unknown", "begun"])
def test_native_receiving_does_not_replay_or_block_on_older_unconfirmed_work(serving: Serving, old_state: str) -> None:
    _, client, db, consumer = serving
    old = db.enqueue_live_message(consumer.session_id, "peer", "old input", time.time())
    if old_state in ("claimed", "unknown"):
        db.execute_write(
            "UPDATE live_messages SET claimed_at=?,outcome=? WHERE id=?",
            (time.time(), "unknown" if old_state == "unknown" else None, old),
        )
    body = native_enroll(client, consumer)
    if old_state == "begun":
        offered = poll(client, body)
        assert client.post(PREFIX + "/begin", json=attempt(body, offered)).json()["begin"]
    before = dict(db.execute_read("SELECT * FROM live_messages WHERE id=?", (old,))[0])
    fresh = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "new input", "delivery": "steer",
    })
    assert fresh.status_code == 200, fresh.text
    assert poll(client, body)["body"] == "new input"
    assert dict(db.execute_read("SELECT * FROM live_messages WHERE id=?", (old,))[0]) == before


@pytest.mark.parametrize("metadata", [
    {"pid": 300}, {"process_started_at": 2.0}, {"machine": "renamed-host"},
])
def test_native_receiving_does_not_reacquire_custody_after_metadata_refresh(
    serving: Serving, metadata: dict[str, Any],
) -> None:
    _, client, db, consumer = serving
    body = native_enroll(client, consumer)
    refreshed = client.post("/api/v1/live-sessions", json={
        "session_id": consumer.session_id, **metadata,
    })
    assert refreshed.status_code == 200, refreshed.text
    assert db.execute_read("SELECT current FROM bridge_inbox_consumers")[0][0] == 1
    body = {**body, "consumer": {
        **body["consumer"],
        **{key: value for key, value in metadata.items() if key != "machine"},
    }}
    assert client.post(PREFIX + "/enroll", json=body).status_code == 200
    fresh = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "after metadata refresh", "delivery": "steer",
    })
    assert fresh.status_code == 200, fresh.text
    offered = poll(client, body)
    assert offered["body"] == "after metadata refresh"
    assert client.post(PREFIX + "/begin", json=attempt(body, offered)).json() == {"begin": True}
    assert client.post(PREFIX + "/outcome", json=report(body, offered, "sdk_accepted")).json() == {
        "stage": "sdk_accepted",
    }
    assert offered["consumer"] == consumer.document()


def test_explicit_negotiation_only_enrolls_actual_frontend_and_cas_consumer(serving: Serving) -> None:
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
        "pid": os.getpid(), "process_start": process_start_time(os.getpid()),
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


def test_poll_reply_loss_preserves_original_and_final_admission_is_one_shot(serving: Serving) -> None:
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


def test_distinct_outcomes_survive_closure_retirement_cleanup_and_lost_reply(serving: Serving) -> None:
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


def test_reload_cas_never_rewrites_original_handoff_or_inherits_final_permission(serving: Serving) -> None:
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
def test_wrong_observations_refuse_before_body_or_final_admission(serving: Serving, change: str) -> None:
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
def test_unsupported_ingress_never_initializes_or_exposes_inbox(serving: Serving, exposure: str) -> None:
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
def test_unqualified_registry_cannot_enroll_consumer(serving: Serving, problem: str) -> None:
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
def test_current_owner_change_refuses_fresh_effects(
    serving: Serving, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
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
    serving: Serving, message_kind: str,
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


def test_historical_interrupt_cannot_gain_final_admission(serving: Serving) -> None:
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


def test_reopened_database_retains_lost_final_reply_and_original_outcome_identity(serving: Serving) -> None:
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
def test_outcome_cannot_settle_another_original_handoff(serving: Serving, change: str) -> None:
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
def test_malformed_negotiation_refuses_without_epoch_mutation(serving: Serving, problem: str) -> None:
    app, client, db, consumer = serving
    payload = request(consumer)
    if problem == "contract":
        payload["contract"] = "retained-inbox-v2"
    elif problem == "extra-owner":
        payload["database"] = "/untrusted/client-selected.db"
    else:
        payload["consumer"] = {
            **consumer.document(),
            **({"pid": "102"} if problem == "coerced-pid" else {"process_started_at": "NaN"}),
        }
    assert client.post(PREFIX + "/negotiate", json=payload).status_code == 422
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


def test_current_operations_do_not_implicitly_initialize_or_report_before_final_begin(serving: Serving) -> None:
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
