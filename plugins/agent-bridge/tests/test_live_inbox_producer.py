"""Ordinary ASGI producer admission through the real enrolled SQLite owner."""

from __future__ import annotations

import json
import hashlib
import time

import pytest

from agent_bridge.db import Database
from agent_bridge.live_inbox_runtime import LiveInboxRuntime
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.runtime_custody import RuntimeCustodyError, RuntimeInstallation
from agent_bridge.plugin_readiness import PluginReadinessRequest, PluginReadinessResult, startup_input_digest
from test_live_inbox_runtime import (
    PREFIX, attempt, enroll, installation as installation, poll,
    request as consumer_request, serving as serving,
)

MESSAGES = "/api/v1/live-sessions/native-a/messages"
PAYLOAD = {
    "sender": "synthetic-sender", "body": "complete original input", "reply_to": "sender-a",
    "delivery": "queue", "idempotency_key": "original-operation",
}


def test_ordinary_send_uses_current_enrollment_and_never_legacy_ack(serving) -> None:
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    reply = client.post(MESSAGES, json=PAYLOAD)
    assert reply.status_code == 200, reply.text
    mid = reply.json()["message_id"]
    stored, = db.execute_read("SELECT * FROM live_messages")
    assert stored["runtime_custody_json"] == app.state.live_inbox_runtime._admission.document()
    assert stored["body"] == PAYLOAD["body"] and stored["idempotency_key"] == PAYLOAD["idempotency_key"]
    assert db.list_pending_live_messages(consumer.session_id) == []
    assert db.ack_live_messages(consumer.session_id, [mid], time.time()) == 0
    assert db.consume_live_message(consumer.session_id, mid, time.time()) == 0
    row = poll(client, body)
    assert row["id"] == mid and row["body"] == PAYLOAD["body"]
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}


def test_never_enrolled_target_stays_legacy_without_epoch_initialization(serving) -> None:
    _, client, db, consumer = serving
    reply = client.post(MESSAGES, json=PAYLOAD)
    assert reply.status_code == 200, reply.text
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    row, = db.list_pending_live_messages(consumer.session_id)
    assert row["runtime_custody_json"] is None


def test_unrelated_legacy_target_does_not_inherit_another_consumers_enrollment(serving) -> None:
    _, client, db, consumer = serving
    enroll(client, consumer)
    assert client.post("/api/v1/live-sessions", json={"session_id": "legacy-b"}).status_code == 200
    reply = client.post("/api/v1/live-sessions/legacy-b/messages", json=PAYLOAD)
    assert reply.status_code == 200, reply.text
    row, = db.list_pending_live_messages("legacy-b")
    assert row["body"] == PAYLOAD["body"] and row["runtime_custody_json"] is None


def test_lost_admission_reply_reopens_original_key_after_closure_and_registration_cleanup(serving) -> None:
    app, client, db, consumer = serving
    enroll(client, consumer)
    first = client.post(MESSAGES, json=PAYLOAD)
    assert first.status_code == 200, first.text
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    runtime = app.state.live_inbox_runtime
    owner = app.state.loop_governance.runtime_installation(db)
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="transition-a",
        target_assembly="target-a", predecessor=runtime._producer,
    )
    db.deregister_live_session(consumer.session_id)
    reopened = Database(db.db_path)
    app.state.db = reopened
    app.state.ready = False
    app.state.live_inbox_runtime = LiveInboxRuntime()
    try:
        retry = client.post(MESSAGES, json=PAYLOAD)
        assert retry.status_code == 200, retry.text
        assert retry.json()["duplicate"] is True
        assert retry.json()["message_id"] == first.json()["message_id"]
        assert dict(reopened.execute_read("SELECT * FROM live_messages")[0]) == original
        changed = client.post(MESSAGES, json={**PAYLOAD, "body": "changed input"})
        assert changed.status_code == 409 and "different" in changed.text
        assert len(reopened.execute_read("SELECT * FROM live_messages")) == 1
    finally:
        app.state.db = db
        reopened.close()


