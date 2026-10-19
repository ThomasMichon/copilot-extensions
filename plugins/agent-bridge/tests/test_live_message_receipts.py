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
from agent_bridge.protocol import LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION
from agent_bridge.routes import live_sessions
from agent_bridge.send_outcome import run_send
from agent_bridge.session_targeting_cli import _deliver_to_live_session

INVALID_REPLY_TIMEOUTS = [
    -1, -1000, 601, float("inf"), float("-inf"), float("nan"),
    pytest.param(10**1000, id="overflow"),
]


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


def test_mixed_ack_retry_does_not_mark_busy_for_an_older_unconsumed_acceptance(api, tmp_db):
    older = _send(api)["message_id"]
    assert tmp_db.ack_live_messages("live-1", [older], time.time() - 10) == 1
    current = tmp_db.enqueue_live_message("live-1", "peer", "current", time.time())
    record_consumption(tmp_db, "live-1", [_consumed(current)])
    tmp_db.update_live_turn_state("live-1", turn_state="idle", last_activity_at=time.time())
    response = api.post("/api/v1/live-sessions/live-1/messages/ack",
                        json={"ids": [older, current]})
    assert response.status_code == 200, response.text
    assert response.json()["acked"] == 1
    assert tmp_db.get_live_session("live-1")["turn_state"] == "idle"
    assert tmp_db.live_message_receipt("live-1", older)["stage"] == "sdk_accepted"
    assert tmp_db.live_message_receipt("live-1", current)["stage"] == "consumed"


def test_consumption_batch_uses_one_observation_timestamp(api, tmp_db, monkeypatch):
    first = _send(api)["message_id"]
    second = tmp_db.enqueue_live_message("live-1", "peer", "second", time.time())
    calls = []

    def clock():
        calls.append(None)
        return 123.0 + len(calls)

    monkeypatch.setattr("agent_bridge.live_message_receipts.time.time", clock)
    record_consumption(tmp_db, "live-1", [_consumed(first), _consumed(second)])
    assert len(calls) == 1
    assert tmp_db.live_message_receipt("live-1", first)["consumed_at"] == 124.0
    assert tmp_db.live_message_receipt("live-1", second)["consumed_at"] == 124.0


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


@pytest.mark.parametrize("old_version", [25, 26])
def test_migration_preserves_legacy_acceptance_without_inventing_pending_consumption(tmp_path, old_version):
    path = tmp_path / "legacy.db"
    db = Database(path)
    now = time.time()
    db.register_live_session(
        "live-1", machine=None, cwd=None, worktree_id="wt-1", repo=None,
        branch=None, pid=None, role=None, now=now,
    )
    legacy = db.enqueue_live_message("live-1", "peer", "old input", now)
    db.ack_live_messages("live-1", [legacy], now)
    db.close()
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE live_messages DROP COLUMN consumption_tracked")
        if old_version == 25:
            conn.execute("ALTER TABLE live_messages DROP COLUMN consumed_at")
        conn.execute("UPDATE schema_version SET version=?", (old_version,))
    db = Database(path)
    try:
        before = db.live_message_receipt("live-1", legacy)
        assert before["stage"] == "sdk_accepted" and before["consumed_at"] is None
        assert before["sdk_accepted_at"] == now
        assert db.inspect_live_inbox("live-1", 10) == []
        db.update_live_turn_state("live-1", turn_state="idle", last_activity_at=now)
        current, reason = db.enqueue_live_message_if_fresh(
            "live-1", sender="peer", body="tracked input", now=now, require_idle=True,
        )
        assert current is not None and reason is None
        db.ack_live_messages("live-1", [current], now)
        assert [row["message_id"] for row in db.inspect_live_inbox("live-1", 10)] == [current]
        competing, reason = db.enqueue_live_message_if_fresh(
            "live-1", sender="peer", body="competing", now=now, require_idle=True,
        )
        assert competing is None and reason == "session_busy"
        assert db.live_message_receipt("live-1", legacy) == before
        assert db.consume_live_message("live-1", current, now) == 1
        assert db.inspect_live_inbox("live-1", 10) == []
    finally:
        db.close()
    db = Database(path)
    try:
        assert db.live_message_receipt("live-1", legacy) == before
        assert db.live_message_receipt("live-1", current)["stage"] == "consumed"
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


