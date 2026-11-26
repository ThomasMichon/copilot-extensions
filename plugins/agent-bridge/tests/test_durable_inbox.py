"""Durable input precedes receiver selection and never grants a second effect."""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import replace

import pytest

from agent_bridge.db import Database
from legacy_inbox_schema import restore_v49_inbox
from test_live_inbox_runtime import (
    PREFIX, Serving, attempt, current, enroll, installation as installation,
    native_enroll, poll, serving as serving,
)

MESSAGES = "/api/v1/live-sessions/native-a/messages"
PAYLOAD = {
    "sender": "original-sender", "body": "original input", "reply_to": "original-reply",
    "delivery": "queue", "idempotency_key": "original-operation",
}


@pytest.mark.parametrize("versioned", [False, True])
def test_installation_receiver_rejects_unsupported_fresh_effect_without_blocking_next_input(
    serving: Serving, versioned: bool,
) -> None:
    _, client, db, consumer = serving
    if versioned:
        consumer = replace(consumer, message_base=1)
    body = enroll(client, consumer)
    unsupported = client.post(MESSAGES, json={**PAYLOAD, "delivery": "interrupt"})
    assert unsupported.status_code == 200, unsupported.text
    accepted = client.post(MESSAGES, json={**PAYLOAD, "idempotency_key": "next-input"})
    assert accepted.status_code == 200, accepted.text
    row = poll(client, body)
    assert row["id"] == accepted.json()["message_id"]
    rejected = db.live_message_receipt(consumer.session_id, unsupported.json()["message_id"])
    assert rejected is not None and rejected["stage"] == "rejected"
    assert rejected["disposition"]["code"] == "unsupported_reader"
    assert rejected["disposition"]["message"]["message_id"] == unsupported.json()["message_id"]
    assert rejected["sdk_accepted_at"] is None and rejected["consumed_at"] is None
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}


@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("cleanup", [False, True])
def test_legacy_body_exposure_keeps_original_delivery_ownership_after_enrollment(
    serving: Serving, native: bool, cleanup: bool,
) -> None:
    _, client, db, consumer = serving
    accepted = client.post(MESSAGES, json=PAYLOAD)
    assert accepted.status_code == 200
    message_id = accepted.json()["message_id"]
    legacy, = db.list_pending_live_messages(consumer.session_id)
    assert legacy["id"] == message_id
    stored, = db.execute_read("SELECT * FROM live_messages")
    body = (native_enroll if native else enroll)(client, consumer)
    offered = client.post(PREFIX + "/poll", json=current(body))
    assert offered.status_code == 200, offered.text
    for duplicate in offered.json()["messages"]:
        if duplicate["id"] == message_id:
            assert client.post(PREFIX + "/begin", json=attempt(body, duplicate)).json() == {"begin": False}
    assert all(message["id"] != message_id for message in offered.json()["messages"])
    assert stored["claimed_at"] is not None
    with pytest.raises(sqlite3.IntegrityError, match="bridge_producer_claim_retained"):
        db.execute_write("UPDATE live_messages SET claimed_at=NULL WHERE id=?", (message_id,))
    next_input = client.post(MESSAGES, json={**PAYLOAD, "idempotency_key": "next-input"})
    assert next_input.status_code == 200
    row = poll(client, body)
    assert row["id"] == next_input.json()["message_id"]
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}
    original, = db.execute_read("SELECT * FROM live_messages WHERE id=?", (message_id,))
    assert original["handoff_json"] is None and original["handoff_started_at"] is None
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["queue_reason"] == "legacy_delivery_unconfirmed"
    if cleanup:
        assert db.deregister_live_session(consumer.session_id)
    acknowledged = client.post(MESSAGES + "/ack", json={"ids": [message_id]})
    assert acknowledged.status_code == 200 and acknowledged.json()["acked"] == 1
    assert client.post(MESSAGES + "/ack", json={"ids": [message_id]}).json()["acked"] == 0
    assert db.consume_live_message(consumer.session_id, message_id, time.time()) == 1
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["stage"] == "consumed"


def test_unsupported_legacy_poll_does_not_claim_hidden_exact_input(serving: Serving) -> None:
    _, client, db, consumer = serving
    accepted = client.post(MESSAGES, json={
        **PAYLOAD, "exact_session": True, "expected_session_id": consumer.session_id,
    })
    assert accepted.status_code == 200
    old_reader = client.get(MESSAGES)
    assert old_reader.status_code == 200 and old_reader.json()["messages"] == []
    stored, = db.execute_read("SELECT * FROM live_messages")
    assert stored["claimed_at"] is None
    body = enroll(client, consumer)
    row = poll(client, body)
    assert row["id"] == accepted.json()["message_id"]
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}


@pytest.mark.parametrize("native", [False, True])
def test_unavailable_receiver_later_delivers_the_original_durable_input(
    serving: Serving, native: bool,
) -> None:
    app, client, db, consumer = serving
    app.state.ready = False
    accepted = client.post(MESSAGES, json=PAYLOAD)
    assert accepted.status_code == 200, accepted.text
    message_id = accepted.json()["message_id"]
    original, = db.execute_read("SELECT * FROM live_messages")
    assert original["runtime_custody_json"] is None and original["handoff_json"] is None
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    assert db.deregister_live_session(consumer.session_id)
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1
    retry = client.post(MESSAGES, json=PAYLOAD)
    assert retry.status_code == 200 and retry.json()["message_id"] == message_id
    assert retry.json()["duplicate"] is True
    registered = client.post("/api/v1/live-sessions", json={
        "session_id": consumer.session_id, "pid": consumer.pid,
        "process_started_at": consumer.process_started_at,
    })
    assert registered.status_code == 200, registered.text
    app.state.ready = True
    body = (native_enroll if native else enroll)(client, consumer)
    row = poll(client, body)
    assert row["id"] == message_id and row["body"] == PAYLOAD["body"]
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}
    for outcome in ("sdk_accepted", "consumed"):
        observed = client.post(PREFIX + "/outcome", json={
            **{key: value for key, value in attempt(body, row).items() if key != "binding"},
            "outcome": outcome,
        })
        assert observed.status_code == 200, observed.text
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["stage"] == "consumed"
    assert client.post(PREFIX + "/poll", json=current(body)).json()["messages"] == []
    stored, = db.execute_read("SELECT * FROM live_messages")
    for field in ("sender", "body", "reply_to", "idempotency_key", "producer_target_json"):
        assert stored[field] == original[field]