@pytest.mark.parametrize("cleanup", ["deregister", "purge"])
@pytest.mark.parametrize("stage", ["admitted", "offered", "sending"])
def test_original_key_recovers_after_native_renames_and_alias_cleanup(serving, cleanup, stage) -> None:
    app, client, db, consumer = serving
    now = time.time()
    assert db.create_cli_mode_reservation("worktree-a", now=now)
    registration = {
        "machine": "machine-a", "worktree_id": "worktree-a",
        "pid": consumer.pid, "process_started_at": consumer.process_started_at,
    }
    assert client.post("/api/v1/live-sessions", json={
        **registration, "session_id": consumer.session_id,
    }).status_code == 200
    body = enroll(client, consumer)
    payload = {**PAYLOAD, "expected_session_id": consumer.session_id}
    first = client.post(MESSAGES, json=payload)
    assert first.status_code == 200, first.text
    if stage != "admitted":
        row = poll(client, body)
        if stage == "sending":
            assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    for successor in ("native-b", "native-c"):
        renamed = client.post("/api/v1/live-sessions", json={
            **registration, "session_id": successor,
        })
        assert renamed.status_code == 200, renamed.text
    assert db.resolve_live_session_id(consumer.session_id) == "native-c"
    assert dict(db.execute_read("SELECT * FROM live_messages")[0]) == original
    successor_prefix = "/api/v1/live-sessions/native-c/retained-inbox"
    successor_request = {
        "contract": body["contract"],
        "consumer": {**consumer.document(), "session_id": "native-c", "incarnation": "consumer-c"},
    }
    observation = client.post(successor_prefix + "/negotiate", json=successor_request)
    assert observation.status_code == 200, observation.text
    successor_body = {**successor_request, **observation.json()}
    enrolled = client.post(successor_prefix + "/enroll", json=successor_body)
    assert enrolled.status_code == 200, enrolled.text
    successor_body.pop("expected_incarnation")
    pending = client.post(successor_prefix + "/poll", json=successor_body)
    assert pending.status_code == 200 and pending.json()["messages"] == []
    if stage != "admitted":
        assert client.post(PREFIX + "/begin", json=attempt(body, row)).status_code == 409
    if cleanup == "deregister":
        assert db.deregister_live_session("native-c")
    else:
        db.reap_stale_live_sessions(now=now + 1000, stale_seconds=1, purge_seconds=1,
                                   pid_alive=lambda _: False)
    assert db.execute_read("SELECT * FROM live_sessions") == []
    assert db.execute_read("SELECT * FROM live_session_aliases") == []
    reopened = Database(db.db_path)
    app.state.db = reopened
    app.state.ready = False
    app.state.live_inbox_runtime = LiveInboxRuntime()
    try:
        retry = client.post(MESSAGES, json=payload)
        assert retry.status_code == 200, retry.text
        assert retry.json()["duplicate"] and retry.json()["message_id"] == first.json()["message_id"]
        for changed in ({"body": "changed"}, {"expected_session_id": "foreign"}):
            assert client.post(MESSAGES, json={**payload, **changed}).status_code == 409
        assert client.post("/api/v1/live-sessions/foreign/messages", json=payload).status_code == 409
        assert dict(reopened.execute_read("SELECT * FROM live_messages")[0]) == original
        assert len(reopened.execute_read("SELECT * FROM live_messages")) == 1
        if stage == "sending":
            handoff = json.loads(original["handoff_json"])
            assert reopened.record_live_message_handoff(
                consumer, original["id"], handoff["id"], outcome="sdk_accepted", now=now + 1001,
            ) == "sdk_accepted"
    finally:
        app.state.db = db
        reopened.close()