@pytest.mark.parametrize("replied", [False, True])
def test_same_process_rename_during_reply_wait_returns_current_identity(
    api: TestClient, tmp_db: Database, monkeypatch: pytest.MonkeyPatch, replied: bool,
) -> None:
    now = time.time()
    tmp_db.register_live_session(
        "live-1", machine="example-host", cwd="/example", worktree_id="wt-1", repo=None,
        branch=None, pid=77, role=None, now=now, process_started_at=now - 10,
    )
    tmp_db.execute_write("UPDATE live_sessions SET cli_mode=1 WHERE session_id='live-1'")

    async def renamed(_log: EventLog, **_kwargs: object) -> dict[str, object]:
        tmp_db.register_live_session(
            "live-renamed", machine="example-host", cwd="/example", worktree_id="wt-1",
            repo=None, branch=None, pid=77, role=None, now=now + 1,
            process_started_at=now - 10,
        )
        return {"replied": replied, "reply": "observed reply" if replied else None,
                "stop_reason": "end_turn" if replied else None}

    monkeypatch.setattr(live_sessions, "await_turn_reply", renamed)
    result = _send(api, wait=True, wait_timeout=0.1)
    assert result["session_id"] == result["receipt"]["session_id"] == "live-renamed"
    assert result["receipt_current"] and result["replied"] is replied
    retry = _send(api, wait=True)
    assert retry["duplicate"] and retry["message_id"] == result["message_id"]
    assert retry["session_id"] == "live-renamed"


def test_retirement_during_reply_wait_preserves_admission_and_reports_lost_evidence(api, tmp_db, monkeypatch):
    async def retired(_log, **_kwargs):
        tmp_db.deregister_live_session("live-1")
        return {"replied": False, "reply": None, "stop_reason": None}

    monkeypatch.setattr(live_sessions, "await_turn_reply", retired)
    result = _send(api, wait=True, wait_timeout=0.1)
    assert result["ok"] and result["receipt"]["stage"] == "admitted"
    assert result["receipt_current"] is False
    assert result["receipt_error"] == "live-message receipt is no longer retained"


def test_retirement_between_admission_and_receipt_read_is_not_a_failed_send(
    api: TestClient, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
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
    assert api.get(f"/api/v1/live-sessions/live-1/messages/{result['message_id']}/receipt").status_code == 404

    class Client:
        def send_live_message(self, _session_id: str, **_kwargs: object) -> dict[str, object]:
            return result

    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    _deliver_to_live_session(
        Client(), argparse.Namespace(json=False, no_wait=True), "live-1", "update",
    )
    output = capsys.readouterr()
    assert "receipt: admitted" in output.out
    assert "observe: agent-bridge read live-1 --json" in output.out
    assert "--message-id" not in output.out
    assert "receipt observation is unavailable" in output.out
    assert "receipt is no longer retained" in output.err


@pytest.mark.parametrize("timeout", [-1, 601, float("inf"), float("nan")])
def test_reply_wait_timeout_is_finite_and_bounded(timeout):
    with pytest.raises(ValueError):
        SendMessageRequest(sender="peer", body="update", wait_timeout=timeout)


@pytest.mark.parametrize("timeout", INVALID_REPLY_TIMEOUTS)
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_invalid_reply_wait_refuses_before_delivery(monkeypatch, capsys, timeout, as_json):
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: pytest.fail("must not prepare delivery"))
    args = argparse.Namespace(
        json=as_json, target="live-1", no_wait=False, wait_reply=True, reply_timeout=timeout,
    )
    with pytest.raises(SystemExit) as error:
        run_send(args, lambda actual: _deliver_to_live_session(None, actual, "live-1", "update"))
    assert error.value.code == 2
    output = capsys.readouterr()
    assert "--reply-timeout must be a finite number of seconds between 0 and 600" in output.err
    if as_json:
        refusal = json.loads(output.out)
        assert refusal["outcome"] == "refused_invalid"
        assert refusal["reason"] == "reply_timeout" and refusal["retryable"] is False
        assert refusal["target"] == "live-1"
    else:
        assert output.out == ""


@pytest.mark.parametrize("timeout", INVALID_REPLY_TIMEOUTS)
def test_client_invalid_reply_wait_makes_no_transport_request(monkeypatch, timeout):
    client = BridgeClient("http://127.0.0.1:1", "test")
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: pytest.fail("must not request"))
    with pytest.raises(BridgeClientError, match="finite number of seconds") as error:
        client.send_live_message("live-1", sender="peer", body="update", wait=True, wait_timeout=timeout)
    assert error.value.status == 422


