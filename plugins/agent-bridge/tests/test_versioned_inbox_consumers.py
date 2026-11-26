"""Versioned producer and actual Node/ASGI consumer contracts."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
import sqlite3
import shutil
import time
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge.client import BridgeClientError
from agent_bridge.db import Database
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.live_backfill import HistoryBackfill, REPLAY_WAIT_SECONDS
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.live_delivery_client import observe, send_and_wait
from agent_bridge.send_outcome import live_outcome
from agent_bridge.message_contracts import ensure_schema
from test_live_inbox_runtime import (
    PREFIX, attempt, current, enroll, native_enroll, poll, report, serving as serving,
)
from test_loop_governance_custody import installation as installation
from test_retained_sdk_recovery import run_consumer

Serving = tuple[FastAPI, TestClient, Database, InboxConsumer]
consume: Callable[..., tuple[int, list[dict[str, Any]], list[str]]] = run_consumer


@pytest.mark.parametrize("outcome", ["sdk_accepted", "unknown"])
def test_native_interrupt_waits_for_original_consumption_before_its_one_grant(
    serving: Serving, outcome: str,
) -> None:
    _, client, db, consumer = serving
    body = native_enroll(client, replace(consumer, message_base=1))
    first = db.enqueue_live_message(consumer.session_id, "peer", "original", time.time())
    original = poll(client, body)
    assert original["id"] == first
    assert client.post(PREFIX + "/begin", json=attempt(body, original)).json() == {"begin": True}
    assert client.post(PREFIX + "/outcome", json=report(body, original, outcome)).status_code == 200
    response = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "interrupt only after original consumption", "delivery": "interrupt",
    })
    assert response.status_code == 200, response.text
    interrupted = poll(client, body)
    blocked = client.post(PREFIX + "/begin", json=attempt(body, interrupted))
    assert blocked.status_code == 409 and "no abort or replay" in blocked.text
    before = db.live_message_handoff_receipt(consumer.session_id, interrupted["id"])
    assert before is not None and before["handoff_started_at"] is None
    assert client.post(PREFIX + "/outcome", json=report(body, original, "consumed")).status_code == 200
    assert client.post(PREFIX + "/begin", json=attempt(body, interrupted)).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=attempt(body, interrupted)).json() == {"begin": False}
    consumed = db.live_message_receipt(consumer.session_id, first)
    assert consumed is not None and consumed["stage"] == "consumed"


def test_unversioned_native_poll_rejects_unsupported_interrupt_without_blocking_ordinary_input(
    serving: Serving,
) -> None:
    _, client, db, consumer = serving
    enroll(client, consumer)
    owner = native_enroll(client, replace(consumer, incarnation="unversioned-native"))
    interrupt = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "unsupported interrupt", "delivery": "interrupt",
    })
    assert interrupt.status_code == 200, interrupt.text
    interrupted_id = interrupt.json()["message_id"]
    ordinary = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "ordinary input after interrupt", "delivery": "steer",
    })
    assert ordinary.status_code == 200, ordinary.text
    for _ in range(2):
        response = client.post(PREFIX + "/poll", json=current(owner))
        assert response.status_code == 200, response.text
        assert [row["id"] for row in response.json()["messages"]] == [ordinary.json()["message_id"]]
    rejected = db.live_message_receipt(consumer.session_id, interrupted_id)
    assert rejected is not None and rejected["stage"] == "rejected"
    assert rejected["disposition"]["code"] == "unsupported_reader"
    original = db.execute_read("SELECT * FROM live_messages WHERE id=?", (interrupted_id,))[0]
    assert original["handoff_started_at"] is None and original["consumed_at"] is None


@pytest.mark.parametrize("kind,body", [
    ("control:set-mode", "autopilot"),
    ("control:set-mode-ready", '{"mode":"plan","plugin_readiness":{}}'),
])
def test_versioned_native_mode_control_retains_one_start_and_original_outcome(
    serving: Serving, kind: str, body: str,
) -> None:
    _, client, db, consumer = serving
    owner = native_enroll(client, replace(consumer, message_base=1))
    message_id = db.enqueue_live_message(consumer.session_id, "peer", body, time.time(), kind=kind)
    request = {
        **current(owner), "message_id": message_id, "state": "started",
        "code": "mode_change_started", "reason": "Original native mode control admitted.",
    }
    started = client.post(PREFIX + "/disposition", json=request)
    assert started.status_code == 200 and started.json()["begin"] is True
    duplicate = client.post(PREFIX + "/disposition", json=request)
    assert duplicate.status_code == 200 and duplicate.json()["begin"] is False
    request.update(state="unknown", code="mode_change_unknown", reason="Native mode outcome is uncertain.")
    assert client.post(PREFIX + "/disposition", json=request).status_code == 200
    assert client.post(PREFIX + "/poll", json=current(owner)).json()["messages"] == []
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["stage"] == "unknown"
    assert receipt["consumed_at"] is None and receipt["sdk_accepted_at"] is None


@pytest.mark.parametrize("operation", ["interrupt", "mode"])
def test_native_control_cannot_start_after_installation_admission_closes(
    serving: Serving, operation: str,
) -> None:
    app, client, db, consumer = serving
    enroll(client, consumer)
    owner = native_enroll(client, replace(consumer, incarnation="native-controls", message_base=1))
    message_id = db.enqueue_live_message(
        consumer.session_id, "peer", "autopilot" if operation == "mode" else "interrupt",
        time.time(), kind="control:set-mode" if operation == "mode" else "prompt",
        delivery="steer" if operation == "mode" else "interrupt",
    )
    original = poll(client, owner) if operation == "interrupt" else None
    runtime = app.state.live_inbox_runtime
    installation = app.state.loop_governance.runtime_installation(db)
    db.close_runtime_epoch(
        installation, db.runtime_epoch(installation), transition_id="close-native-controls",
        target_assembly="next", predecessor=runtime._producer,
    )
    if operation == "interrupt":
        assert original is not None
        response = client.post(PREFIX + "/begin", json=attempt(owner, original))
    else:
        response = client.post(PREFIX + "/disposition", json={
            **current(owner), "message_id": message_id, "state": "started",
            "code": "mode_change_started", "reason": "Original native mode control admitted.",
        })
    assert response.status_code == 409 and "closed" in response.text
    retained = db.execute_read("SELECT * FROM live_messages WHERE id=?", (message_id,))[0]
    assert retained["handoff_started_at"] is None and retained["disposition_json"] is None


@pytest.mark.parametrize("outcome,applied,state", [
    ("unknown", None, "in_flight"), ("completed", True, "applied"), ("rejected", False, "rejected"),
])
def test_retained_mode_result_preserves_the_original_control_outcome(
    serving: Serving, outcome: str, applied: bool | None, state: str,
) -> None:
    _, client, db, consumer = serving
    enroll(client, consumer)
    owner = native_enroll(client, replace(consumer, incarnation="native-mode-result", message_base=1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(client.post, "/api/v1/live-sessions/native-a/mode", json={
            "mode": "plan", "wait_timeout": 1,
        })
        deadline = time.monotonic() + 5
        rows: list[dict[str, Any]] = []
        while time.monotonic() < deadline:
            response = client.post(PREFIX + "/poll", json=current(owner))
            assert response.status_code == 200, response.text
            rows = response.json()["messages"]
            if rows:
                break
            if result.done():
                early = result.result()
                assert early.status_code == 200, early.text
            time.sleep(.01)
        assert len(rows) == 1
        message_id = rows[0]["id"]
        disposition = {
            **current(owner), "message_id": message_id, "state": "started",
            "code": "mode_change_started", "reason": "Original mode control started.",
        }
        if outcome != "rejected":
            assert client.post(PREFIX + "/disposition", json=disposition).status_code == 200
        disposition.update(
            state=outcome, code="mode_change_" + outcome,
            reason="Original retained mode outcome.",
        )
        response = client.post(PREFIX + "/disposition", json=disposition)
        assert response.status_code == 200, response.text
        response = result.result(timeout=5)
    assert response.status_code == 200, response.text
    assert (response.json()["applied"], response.json()["state"]) == (applied, state)
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["disposition"]["state"] == outcome


@pytest.mark.parametrize("expired,empty", [(False, False), (False, True), (True, False)])
def test_history_replay_api_acknowledges_acceptance_not_event_count(
    serving: Serving, expired: bool, empty: bool,
) -> None:
    app, client, db, consumer = serving
    now = time.time()
    store = app.state.live_event_store = LiveEventStore()
    backfill = app.state.history_backfill = HistoryBackfill(started_at=now + 1)
    row = db.get_live_session(consumer.session_id)
    assert row is not None
    assert backfill.maybe_start(db, store, row, now)
    if expired:
        backfill.release_expired(store, now + REPLAY_WAIT_SECONDS)
    events = [] if empty else [{"id": "history-one", "type": "assistant.message",
                                "data": {"content": "represented history"}}]
    route = f"/api/v1/live-sessions/{consumer.session_id}/events"
    response = client.post(route + "?replay=true", json={"events": events})
    assert response.status_code == 200
    result = response.json()
    assert result["session_id"] == consumer.session_id
    assert result["replay_accepted"] is (not expired)
    assert result["ingested"] == (0 if expired or empty else 1)
    duplicate = client.post(route + "?replay=true", json={"events": events})
    assert duplicate.status_code == 200 and duplicate.json()["replay_accepted"] is False
    assert "replay_accepted" not in client.post(route, json={"events": []}).json()


def test_history_replay_empty_batch_has_only_one_atomic_acceptance() -> None:
    store, backfill = LiveEventStore(), HistoryBackfill(started_at=2)

    class Queue:
        def enqueue_live_message_if_fresh(self, *args: Any, **kwargs: Any) -> tuple[int, None]:
            return 1, None

    assert backfill.maybe_start(Queue(), store, {"session_id": "native-a", "registered_at": 1}, now=3)

    def replay(_: int) -> tuple[int, bool]:
        return backfill.replay_with_acceptance(store, "native-a", [], worktree_id=None)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(replay, range(2)))
    assert sorted(results) == [(0, False), (0, True)]


def test_additive_schema_first_touch_serializes_concurrent_openers(tmp_path: Path) -> None:
    path = tmp_path / "legacy-inbox.db"
    with sqlite3.connect(path) as original:
        original.execute("CREATE TABLE live_messages (id INTEGER PRIMARY KEY, body TEXT)")
        original.execute("INSERT INTO live_messages VALUES (1,'original')")

    def upgrade(_: int) -> None:
        with sqlite3.connect(path) as current:
            ensure_schema(current)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(upgrade, range(2)))
    with sqlite3.connect(path) as current:
        assert current.execute("SELECT id,body,message_contract_json,disposition_json FROM live_messages").fetchall() == [
            (1, "original", None, None),
        ]


def test_required_message_semantics_refuse_an_older_provider_before_post() -> None:
    class PriorProvider:
        _base = "http://example.invalid"

        def _request(self, method: str, path: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
            if (method, path) == ("GET", "/health"):
                return {"protocol_version": 52, "capabilities": {"live_delivery_observation": 1}}
            pytest.fail("A provider lacking versioned messages received an effect")

    with pytest.raises(BridgeClientError, match="request was not sent"):
        send_and_wait(
            PriorProvider(), "native-a", timeout=1, sender="peer", body="update",
            message_version=1, message_requires=["required"],
        )


def test_receiver_rejection_never_projects_requested_urgency_as_delivery() -> None:
    receipt = {"stage": "rejected", "message_id": 7, "disposition": {
        "code": "unsupported_message", "reason": "The receiver cannot interpret this revision.",
    }}
    for delivery in ("queue", "steer", "interrupt"):
        assert live_outcome({"receipt": receipt}, delivery) == "rejected"


def test_required_message_semantics_cannot_fall_through_to_legacy_delivery(serving: Serving) -> None:
    _, client, db, consumer = serving
    response = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "sender", "body": "never dispatch", "message_version": 9,
    })
    assert response.status_code == 200, response.text
    assert response.json()["receipt"]["queue_reason"] == "awaiting_consumer"
    assert db.list_pending_live_messages(consumer.session_id) == []
    message_id = response.json()["message_id"]
    assert db.ack_live_messages(consumer.session_id, [message_id], time.time()) == 0
    assert db.consume_live_message(consumer.session_id, message_id, time.time()) == 0
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1
    baseline = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "sender", "body": "equivalent baseline", "message_version": 1,
    })
    assert baseline.status_code == 200
    assert len(db.list_pending_live_messages(consumer.session_id)) == 1


def test_terminal_control_audit_keeps_original_context_and_legacy_retention(serving: Serving) -> None:
    _, client, db, consumer = serving
    consumer = replace(consumer, message_base=1)
    body = native_enroll(client, consumer)
    message_id = db.enqueue_live_message(
        consumer.session_id, "sender", "unsupported control", time.time(), kind="control:set-mode",
    )
    rejected = client.post("/api/v1/live-sessions/native-a/retained-inbox/disposition", json={
        **current(body), "message_id": message_id, "state": "rejected",
        "code": "unsupported_message", "reason": "No retained mode mutation executor.",
    })
    assert rejected.status_code == 200
    with pytest.raises(sqlite3.IntegrityError, match="disposition_retained"):
        db.execute_write(
            "DELETE FROM live_messages WHERE id=? AND runtime_custody_json IS NULL AND handoff_json IS NULL",
            (message_id,),
        )
    with pytest.raises(sqlite3.IntegrityError, match="disposition_terminal"):
        db.execute_write("UPDATE live_messages SET disposition_json=NULL WHERE id=?", (message_id,))
    with pytest.raises(sqlite3.IntegrityError, match="disposition_not_sdk"):
        db.execute_write("UPDATE live_messages SET delivered_at=? WHERE id=?", (time.time(), message_id))
    original = db.execute_read("SELECT * FROM live_messages WHERE id=?", (message_id,))[0]
    assert original["handoff_started_at"] is None
    assert original["delivered_at"] is None and original["consumed_at"] is None
    # This is the legacy alias-fold predicate; a retained terminal control stays
    # with its exact original native context rather than following a rename.
    with pytest.raises(sqlite3.IntegrityError, match="disposition_input"):
        db.execute_write(
            "UPDATE live_messages SET session_id='successor-native' WHERE id=? AND exact_session=0 "
            "AND runtime_custody_json IS NULL AND handoff_json IS NULL", (message_id,),
        )
    db.execute_write(
        "UPDATE live_sessions SET worktree_id='stable-resource',repo='repo-a',cli_mode=1 WHERE session_id=?",
        (consumer.session_id,),
    )
    assert db.register_live_session(
        "successor-native", machine="machine-a", cwd=".", worktree_id="stable-resource",
        repo="repo-a", branch=None, pid=consumer.pid, process_started_at=consumer.process_started_at,
        role=None, now=time.time(),
    ) == "live"
    assert db.resolve_live_session_id(consumer.session_id) == "successor-native"
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["stage"] == "rejected"


@pytest.mark.parametrize("native", [False, True])
def test_prior_receiver_cannot_silently_ignore_new_required_message_semantics(
    serving: Serving, native: bool,
) -> None:
    app, client, db, consumer = serving
    body = (native_enroll if native else enroll)(client, consumer)
    options = {} if native else {"runtime_admission": app.state.live_inbox_runtime._admission}
    message_id = db.enqueue_live_message(
        consumer.session_id, "sender", "never dispatch", time.time(),
        message_requires=("future-required",), **options,
    )
    response = client.post("/api/v1/live-sessions/native-a/retained-inbox/poll", json=current(body))
    assert response.status_code == 200 and response.json()["messages"] == []
    receipt = db.live_message_receipt(consumer.session_id, message_id)
    assert receipt is not None and receipt["stage"] == "rejected"
    assert receipt["disposition"]["code"] == "unsupported_reader"
    assert receipt["sdk_accepted_at"] is None and receipt["consumed_at"] is None


def test_native_receiver_must_still_own_the_registered_worktree(serving: Serving) -> None:
    _, client, db, consumer = serving
    body = native_enroll(client, consumer)
    db.execute_write("UPDATE live_sessions SET worktree_id='stable-resource' WHERE session_id=?", (consumer.session_id,))
    db.execute_write(
        "INSERT INTO live_sessions "
        "(session_id,machine,repo,worktree_id,registered_at,updated_at,status) "
        "SELECT 'successor-native',machine,repo,worktree_id,registered_at+1,updated_at,'live' "
        "FROM live_sessions WHERE session_id=?", (consumer.session_id,),
    )
    response = client.post("/api/v1/live-sessions/native-a/retained-inbox/poll", json=current(body))
    assert response.status_code == 409
    assert "current worktree session" in response.text
    assert db.execute_read("SELECT * FROM live_messages") == []


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_versioned_sender_observes_original_receiver_rejection_without_resending(
    serving: Serving, tmp_path: Path,
) -> None:
    _, client, db, consumer = serving
    native_enroll(client, consumer)

    class LocalPeer:
        _base = "http://testserver"

        def _request(
            self, method: str, path: str, body: dict[str, Any] | None = None, **kwargs: Any,
        ) -> dict[str, Any]:
            response = client.request(method, path, json=body)
            if not response.is_success:
                raise BridgeClientError(response.status_code, response.text)
            result: dict[str, Any] = response.json()
            return result

    peer = LocalPeer()
    admitted = send_and_wait(
        peer, consumer.session_id, timeout=0, sender="original-sender", body="unsupported revision",
        message_version=9, idempotency_key="original-operation",
    )
    message_id = admitted["message_id"]
    effects, events = tmp_path / "sdk-effects.jsonl", tmp_path / "sdk-events.jsonl"
    code, _, logs = consume(client, {
        "sid": consumer.session_id, "cwd": str(tmp_path), "incarnation": "current-versioned-consumer",
        "effects": str(effects), "events": str(events), "receivingApi": "native-sdk-v1",
    })
    assert code == 0 and not effects.exists(), logs
    rejected = observe(peer, consumer.session_id, message_id)
    assert rejected["outcome"] == "rejected"
    assert rejected["receipt"]["message_id"] == message_id
    assert rejected["receipt"]["disposition"]["code"] == "unsupported_message_version"
    assert rejected["receipt"]["sdk_accepted_at"] is None
    assert rejected["receipt"]["consumed_at"] is None
    assert len(db.execute_read("SELECT * FROM live_messages")) == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("unsupported", [False, True])
def test_real_node_consumer_settles_original_versioned_rows_through_asgi(
    serving: Serving, tmp_path: Path, unsupported: bool,
) -> None:
    _, client, db, consumer = serving
    ids = [
        db.enqueue_live_message(
            consumer.session_id, "original-sender", "" if not unsupported else "never send",
            time.time(), kind="control:replay-history" if not unsupported else "future:message",
        )
        for _ in range(24 if not unsupported else 1)
    ]
    db.execute_write("UPDATE live_messages SET message_contract_json=NULL")
    effects, events, replays = (tmp_path / name for name in (
        "sdk-effects.jsonl", "sdk-events.jsonl", "history-effects.jsonl",
    ))
    code, calls, logs = consume(client, {
        "sid": consumer.session_id, "cwd": str(tmp_path), "incarnation": "current-versioned-consumer",
        "effects": str(effects), "events": str(events), "replays": str(replays), "ticks": 25,
        "receivingApi": "native-sdk-v1",
    })
    assert code == 0, logs
    assert not effects.exists(), logs
    rows = db.execute_read("SELECT * FROM live_messages ORDER BY id")
    assert [row["id"] for row in rows] == ids
    for row in rows:
        assert row["delivered_at"] is None and row["consumed_at"] is None
        receipt = db.live_message_receipt(consumer.session_id, row["id"])
        assert receipt is not None
        assert receipt["stage"] == ("rejected" if unsupported else "control_completed")
        assert receipt["disposition"]["message"]["sender"] == "original-sender"
    if unsupported:
        assert not replays.exists()
    else:
        assert len(replays.read_text().splitlines()) == 24
    assert not any(call["path"].endswith("/begin") for call in calls)
    assert db.list_pending_live_messages(consumer.session_id) == []
    assert db.execute_read(
        "SELECT id FROM live_messages WHERE delivered_at IS NULL "
        "AND ((kind LIKE 'control:%' AND claimed_at IS NULL) OR "
        "(kind NOT LIKE 'control:%' AND handoff_started_at IS NULL AND outcome IS NOT 'unknown'))"
    ) == []