@pytest.mark.parametrize("problem", [
    "frontend", "database", "closed", "draining", "proxy", "public-bind", "incarnation",
    "pid", "expiry", "installation", "readonly",
])
def test_selected_retained_target_refuses_without_legacy_downgrade(
    serving, monkeypatch: pytest.MonkeyPatch, problem: str,
) -> None:
    app, client, db, consumer = serving
    enroll(client, consumer)
    other = None
    if problem == "frontend":
        app.state.live_inbox_runtime = LiveInboxRuntime()
    elif problem in ("database", "readonly"):
        other = Database(db.db_path, read_only=problem == "readonly")
        app.state.db = other
    elif problem == "closed":
        owner = app.state.loop_governance.runtime_installation(db)
        db.close_runtime_epoch(
            owner, db.runtime_epoch(owner), transition_id="transition-a",
            target_assembly="target-a", predecessor=app.state.live_inbox_runtime._producer,
        )
    elif problem == "draining":
        app.state.session_manager.is_draining = True
    elif problem == "proxy":
        client.headers["X-Forwarded-For"] = "127.0.0.1"
    elif problem == "public-bind":
        app.state.config.bind = "0.0.0.0"
    elif problem == "incarnation":
        db.execute_write("UPDATE bridge_inbox_consumers SET current=0")
    elif problem == "pid":
        db.execute_write("UPDATE live_sessions SET pid=103")
    elif problem == "expiry":
        db.execute_write("UPDATE live_sessions SET updated_at=?", (time.time() - 1000,))
    else:
        def refuse(_db: Database) -> RuntimeInstallation:
            raise RuntimeCustodyError("installation changed")

        monkeypatch.setattr(app.state.loop_governance, "runtime_installation", refuse)
    try:
        reply = client.post(MESSAGES, json=PAYLOAD)
        assert reply.status_code == (503 if problem == "readonly" else 409), reply.text
        if problem != "readonly":
            assert reply.json()["detail"]["code"] == "retained_inbox_refused"
        assert db.execute_read("SELECT * FROM live_messages") == []
    finally:
        if other is not None:
            app.state.db = db
            other.close()


@pytest.mark.parametrize("changes", [
    {"delivery": "interrupt"}, {"kind": "startup:prompt:1"}, {"kind": "control:set-mode"},
    {"idempotency_key": None}, {"idempotency_key": " "},
])
def test_unsupported_message_effects_never_enter_the_enrolled_queue(serving, changes: dict) -> None:
    _, client, db, consumer = serving
    enroll(client, consumer)
    reply = client.post(MESSAGES, json={**PAYLOAD, **changes})
    assert reply.status_code in (400, 409)
    assert db.execute_read("SELECT * FROM live_messages") == []


def test_legacy_control_admission_and_enrollment_have_one_serial_order(serving) -> None:
    _, client, db, consumer = serving
    mid, reason = db.enqueue_live_message_if_fresh(
        consumer.session_id, sender="sender", body="mode", kind="control:set-mode", now=time.time(),
    )
    assert mid is not None and reason is None
    observation = client.post(PREFIX + "/negotiate", json=consumer_request(consumer))
    body = {**consumer_request(consumer), **observation.json()}
    reply = client.post(PREFIX + "/enroll", json=body)
    assert reply.status_code == 409 and "Pending legacy controls" in reply.text
    assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []
    assert db.withdraw_live_control(consumer.session_id, mid, time.time())
    assert client.post(PREFIX + "/enroll", json=body).status_code == 200
    new_mid, reason = db.enqueue_live_message_if_fresh(
        consumer.session_id, sender="sender", body="new mode", kind="control:set-mode", now=time.time(),
    )
    assert new_mid is None and reason == "retained_inbox_requires_admission"
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1


def test_startup_without_native_readiness_times_out_without_admitting_a_prompt(serving) -> None:
    _, client, db, consumer = serving
    enroll(client, consumer)
    reply = client.post(MESSAGES, json={**PAYLOAD, "plugin_readiness": {
        "owner": "bridge", "expected_session_id": consumer.session_id,
        "cwd": str(db.db_path.parent), "plugins": [], "timeout_seconds": 0.01,
    }})
    assert reply.status_code == 409, reply.text
    assert "readiness-timeout" in reply.text
    control, = db.execute_read("SELECT * FROM live_messages")
    assert control["kind"] == "control:plugin-readiness"
    assert control["runtime_custody_json"] is None
    assert control["outcome"] == "withdrawn"