@pytest.mark.parametrize("timeout,effective", [(None, 120.0), (0.0, 0.0), (600.0, 600.0)])
def test_client_reply_wait_uses_validated_bound_plus_transport_margin(monkeypatch, timeout, effective):
    client = BridgeClient("http://127.0.0.1:1", "test")
    monkeypatch.setattr(client, "daemon_supports", lambda version, **_kwargs: version == LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION)
    calls = []

    def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"protocol_version": LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION} if args[:2] == ("GET", "/health") else {}

    monkeypatch.setattr(client, "_request", request)
    client.send_live_message("live-1", sender="peer", body="update", wait=True, wait_timeout=timeout)
    request, options = calls[-1]
    assert [args[:2] for args, _kwargs in calls] == [
        ("GET", "/health"), ("POST", "/api/v1/live-sessions/live-1/messages"),
    ]
    assert all(options["allow_retry"] is False for _args, options in calls)
    assert options["request_timeout"] == effective + 15
    assert request[2]["wait"] is True
    if timeout is None:
        assert "wait_timeout" not in request[2]
    else:
        assert request[2]["wait_timeout"] == effective


def test_receipt_only_send_does_not_use_ignored_reply_timeout(monkeypatch, capsys):
    client = BridgeClient("http://127.0.0.1:1", "test")
    calls = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: calls.append((args, kwargs)) or {
        "message_id": 7, "receipt": {"stage": "admitted"},
    })
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    args = argparse.Namespace(json=True, no_wait=False, wait_reply=False, reply_timeout=float("nan"))
    _deliver_to_live_session(client, args, "live-1", "update")
    assert calls[0][1]["request_timeout"] is None
    assert "wait_timeout" not in calls[0][0][2]
    assert json.loads(capsys.readouterr().out)["outcome"] == "admitted"


@pytest.mark.parametrize("protocol", sorted({
    0, 32, 33, LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION - 1,
    LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION,
}))
@pytest.mark.parametrize("wait", [False, True])
def test_reply_wait_requires_receipt_protocol_before_message_submission(
    monkeypatch: pytest.MonkeyPatch, protocol: int, wait: bool,
) -> None:
    client = BridgeClient("http://127.0.0.1:1", "test")
    calls: list[tuple[str, str]] = []

    def request(method: str, path: str, *args: object, **kwargs: object) -> dict[str, object]:
        calls.append((method, path))
        if path == "/health":
            return {"protocol_version": protocol, "min_protocol_version": 0}
        assert method == "POST"
        return {"message_id": 7, "replied": False}

    monkeypatch.setattr(client, "_request", request)
    if wait and protocol < LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION:
        with pytest.raises(
            BridgeClientError,
            match=f"reply waits require daemon protocol {LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION}",
        ) as error:
            client.send_live_message("live-1", sender="peer", body="update",
                                     wait=wait, wait_timeout=0)
        assert error.value.status == 426 and calls == [("GET", "/health")]
    else:
        result = client.send_live_message("live-1", sender="peer", body="update",
                                          wait=wait, wait_timeout=0)
        assert result["message_id"] == 7
        assert calls == ([("GET", "/health")] * 2 if wait else []) + [
            ("POST", "/api/v1/live-sessions/live-1/messages"),
        ]


@pytest.mark.parametrize("as_json", [False, True])
def test_cli_legacy_reply_wait_reports_typed_refusal_without_sending(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], as_json: bool,
) -> None:
    client = BridgeClient("http://127.0.0.1:1", "test")

    def request(method: str, path: str, *args: object, **kwargs: object) -> dict[str, object]:
        assert (method, path) == ("GET", "/health"), "unsupported wait must not submit a message"
        return {"protocol_version": 32, "min_protocol_version": 0}

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    args = argparse.Namespace(
        json=as_json, target="live-1", no_wait=False, wait_reply=True, reply_timeout=0,
    )
    with pytest.raises(SystemExit) as error:
        run_send(args, lambda actual: _deliver_to_live_session(client, actual, "live-1", "update"))
    assert error.value.code == 69
    output = capsys.readouterr()
    assert f"require daemon protocol {LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION}" in output.err
    if as_json:
        result = json.loads(output.out)
        assert result["outcome"] == "refused_unavailable"
        assert result["reason"] == "unsupported_protocol" and result["retryable"] is False
        assert result["target"] == "live-1"
    else:
        assert output.out == ""


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


