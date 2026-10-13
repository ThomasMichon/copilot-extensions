"""Admission, consumption, and bounded observation are separate contracts."""

from __future__ import annotations

import argparse
import json
import sqlite3
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.db import Database, SCHEMA_VERSION
from agent_bridge.events import EventLog
from agent_bridge.live_message_receipts import record_consumption
from agent_bridge.live_representation import LiveEventStore, await_turn_reply
from agent_bridge.models import SendMessageRequest
from agent_bridge.routes import live_sessions
from agent_bridge.session_targeting_cli import _deliver_to_live_session


@pytest.fixture
def api(tmp_db):
    app = FastAPI()
    app.state.db = tmp_db
    app.state.live_event_store = LiveEventStore()
    app.include_router(live_sessions.router)
    tmp_db.register_live_session(
        "live-1", machine=None, cwd=None, worktree_id="wt-1", repo=None,
        branch=None, pid=None, role=None, now=time.time(),
    )
    tmp_db.update_live_turn_state("live-1", turn_state="running", last_activity_at=time.time())
    return TestClient(app)


def _send(api, **extra):
    response = api.post("/api/v1/live-sessions/live-1/messages", json={
        "sender": "peer", "body": "update", "idempotency_key": "message-1", **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


def _consumed(mid, **extra):
    return {
        "type": "user.message", "id": f"event-{mid}",
        "data": {
            "source": "agent-bridge", "content": "Message from peer",
            "transformedContent": f'<agent-message from="peer" msg-id="{mid}">update</agent-message>',
        }, **extra,
    }


def test_busy_send_returns_admission_not_delivery_and_retry_preserves_id(api):
    started = time.monotonic()
    sent = _send(api)
    assert time.monotonic() - started < 1
    assert sent["receipt"]["stage"] == "admitted"
    assert not sent["replied"]
    retry = _send(api)
    assert retry["duplicate"] and retry["receipt"] == sent["receipt"]


def test_sdk_acceptance_does_not_mean_consumption(api, tmp_db):
    sent = _send(api)
    mid = sent["message_id"]
    api.post("/api/v1/live-sessions/live-1/messages/ack", json={"ids": [mid]})
    receipt = api.get(f"/api/v1/live-sessions/live-1/messages/{mid}/receipt").json()
    assert receipt["stage"] == "sdk_accepted"
    assert receipt["sdk_accepted_at"] is not None and receipt["consumed_at"] is None
    pending = api.get("/api/v1/live-sessions/wt-1/result").json()["state"]["pending_messages"]
    assert pending["value"][0]["message_id"] == mid
    assert pending["value"][0]["stage"] == "sdk_accepted"
    assert len(tmp_db.list_pending_live_messages("live-1")) == 0
    tmp_db.update_live_turn_state("live-1", turn_state="idle", last_activity_at=time.time())
    next_id, reason = tmp_db.enqueue_live_message_if_fresh(
        "live-1", sender="peer", body="competing", now=time.time(), require_idle=True,
    )
    assert next_id is None and reason == "session_busy"


def test_consumption_before_ack_is_monotonic_and_nested_or_foreign_events_do_not_count(api, tmp_db):
    mid = _send(api)["message_id"]
    for extra in ({"agentId": "child"},):
        record_consumption(tmp_db, "live-1", [_consumed(mid, **extra)])
    foreign = _consumed(mid)
    foreign["data"]["source"] = "user"
    record_consumption(tmp_db, "live-1", [foreign])
    assert tmp_db.live_message_receipt("live-1", mid)["stage"] == "admitted"
    event = _consumed(mid)
    ingested = api.post("/api/v1/live-sessions/live-1/events", json={"events": [event]})
    assert ingested.status_code == 200, ingested.text
    before = tmp_db.live_message_receipt("live-1", mid)
    assert before["stage"] == "consumed" and before["sdk_accepted_at"] is None
    assert api.get("/api/v1/live-sessions/live-1/messages").json()["messages"] == []
    tmp_db.update_live_turn_state("live-1", turn_state="idle", last_activity_at=time.time())
    api.post("/api/v1/live-sessions/live-1/messages/ack", json={"ids": [mid]})
    assert tmp_db.get_live_session("live-1")["turn_state"] == "idle"
    record_consumption(tmp_db, "live-1", [event])
    after = tmp_db.live_message_receipt("live-1", mid)
    assert after["stage"] == "consumed" and after["consumed_at"] == before["consumed_at"]
    assert _send(api)["receipt"] == after
    assert api.get("/api/v1/live-sessions/wt-1/result").json()["state"]["pending_messages"]["value"] == []


@pytest.mark.parametrize("delivery", ["queue", "steer", "interrupt"])
def test_consumed_without_ack_settles_delivery_and_idle_admission(api, tmp_db, delivery):
    mid = _send(api, delivery=delivery)["message_id"]
    assert api.get("/api/v1/live-sessions/live-1/messages").json()["messages"][0]["id"] == mid
    record_consumption(tmp_db, "live-1", [_consumed(mid)])
    receipt = tmp_db.live_message_receipt("live-1", mid)
    assert receipt["stage"] == "consumed" and receipt["sdk_accepted_at"] is None
    assert api.get("/api/v1/live-sessions/live-1/messages").json()["messages"] == []
    assert _send(api, delivery=delivery)["receipt"] == receipt
    tmp_db.update_live_turn_state("live-1", turn_state="idle", last_activity_at=time.time())
    next_id, reason = tmp_db.enqueue_live_message_if_fresh(
        "live-1", sender="peer", body="next", now=time.time(), require_idle=True,
    )
    assert next_id is not None and reason is None
    refused_id, reason = tmp_db.enqueue_live_message_if_fresh(
        "live-1", sender="peer", body="competing", now=time.time(), require_idle=True,
    )
    assert refused_id is None and reason == "session_busy"
    assert [row["id"] for row in tmp_db.list_pending_live_messages("live-1")] == [next_id]
    assert tmp_db.live_message_receipt("live-1", mid) == receipt


@pytest.mark.parametrize("retirement", ["takeover", "expired", "wedged"])
def test_consumed_without_ack_receipt_survives_pending_inbox_retirement(api, tmp_db, retirement):
    mid = _send(api)["message_id"]
    record_consumption(tmp_db, "live-1", [_consumed(mid)])
    before = tmp_db.live_message_receipt("live-1", mid)
    pending = tmp_db.enqueue_live_message("live-1", "peer", "not consumed", time.time())
    now = time.time() + 1200
    if retirement == "takeover":
        tmp_db.expire_live_sessions_for_worktree("wt-1", now=now)
    else:
        if retirement == "wedged":
            tmp_db.execute_write("UPDATE live_sessions SET status='wedged' WHERE session_id='live-1'")
        tmp_db.reap_stale_live_sessions(
            now=now, stale_seconds=600, purge_seconds=86400, pid_alive=lambda _pid: False,
        )
    assert tmp_db.live_message_receipt("live-1", mid) == before
    assert tmp_db.live_message_receipt("live-1", pending) is None
    assert tmp_db.list_pending_live_messages("live-1") == []


def test_receipt_is_session_scoped_and_inbox_read_is_bounded_cursor_neutral(api, tmp_db):
    mid = _send(api)["message_id"]
    assert api.get(f"/api/v1/live-sessions/other/messages/{mid}/receipt").status_code == 404
    for index in range(3):
        tmp_db.enqueue_live_message("live-1", "s" * 1000, "private message body", time.time(),
                                    kind="k" * 1000)
    prior = tmp_db.inspect_live_inbox("live-1", 10)
    response = api.get("/api/v1/live-sessions/wt-1/result",
                       params={"max_items": 2, "max_text_chars": 256})
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["state"]["pending_messages"]["value"]) == 2
    assert body["state"]["pending_messages"]["reason"]
    assert body["limits"]["used_text_chars"] <= 256
    assert "private message body" not in response.text
    assert tmp_db.inspect_live_inbox("live-1", 10) == prior
    assert body["state"]["at_rest"] is False
    assert body["state"]["liveness"] == "active"


def test_same_process_rename_preserves_receipt_consumption_and_idempotency(api, tmp_db):
    now = time.time()
    tmp_db.register_live_session(
        "alias-old", machine="example-host", cwd="/example", worktree_id="wt-alias", repo=None,
        branch=None, pid=77, role=None, now=now, process_started_at=now - 10,
    )
    tmp_db.execute_write("UPDATE live_sessions SET cli_mode=1 WHERE session_id='alias-old'")
    sent = api.post("/api/v1/live-sessions/alias-old/messages", json={
        "sender": "peer", "body": "update", "idempotency_key": "rename-message",
    }).json()
    mid = sent["message_id"]
    tmp_db.register_live_session(
        "alias-new", machine="example-host", cwd="/example", worktree_id="wt-alias", repo=None,
        branch=None, pid=77, role=None, now=now + 1, process_started_at=now - 10,
    )
    record_consumption(tmp_db, "alias-old", [_consumed(mid)])
    observed = api.get(f"/api/v1/live-sessions/alias-old/messages/{mid}/receipt")
    assert observed.status_code == 200, observed.text
    assert observed.json()["stage"] == "consumed"
    assert observed.json()["session_id"] == "alias-new"
    assert api.get("/api/v1/live-sessions/alias-old/messages").json()["messages"] == []
    assert tmp_db.list_pending_live_messages("alias-new") == []
    retry = api.post("/api/v1/live-sessions/alias-old/messages", json={
        "sender": "peer", "body": "update", "idempotency_key": "rename-message",
    }).json()
    assert retry["duplicate"] and retry["message_id"] == mid
    assert retry["receipt"] == observed.json()


def test_receipt_survives_database_reopen_and_v25_migration(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as conn:
        conn.executescript(
            "CREATE TABLE schema_version(version INTEGER NOT NULL);"
            "INSERT INTO schema_version VALUES(25);"
            "CREATE TABLE live_messages("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,session_id TEXT NOT NULL,"
            "sender TEXT NOT NULL,body TEXT NOT NULL,reply_to TEXT,"
            "kind TEXT NOT NULL DEFAULT 'prompt',delivery TEXT NOT NULL DEFAULT 'queue',"
            "idempotency_key TEXT,created_at REAL NOT NULL,delivered_at REAL,"
            "claimed_at REAL,outcome TEXT);"
        )
    db = Database(path)
    try:
        mid = db.enqueue_live_message("live-1", "peer", "update", time.time())
        db.ack_live_messages("live-1", [mid], time.time())
        db.consume_live_message("live-1", mid, time.time())
        before = db.live_message_receipt("live-1", mid)
        assert db.execute_read("SELECT version FROM schema_version")[0]["version"] == SCHEMA_VERSION
    finally:
        db.close()
    db = Database(path)
    try:
        assert db.live_message_receipt("live-1", mid) == before
    finally:
        db.close()


@pytest.mark.asyncio
async def test_reply_wait_ignores_unrelated_completion_nested_text_and_empty_tool_turn():
    log = EventLog()
    log.append("agent_message", {"text": "unrelated"})
    log.append("turn_complete", {"stop_reason": "end_turn"})
    log.append("user_message", {"relay_message_id": 7})
    log.append("agent_message", {"text": "nested", "agent_id": "child"})
    log.append("turn_complete", {"stop_reason": "end_turn", "agent_id": "child"})
    log.append("turn_complete", {"stop_reason": "tool_use"})
    log.append("agent_message", {"text": "observed after consumption"})
    log.append("turn_complete", {"stop_reason": "end_turn"})
    reply = await await_turn_reply(log, after=0, timeout=0.1, message_id=7)
    assert reply["replied"] and reply["reply"] == "observed after consumption"


def test_reply_timeout_does_not_change_admitted_message_or_trigger_retry(api):
    first = _send(api, wait=True, wait_timeout=0.01)
    assert not first["replied"] and first["receipt"]["stage"] == "admitted"
    retry = _send(api, wait=True, wait_timeout=10)
    assert retry["duplicate"] and retry["receipt"] == first["receipt"]
    assert retry["message_id"] == first["message_id"]


def test_retirement_during_reply_wait_preserves_admission_and_reports_lost_evidence(api, tmp_db, monkeypatch):
    async def retired(_log, **_kwargs):
        tmp_db.deregister_live_session("live-1")
        return {"replied": False, "reply": None, "stop_reason": None}

    monkeypatch.setattr(live_sessions, "await_turn_reply", retired)
    result = _send(api, wait=True, wait_timeout=0.1)
    assert result["ok"] and result["receipt"]["stage"] == "admitted"
    assert result["receipt_current"] is False
    assert result["receipt_error"] == "live-message receipt is no longer retained"


def test_retirement_between_admission_and_receipt_read_is_not_a_failed_send(api, tmp_db, monkeypatch):
    original = tmp_db.enqueue_live_message_if_fresh

    def retired(*args, **kwargs):
        admitted = original(*args, **kwargs)
        tmp_db.deregister_live_session("live-1")
        return admitted

    monkeypatch.setattr(tmp_db, "enqueue_live_message_if_fresh", retired)
    result = _send(api, wait=True, wait_timeout=10)
    assert result["ok"] and result["message_id"] > 0
    assert result["receipt"]["stage"] == "admitted"
    assert result["receipt_current"] is False and result["receipt_error"]


@pytest.mark.parametrize("timeout", [-1, 601, float("inf"), float("nan")])
def test_reply_wait_timeout_is_finite_and_bounded(timeout):
    with pytest.raises(ValueError):
        SendMessageRequest(sender="peer", body="update", wait_timeout=timeout)


@pytest.mark.parametrize("stage,delivered", [("admitted", False), ("sdk_accepted", False), ("consumed", True)])
def test_live_cli_defaults_to_receipt_and_explicit_wait_is_opt_in(monkeypatch, capsys, stage, delivered):
    class Client:
        def send_live_message(self, sid, **kwargs):
            self.wait = kwargs["wait"]
            return {"message_id": 7, "session_id": sid, "receipt": {"stage": stage}}

    client = Client()
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    args = argparse.Namespace(json=True, no_wait=False, reply_timeout=0.01)
    _deliver_to_live_session(client, args, "live-1", "update")
    assert client.wait is False
    result = json.loads(capsys.readouterr().out)
    assert result["accepted"] and result["delivered"] is delivered and result["outcome"] == stage
    args.wait_reply = True
    _deliver_to_live_session(client, args, "live-1", "update")
    assert client.wait is True


@pytest.mark.parametrize("tail", [0, 2, 100])
def test_live_read_uses_bounded_result_not_acp_cursor(monkeypatch, capsys, tail):
    class Client:
        def daemon_supports(self, _version):
            return True

        def resolve_live_result_target(self, _target):
            return {"session_id": "live-1"}

        def get_live_result_snapshot(self, target, **kwargs):
            self.bounds = kwargs
            return {"identity": {"snapshot_session_id": "live-1"}, "state": {"liveness": "active"}}

        def get_live_message_receipt(self, sid, mid):
            assert (sid, mid) == ("live-1", 7)
            return {"message_id": mid, "stage": "sdk_accepted"}

    client = Client()
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
    args = argparse.Namespace(session_id="wt-1", caller=None, json=True, tail=tail,
                              message_id=7, max_text_chars=256)
    cli._cmd_read(args)
    result = json.loads(capsys.readouterr().out)
    assert result["state"]["liveness"] == "active"
    assert result["message_receipt"]["stage"] == "sdk_accepted"
    assert client.bounds == {"max_items": tail, "max_text_chars": 256}


@pytest.mark.parametrize("name,value", [("max_items", 0), ("max_items", 101), ("max_text_chars", 255), ("max_text_chars", 20001)])
def test_live_read_bounds_are_refused_not_silently_replaced(api, name, value):
    response = api.get("/api/v1/live-sessions/wt-1/result", params={name: value})
    assert response.status_code == 422


def test_receipt_client_refuses_unsupported_daemon_without_request(monkeypatch):
    client = BridgeClient("http://127.0.0.1:1", "test")
    monkeypatch.setattr(client, "daemon_supports", lambda _version: False)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: pytest.fail("must not request"))
    with pytest.raises(BridgeClientError, match="protocol 33"):
        client.get_live_message_receipt("live-1", 7)