@pytest.mark.parametrize("native", [False, True])
def test_unknown_original_effect_does_not_refuse_or_replay_new_input(
    serving: Serving, native: bool,
) -> None:
    _, client, db, consumer = serving
    body = (native_enroll if native else enroll)(client, consumer)
    first = client.post(MESSAGES, json=PAYLOAD)
    assert first.status_code == 200
    row = poll(client, body)
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
    unknown = client.post(PREFIX + "/outcome", json={
        **{key: value for key, value in attempt(body, row).items() if key != "binding"},
        "outcome": "unknown",
    })
    assert unknown.status_code == 200, unknown.text
    second = client.post(MESSAGES, json={
        **PAYLOAD, "idempotency_key": "new-operation", "body": "new input",
    })
    assert second.status_code == 200, second.text
    offered = client.post(PREFIX + "/poll", json=current(body))
    assert offered.status_code == 200, offered.text
    messages = offered.json()["messages"]
    assert [message["id"] for message in messages if message["handoff_stage"] == "offered"] == [
        second.json()["message_id"],
    ]
    assert all(
        message["id"] != row["id"] or message["handoff_stage"] == "unknown"
        for message in messages
    )
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}
    original = db.live_message_handoff_receipt(consumer.session_id, row["id"])
    assert original is not None and original["stage"] == "unknown"
    assert client.post(MESSAGES, json=PAYLOAD).json()["message_id"] == first.json()["message_id"]


def test_invalid_target_and_failed_write_never_report_acceptance(serving: Serving) -> None:
    app, client, db, _ = serving
    assert client.post("/api/v1/live-sessions/missing/messages", json=PAYLOAD).status_code == 404
    mismatch = client.post(MESSAGES, json={**PAYLOAD, "expected_session_id": "foreign"})
    assert mismatch.status_code == 409
    readonly = Database(db.db_path, read_only=True)
    app.state.db = readonly
    try:
        assert client.post(MESSAGES, json=PAYLOAD).status_code == 503
    finally:
        app.state.db = db
        readonly.close()
    assert db.execute_read("SELECT * FROM live_messages") == []
    db.execute_write(
        "CREATE TRIGGER controlled_failed_write BEFORE INSERT ON live_messages "
        "BEGIN SELECT RAISE(ABORT,'controlled_failed_durable_write'); END",
    )
    with pytest.raises(sqlite3.IntegrityError, match="controlled_failed_durable_write"):
        client.post(MESSAGES, json=PAYLOAD)
    assert db.execute_read("SELECT * FROM live_messages") == []


def test_original_alias_key_recovers_receipt_after_registration_and_alias_cleanup(
    serving: Serving,
) -> None:
    _, client, db, consumer = serving
    db.execute_write(
        "INSERT INTO live_session_aliases VALUES (?,?,?)",
        ("original-alias", consumer.session_id, time.time()),
    )
    path = "/api/v1/live-sessions/original-alias/messages"
    original = client.post(path, json=PAYLOAD)
    assert original.status_code == 200, original.text
    assert db.deregister_live_session(consumer.session_id)
    assert db.execute_read("SELECT * FROM live_session_aliases") == []
    recovered = client.post(path, json=PAYLOAD)
    assert recovered.status_code == 200, recovered.text
    assert recovered.json()["duplicate"] is True and recovered.json()["receipt_current"] is True
    assert recovered.json()["message_id"] == original.json()["message_id"]
    assert recovered.json()["receipt"] == original.json()["receipt"]
    assert client.post(MESSAGES, json=PAYLOAD).status_code == 409
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1


def test_schema_upgrade_retains_old_input_and_protects_new_queued_input(serving: Serving) -> None:
    app, client, db, consumer = serving
    original_id = db.enqueue_live_message(consumer.session_id, "old", "old input", time.time())
    path = db.db_path
    db.close()
    with sqlite3.connect(path) as old:
        restore_v49_inbox(old)
    reopened = Database(path)
    app.state.db = reopened
    try:
        legacy, = reopened.execute_read("SELECT * FROM live_messages")
        assert legacy["id"] == original_id and legacy["body"] == "old input"
        assert legacy["producer_target_json"] is None
        assert client.post(MESSAGES, json=PAYLOAD).status_code == 200
        queued, = reopened.execute_read("SELECT * FROM live_messages WHERE producer_target_json IS NOT NULL")
        assert json.loads(queued["producer_target_json"])["session_id"] == consumer.session_id
        for statement in (
            "DELETE FROM live_messages WHERE producer_target_json IS NOT NULL",
            "UPDATE live_messages SET body='changed' WHERE producer_target_json IS NOT NULL",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="bridge_producer_input_"):
                reopened.execute_write(statement)
        assert len(reopened.execute_read("SELECT * FROM live_messages")) == 2
    finally:
        reopened.close()
