"""Retained native identity is not replaced by a live routing alias."""

from __future__ import annotations

import time

import pytest

from agent_bridge.live_inbox_runtime import LiveInboxRuntime
from test_live_inbox_producer import PAYLOAD
from test_live_inbox_runtime import enroll, installation as installation, serving as serving


@pytest.fixture
def renamed(serving):
    app, client, db, consumer = serving
    assert db.create_cli_mode_reservation("worktree-a", now=time.time())
    registration = {
        "machine": "machine-a", "worktree_id": "worktree-a",
        "pid": consumer.pid, "process_started_at": consumer.process_started_at,
    }
    assert client.post("/api/v1/live-sessions", json={
        **registration, "session_id": consumer.session_id,
    }).status_code == 200
    enroll(client, consumer)
    url = f"/api/v1/live-sessions/{consumer.session_id}/messages"
    admitted = client.post(url, json=PAYLOAD)
    assert admitted.status_code == 200, admitted.text
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    assert client.post("/api/v1/live-sessions", json={
        **registration, "session_id": "native-b",
    }).status_code == 200
    assert db.resolve_live_session_id(consumer.session_id) == "native-b"
    return serving, url, admitted.json(), original


def test_active_alias_retains_original_receipt_and_rejects_successor_recovery(renamed) -> None:
    (_, client, db, consumer), url, admitted, original = renamed
    retry = client.post(url, json=PAYLOAD)
    assert retry.status_code == 200, retry.text
    assert retry.json()["receipt_current"] is True
    assert retry.json()["receipt"] == admitted["receipt"]
    assert retry.json()["session_id"] == consumer.session_id
    receipt = client.get(f"{url}/{admitted['message_id']}/receipt")
    assert receipt.status_code == 200 and receipt.json() == admitted["receipt"]
    successor = "/api/v1/live-sessions/native-b/messages"
    assert client.post(successor, json=PAYLOAD).status_code == 409
    assert client.get(f"{successor}/{admitted['message_id']}/receipt").status_code == 404
    assert client.post(url, json={**PAYLOAD, "expected_session_id": "native-b"}).status_code == 409
    assert db.inspect_live_inbox(consumer.session_id, 10)[0]["message_id"] == admitted["message_id"]
    assert db.inspect_live_inbox("native-b", 10) == []
    assert dict(db.execute_read("SELECT * FROM live_messages")[0]) == original


@pytest.mark.parametrize("frontend", ["serving", "missing", "closed"])
def test_active_alias_never_downgrades_original_enrollment_to_legacy(renamed, frontend) -> None:
    (app, client, db, _), url, _, original = renamed
    if frontend == "missing":
        app.state.live_inbox_runtime = LiveInboxRuntime()
    elif frontend == "closed":
        owner = app.state.loop_governance.runtime_installation(db)
        db.close_runtime_epoch(
            owner, db.runtime_epoch(owner), transition_id="transition-a",
            target_assembly="target-a", predecessor=app.state.live_inbox_runtime._producer,
        )
    rejected = client.post(url, json={**PAYLOAD, "idempotency_key": "new-operation"})
    assert rejected.status_code == 409, rejected.text
    assert rejected.json()["detail"]["code"] == "retained_inbox_refused"
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1
    assert dict(db.execute_read("SELECT * FROM live_messages")[0]) == original
    assert db.list_pending_live_messages("native-b") == []
    for kind in ("control:set-mode", "startup:prompt:1"):
        mid, reason = db.enqueue_live_message_if_fresh(
            "native-a", sender="sender", body="unsupported", kind=kind, now=time.time(),
        )
        assert mid is None and reason == "retained_inbox_requires_admission"


@pytest.mark.parametrize("operation", ["startup", "readiness", "mode-ready", "mode"])
def test_active_alias_refuses_control_admission_before_any_readiness_wait(
    renamed, monkeypatch, operation,
) -> None:
    (_, client, db, _), url, _, original = renamed
    waits = []

    def waiting(*args, **kwargs):
        waits.append(True)
        raise AssertionError("retained refusal must precede the control/readiness waiter")

    monkeypatch.setattr(db, "live_control_state", waiting)
    readiness = {
        "owner": "bridge", "cwd": str(db.db_path.parent), "plugins": [], "timeout_seconds": 120,
    }
    base = url.removesuffix("/messages")
    if operation == "startup":
        response = client.post(url, json={
            **PAYLOAD, "idempotency_key": "startup-operation", "plugin_readiness": readiness,
        })
    elif operation == "readiness":
        response = client.post(base + "/plugin-readiness", json=readiness)
    else:
        payload = {"mode": "autopilot"}
        if operation == "mode-ready":
            payload["plugin_readiness"] = readiness
        response = client.post(base + "/mode", json=payload)
    assert response.status_code == 409, response.text
    assert "retained_inbox_requires_admission" in response.text
    assert waits == []
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1
    assert dict(db.execute_read("SELECT * FROM live_messages")[0]) == original