@pytest.mark.parametrize("receipt", [None, {}, {"stage": None}])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_missing_receipt_stage_does_not_invent_admission(monkeypatch, capsys, receipt, as_json):
    class Client:
        def send_live_message(self, sid, **kwargs):
            result = {"message_id": 7, "session_id": sid}
            if receipt is not None:
                result["receipt"] = receipt
            return result

    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    args = argparse.Namespace(json=as_json, no_wait=False)
    _deliver_to_live_session(Client(), args, "live-1", "update")
    output = capsys.readouterr().out
    if as_json:
        result = json.loads(output)
        assert result["accepted"] and result["delivered"] is None
        assert result["outcome"] != "admitted"
    else:
        assert "receipt: unavailable (delivery evidence unavailable)" in output
        assert "receipt: admitted" not in output
        assert "observe: agent-bridge read live-1 --json" in output
        assert "--message-id" not in output
        assert f"receipt observation requires daemon protocol {LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION} or newer" in output


@pytest.mark.parametrize("stage", ["admitted", "sdk_accepted", "consumed"])
@pytest.mark.parametrize("current", [None, False, True])
def test_cli_observation_hint_uses_message_id_only_for_current_receipt_evidence(
    monkeypatch, capsys, stage, current,
):
    class Client:
        def send_live_message(self, sid, **kwargs):
            return {"message_id": 7, "session_id": sid, "receipt": {"stage": stage},
                    "receipt_current": current}

    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    _deliver_to_live_session(Client(), argparse.Namespace(json=False, no_wait=False), "live-1", "update")
    output = capsys.readouterr().out
    if current:
        assert "observe: agent-bridge read live-1 --message-id 7 --json" in output
        assert "receipt observation is unavailable" not in output
    else:
        assert "observe: agent-bridge read live-1 --json" in output
        assert "--message-id" not in output
        assert "receipt observation is unavailable" in output
    assert "receipt observation requires" not in output


@pytest.mark.parametrize("duplicate", [False, True])
@pytest.mark.parametrize("as_json", [False, True])
def test_cli_wait_output_distinguishes_duplicate_and_uses_current_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    duplicate: bool, as_json: bool,
) -> None:
    client = BridgeClient("http://127.0.0.1:1", "test")
    monkeypatch.setattr(client, "daemon_supports", lambda version, **_kwargs: version == LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION)
    monkeypatch.setattr(client, "_request", lambda method, path, *_args, **_kwargs: (
        {"protocol_version": LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION} if (method, path) == ("GET", "/health") else {
            "message_id": 7, "session_id": "live-renamed", "duplicate": duplicate,
            "receipt": {"stage": "admitted"}, "receipt_current": True, "replied": False,
        }
    ))
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    args = argparse.Namespace(
        json=as_json, no_wait=False, wait_reply=True, reply_timeout=120.0,
        idempotency_key="message-1",
    )
    _deliver_to_live_session(client, args, "live-1", "update")
    output = capsys.readouterr().out
    if as_json:
        result = json.loads(output)
        assert result["duplicate"] is duplicate and result["accepted"]
        assert result["target"] == result["session_id"] == "live-renamed"
    else:
        assert "Accepted by live session inbox live-renamed" in output
        assert "observe: agent-bridge read live-renamed --message-id 7 --json" in output
        assert ("No reply within 120s" in output) is not duplicate
        assert ("no additional reply wait was started" in output) is duplicate


@pytest.mark.parametrize("tail", [0, 2, 100])
def test_live_read_uses_bounded_result_not_acp_cursor(monkeypatch, capsys, tail):
    class Client:
        def daemon_supports(self, _version):
            return True

        def resolve_live_result_target(self, _target):
            return {"session_id": "live-1"}

        def get_live_result_snapshot(self, target, **kwargs):
            assert target == "live-1"
            self.bounds = kwargs
            return {"identity": {"snapshot_session_id": "live-1"}, "state": {"liveness": "active"}}

        def get_live_message_receipt(self, sid, mid):
            assert (sid, mid) == ("live-1", 7)
            return {"message_id": mid, "stage": "sdk_accepted"}

    client = Client()
    def read_client(*, ensure: bool = True) -> Client:
        assert ensure is False
        return client

    monkeypatch.setattr(cli, "_get_client", read_client)
    args = argparse.Namespace(session_id="wt-1", caller=None, json=True, tail=tail,
                              message_id=7, max_text_chars=256)
    cli._cmd_read(args)
    result = json.loads(capsys.readouterr().out)
    assert result["state"]["liveness"] == "active"
    assert result["message_receipt"]["stage"] == "sdk_accepted"
    assert client.bounds == {"max_items": tail, "max_text_chars": 256}


