"""ASGI recovery reads retain the existing local admission boundary."""

from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agent_bridge.db import Database
from test_live_inbox_runtime import (
    PREFIX, attempt, enroll, message, poll, report, serving as serving,
)
from test_loop_governance_custody import installation as installation


def test_recovery_reads_original_receipt_after_reload_closure_and_passive_reopen(serving):
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    mid = message(app, db, consumer)
    row = poll(client, body)
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
    enroll(client, replace(consumer, incarnation="successor"))
    owner = app.state.loop_governance.runtime_installation(db)
    runtime = app.state.live_inbox_runtime
    db.close_runtime_epoch(
        owner, db.runtime_epoch(owner), transition_id="closed",
        target_assembly="retained", predecessor=runtime._producer,
    )
    db.deregister_live_session(consumer.session_id)
    passive = Database(db.db_path, read_only=True)
    app.state.db = passive
    app.state.ready = False
    app.state.session_manager = SimpleNamespace(is_draining=True)
    app.state.live_inbox_runtime = None
    app.state.loop_governance = None
    before = db.runtime_epoch(owner)
    try:
        response = client.get(PREFIX + f"/recovery/{mid}")
        assert response.status_code == 200, response.text
        receipt = response.json()
        assert receipt["consumer"] == consumer.document()
        assert receipt["handoff_id"] == row["handoff_id"]
        assert receipt["stage"] == "sending"
        page = client.get(PREFIX + "/recovery", params={"limit": 1})
        assert page.status_code == 200, page.text
        assert page.json()["messages"] == [receipt]
        assert "original body" not in page.text
        assert db.runtime_epoch(owner) == before
        # Unextended receipt shape stays byte-for-byte compatible.
        legacy = client.get(f"/api/v1/live-sessions/native-a/messages/{mid}/receipt").json()
        assert set(legacy) == {
            "session_id", "message_id", "stage", "admitted_at", "sdk_accepted_at", "consumed_at",
        }
    finally:
        app.state.db = db
        passive.close()
    assert client.post(PREFIX + "/outcome", json=report(body, row, "consumed")).json() == {
        "stage": "consumed",
    }
    assert client.get(PREFIX + "/recovery").json()["messages"] == []
    assert client.get(PREFIX + f"/recovery/{mid}").json()["stage"] == "consumed"


@pytest.mark.parametrize("exposure", ["remote", "proxy", "public-bind", "no-auth"])
def test_recovery_never_bypasses_local_ingress_or_initializes_runtime(serving, exposure):
    app, client, db, _ = serving
    remote = None
    if exposure == "remote":
        remote = TestClient(app, headers=dict(client.headers), client=("192.0.2.1", 12345))
        client = remote
    elif exposure == "proxy":
        client.headers["X-Forwarded-For"] = "127.0.0.1"
    elif exposure == "public-bind":
        app.state.config.bind = "0.0.0.0"
    else:
        client.headers.pop("authorization")
    try:
        for endpoint in ("/recovery", "/recovery/1"):
            response = client.get(PREFIX + endpoint)
            assert response.status_code == (401 if exposure == "no-auth" else 409)
        assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
        assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []
    finally:
        if remote is not None:
            remote.close()


@pytest.mark.parametrize("query", [
    {"limit": 0}, {"limit": 101}, {"after": -1}, {"after": 1 << 63},
])
def test_recovery_rejects_invalid_bounds_and_missing_receipts(serving, query):
    _, client, _, _ = serving
    assert client.get(PREFIX + "/recovery", params=query).status_code == 422
    assert client.get(PREFIX + "/recovery/1").status_code == 404


def test_bounded_poll_mints_only_one_original_before_a_possible_consumer_crash(serving):
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    ids = [message(app, db, consumer) for _ in range(3)]
    payload = {key: value for key, value in body.items() if key != "expected_incarnation"}
    response = client.post(PREFIX + "/poll", json={**payload, "limit": 1})
    assert response.status_code == 200, response.text
    assert [row["id"] for row in response.json()["messages"]] == ids[:1]
    rows = db.execute_read("SELECT id,handoff_json FROM live_messages ORDER BY id")
    assert [row["handoff_json"] is not None for row in rows] == [True, False, False]
    assert client.post(PREFIX + "/poll", json={**payload, "limit": 1}).json() == response.json()
    # An older caller omitting the optional bound retains its original contract.
    assert len(client.post(PREFIX + "/poll", json=payload).json()["messages"]) == 3


@pytest.mark.parametrize("limit", [0, 101, True, 1.5, "1"])
def test_bounded_poll_rejects_invalid_limits_without_retaining_attempts(serving, limit):
    app, client, db, consumer = serving
    body = enroll(client, consumer)
    message(app, db, consumer)
    payload = {key: value for key, value in body.items() if key != "expected_incarnation"}
    assert client.post(PREFIX + "/poll", json={**payload, "limit": limit}).status_code == 422
    assert db.execute_read("SELECT handoff_json FROM live_messages")[0][0] is None