@pytest.mark.parametrize("changed", ["none", "body", "sender", "operation", "not-ready", "control"])
@pytest.mark.parametrize("native_sdk", [False, True])
def test_retained_startup_admission_requires_its_original_ready_input(serving, changed, native_sdk):
    app, client, db, consumer = serving
    if native_sdk:
        from test_live_inbox_runtime import native_enroll

        native_enroll(client, consumer)
    else:
        enroll(client, consumer)
    expectation = PluginReadinessRequest(
        owner="bridge", expected_session_id=consumer.session_id, cwd=str(db.db_path.parent),
        plugins=[], timeout_seconds=1,
    )
    digest = startup_input_digest(
        expectation, body=PAYLOAD["body"], sender=PAYLOAD["sender"],
        reply_to=PAYLOAD["reply_to"], delivery=PAYLOAD["delivery"],
    )
    control, reason = db.enqueue_live_message_if_fresh(
        consumer.session_id, sender="bridge-startup:" + digest,
        body=json.dumps({**expectation.model_dump(), "selection_digest": "selected"}),
        now=time.time(), kind="control:plugin-readiness",
        idempotency_key="startup-readiness-" + hashlib.sha256(PAYLOAD["idempotency_key"].encode()).hexdigest(),
    )
    assert control is not None and reason is None
    assert [row["id"] for row in db.claim_live_controls(
        consumer.session_id, time.time(), 130, readiness_only=True,
    )] == [control]
    outcome = PluginReadinessResult(
        status="failed" if changed == "not-ready" else "ready",
        reason="selected-loadout-registered", session_id=consumer.session_id,
        cwd=str(db.db_path.parent), selection_digest="selected",
    )
    db.ack_live_messages(
        consumer.session_id, [control], time.time(), controls=True, outcome=outcome.model_dump_json(),
    )
    options = {
        **PAYLOAD, "kind": f"startup:prompt:{control}", "expected_session_id": consumer.session_id,
        "now": time.time(), "enrolled_inbox": True,
        "runtime_admission": app.state.live_inbox_runtime._admission,
    }
    if changed in {"body", "sender"}:
        options[changed] = "changed"
    elif changed == "operation":
        options["idempotency_key"] = "another-operation"
    elif changed == "control":
        options["kind"] = f"startup:prompt:{control + 1}"
    if changed == "none":
        message_id, reason = db.enqueue_live_message_if_fresh(consumer.session_id, **options)
        assert message_id is not None and reason is None
        message, = db.execute_read("SELECT * FROM live_messages WHERE kind NOT LIKE 'control:%'")
        assert (message["runtime_custody_json"] is None) == native_sdk
    else:
        with pytest.raises(RuntimeCustodyError, match="unsupported"):
            db.enqueue_live_message_if_fresh(consumer.session_id, **options)
        assert db.execute_read("SELECT * FROM live_messages WHERE kind NOT LIKE 'control:%'") == []


def test_new_frontend_retains_original_admission_on_retry_and_selects_new_one_for_new_work(serving) -> None:
    app, client, db, consumer = serving
    enroll(client, consumer)
    first = client.post(MESSAGES, json=PAYLOAD)
    assert first.status_code == 200
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    app.state.live_inbox_runtime = LiveInboxRuntime()
    app.state.loop_governance = LoopGovernance()
    # Negotiation recovers the same epoch; this is not SDK send authority.
    assert client.post(PREFIX + "/negotiate", json=consumer_request(consumer)).status_code == 200
    retry = client.post(MESSAGES, json=PAYLOAD)
    assert retry.status_code == 200 and retry.json()["duplicate"]
    assert dict(db.execute_read("SELECT * FROM live_messages")[0]) == original
    fresh = client.post(MESSAGES, json={**PAYLOAD, "idempotency_key": "new-operation"})
    assert fresh.status_code == 200
    rows = db.execute_read("SELECT * FROM live_messages ORDER BY id")
    assert rows[0]["runtime_custody_json"] != rows[1]["runtime_custody_json"]
    assert json.loads(rows[0]["runtime_custody_json"])["epoch"] == json.loads(
        rows[1]["runtime_custody_json"],
    )["epoch"]
    assert json.loads(db.execute_read("SELECT consumer_json FROM bridge_inbox_consumers")[0][0]) == (
        consumer.document()
    )