@pytest.mark.parametrize("with_receipt", [False, True])
def test_live_read_pins_snapshot_and_receipt_across_worktree_rollover(
    api: TestClient, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str], with_receipt: bool,
) -> None:
    mid = _send(api)["message_id"]
    client = BridgeClient("http://127.0.0.1:1", "test")
    monkeypatch.setattr(client, "daemon_supports", lambda _version, **_kwargs: True)

    def request(
        method: str, path: str, *, params: dict[str, object] | None = None,
        allow_retry: bool = True,
    ) -> dict[str, object]:
        assert not allow_retry
        if path == "/health":
            return {"protocol_version": LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION}
        response = api.request(method, path, params=params)
        assert response.status_code == 200, response.text
        payload = response.json()
        if path == "/api/v1/live-sessions/result-target":
            assert payload["session_id"] == "live-1"
            tmp_db.register_live_session(
                "live-successor", machine=None, cwd=None, worktree_id="wt-1", repo=None,
                branch=None, pid=None, role=None, now=time.time() + 1,
            )
            resolved = api.get("/api/v1/live-sessions/result-target", params={"handle": "wt-1"})
            assert resolved.json()["session_id"] == "live-successor"
        return payload

    monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
    cli._cmd_read(argparse.Namespace(
        session_id="wt-1", caller=None, json=True, message_id=mid if with_receipt else None,
    ))
    result = json.loads(capsys.readouterr().out)
    assert result["identity"]["snapshot_session_id"] == "live-1"
    if with_receipt:
        assert result["message_receipt"]["session_id"] == "live-1"
        assert result["message_receipt"]["message_id"] == mid
    else:
        assert "message_receipt" not in result


@pytest.mark.parametrize("message_id", [None, 7])
def test_stopped_daemon_read_reports_unavailable_without_starting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], message_id: int | None,
) -> None:
    import socket

    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    client = BridgeClient(f"http://127.0.0.1:{port}", "test", connect_grace=0)
    starts = []
    monkeypatch.setattr(BridgeClient, "from_config", classmethod(lambda _cls: client))
    monkeypatch.setattr(cli, "_ensure_daemon", lambda: starts.append("started"))
    args = argparse.Namespace(session_id="live-1", caller=None, json=True, message_id=message_id)
    with pytest.raises(SystemExit) as refused:
        cli._cmd_read(args)
    assert refused.value.code == 1
    assert starts == []
    assert "Read evidence unavailable; the daemon was not started" in capsys.readouterr().err


@pytest.mark.parametrize("receipt_current", [False, True])
def test_remote_observation_hint_names_hosting_machine(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], receipt_current: bool,
) -> None:
    from agent_bridge import live_peer_address

    result = {
        "session_id": "hosting-native-id", "message_id": 7,
        "receipt": {"stage": "admitted"}, "receipt_current": receipt_current,
    }
    monkeypatch.setattr(live_peer_address, "send_remote_peer", lambda *_a, **_k: result)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    _deliver_to_live_session(
        object(), argparse.Namespace(json=False), "live:host-b:project-a:wt-b", "update",
        remote_address=live_peer_address.LivePeerAddress("host-b", "project-a", "wt-b"),
    )
    output = capsys.readouterr().out
    assert "observe on hosting machine host-b: agent-bridge read hosting-native-id" in output
    assert "observe: agent-bridge read hosting-native-id" not in output
    assert ("--message-id 7" in output) is receipt_current


@pytest.mark.parametrize("name,value", [("max_items", 0), ("max_items", 101), ("max_text_chars", 255), ("max_text_chars", 20001)])
def test_live_read_bounds_are_refused_not_silently_replaced(api, name, value):
    response = api.get("/api/v1/live-sessions/wt-1/result", params={name: value})
    assert response.status_code == 422


def test_receipt_client_refuses_unsupported_daemon_without_request(monkeypatch):
    client = BridgeClient("http://127.0.0.1:1", "test")
    monkeypatch.setattr(client, "daemon_supports", lambda _version, **_kwargs: False)
    monkeypatch.setattr(client, "_request", lambda *_args, **_kwargs: pytest.fail("must not request"))
    with pytest.raises(BridgeClientError, match=f"protocol {LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION}"):
        client.get_live_message_receipt("live-1", 7)
