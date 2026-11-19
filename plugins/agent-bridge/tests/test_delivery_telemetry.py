"""Committed inbox transitions and content-free extension diagnostic ingress."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest

from agent_bridge import delivery_telemetry, status_producers, telemetry
from test_live_inbox_runtime import (
    PREFIX, Serving, attempt, enroll, installation as installation, native_enroll,
    poll, report, serving as serving,
)


def _wait_for_start(event: Event) -> None:
    event.wait(5)


@pytest.mark.parametrize("native", [False, True])
def test_delivery_events_observe_committed_rows_without_duplicate_begin(
    serving: Serving, native: bool,
) -> None:
    _, client, db, consumer = serving
    body = (native_enroll if native else enroll)(client, consumer)
    seen: list[dict[str, Any]] = []
    completed = Event()

    def sink(event: dict[str, Any]) -> None:
        if event.get("name") != "inbox_delivery":
            return
        with sqlite3.connect(db.db_path, timeout=0.1) as reader:
            row = reader.execute(
                "SELECT handoff_started_at,delivered_at,consumed_at FROM live_messages WHERE id=?",
                (event["message_id"],),
            ).fetchone()
        assert row is not None
        field = {"handoff_started": 0, "sdk_accepted": 1, "consumed": 2}.get(event["event"])
        if field is not None:
            assert row[field] is not None
        seen.append(event)
        if len(seen) == 4:
            completed.set()

    writer = telemetry.set_buffered_telemetry_sink(sink)
    try:
        reply = client.post("/api/v1/live-sessions/native-a/messages", json={
            "sender": "peer", "body": "private-message-sentinel", "delivery": "queue",
            "idempotency_key": "original-operation",
        })
        assert reply.status_code == 200, reply.text
        row = poll(client, body)
        assert client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
        assert not client.post(PREFIX + "/begin", json=attempt(body, row)).json()["begin"]
        for outcome in ("sdk_accepted", "consumed"):
            result = client.post(PREFIX + "/outcome", json=report(body, row, outcome))
            assert result.status_code == 200, result.text
        assert completed.wait(5)
        assert [event["event"] for event in seen] == [
            "admitted", "handoff_started", "sdk_accepted", "consumed",
        ]
        assert all(event["message_id"] == reply.json()["message_id"] for event in seen)
        assert all(event["consumer_incarnation"] == consumer.incarnation for event in seen[1:])
        assert all(event["handoff_id"] == row["handoff_id"] for event in seen[1:])
        assert "private-message-sentinel" not in json.dumps(seen)
    finally:
        telemetry.clear_telemetry_sink()
        assert writer.join(5)


@pytest.mark.parametrize("native", [False, True])
def test_takeover_stages_keep_the_original_handoff_consumer(
    serving: Serving, native: bool,
) -> None:
    _, client, db, consumer = serving
    consumer = replace(consumer, message_base=1)
    enroll_receiver = native_enroll if native else enroll
    original_body = enroll_receiver(client, consumer)
    reply = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "original-input", "delivery": "queue",
        "idempotency_key": "takeover-operation",
    })
    assert reply.status_code == 200, reply.text
    original = poll(client, original_body)
    successor = replace(consumer, incarnation="successor")
    current_body = enroll_receiver(client, successor)
    offered = poll(client, current_body)
    assert offered["handoff_id"] == original["handoff_id"]
    assert current_body["consumer"]["incarnation"] == successor.incarnation
    seen: list[dict[str, Any]] = []
    completed = Event()

    def capture(event: dict[str, Any]) -> None:
        seen.append(event)
        if len(seen) == 3:
            completed.set()

    writer = telemetry.set_buffered_telemetry_sink(capture)
    try:
        result = client.post(PREFIX + "/begin", json=attempt(current_body, offered))
        assert result.status_code == 200 and result.json()["begin"]
        for outcome in ("sdk_accepted", "consumed"):
            result = client.post(PREFIX + "/outcome", json=report(original_body, original, outcome))
            assert result.status_code == 200, result.text
        assert completed.wait(5)
        stages = [event for event in seen if event.get("name") == "inbox_delivery"]
        assert [event["event"] for event in stages] == ["handoff_started", "sdk_accepted", "consumed"]
        receipt = db.live_message_handoff_receipt(consumer.session_id, original["id"])
        assert receipt is not None
        assert receipt["consumer"]["incarnation"] == consumer.incarnation != successor.incarnation
        assert all(event["consumer_incarnation"] == consumer.incarnation for event in stages)
        assert all(event["handoff_id"] == original["handoff_id"] for event in stages)
    finally:
        telemetry.clear_telemetry_sink()
        assert writer.join(5)


def test_represented_diagnostics_are_redacted_and_never_status_or_receipt_evidence(
    serving: Serving, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, db, _ = serving
    seen: list[dict[str, Any]] = []
    arrived, start = Event(), Event()

    def capture(event: dict[str, Any]) -> None:
        seen.append(event)
        arrived.set()

    writer = telemetry.set_buffered_telemetry_sink(capture, on_start=lambda: _wait_for_start(start))
    monkeypatch.setattr(status_producers, "normalized", lambda *a, **k: pytest.fail("status mutation"))
    try:
        data = {
            "stage": "match_failed", "source": "retained-sdk-recovery", "session_id": "native-a",
            "message_id": 12, "handoff_id": "handoff-a", "consumer_incarnation": "consumer-a",
            "event_count": 3, "failures": {"header_mismatch": 2, "not_root_input": 1,
                                         "private-body-sentinel": 99},
            "body": "private-body-sentinel", "error": "private-error-sentinel",
        }
        status_producers.represented(db, "native-a", [{
            "type": "bridge.delivery_observation", "data": data,
        }])
        assert writer.stats()["accepted"] == 1, json.dumps(writer.stats())
        start.set()
        assert arrived.wait(5), writer.stats()
        assert seen[0]["failures"] == {"header_mismatch": 2, "not_root_input": 1}
        assert "private-" not in json.dumps(seen)
        assert db.execute_read("SELECT id FROM live_messages") == []
        changes: tuple[dict[str, object], ...] = (
            {"session_id": "foreign"}, {"stage": "consumed"}, {"stage": []}, {"source": {}},
        )
        for changed in changes:
            delivery_telemetry.represented("native-a", {**data, **changed})
        for marker in ({"agentId": "child"}, {"parentToolCallId": "child-call"}):
            status_producers.represented(db, "native-a", [{
                "type": "bridge.delivery_observation", "data": {**data, **marker},
            }])
        status_producers.represented(db, "native-a", [{
            "type": "bridge.delivery_observation", "agentId": "child", "data": data,
        }])
        assert writer.stats()["accepted"] == 1 and len(seen) == 1
        arrived.clear()
        delivery_telemetry.represented("native-a", {
            "stage": "hook_notice", "source": "pending-input-set-v1", "session_id": "native-a",
            "pending_count": 3, "incomplete": False, "delivery_path": "retained",
            "body": "private-body-sentinel",
        })
        assert arrived.wait(5)
        assert seen[-1]["pending_count"] == 3 and seen[-1]["source"] == "pending-input-set-v1"
        assert seen[-1]["delivery_path"] == "retained"
    finally:
        start.set()
        telemetry.clear_telemetry_sink()
        assert writer.join(5)


def test_disabled_and_failing_telemetry_do_not_change_inbox_behavior(
    serving: Serving, monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, _, db, _ = serving
    telemetry.clear_telemetry_sink()
    monkeypatch.setattr(db, "live_message_receipt", lambda *a:
        pytest.fail("disabled diagnostics read receipts"),
    )
    monkeypatch.setattr(db, "live_message_handoff_receipt", lambda *a:
        pytest.fail("disabled diagnostics read handoffs"),
    )
    delivery_telemetry.committed(db, "native-a", 1, "admitted")
    delivery_telemetry.committed(db, "native-a", 1, "handoff_started")

    def broken_sink(event: dict[str, Any]) -> None:
        raise RuntimeError("sink unavailable")

    telemetry.set_telemetry_sink(broken_sink)
    try:
        delivery_telemetry.committed(db, "native-a", 1, "handoff_started")
    finally:
        telemetry.clear_telemetry_sink()
    entered, start = Event(), Event()

    def buffered_failure(event: dict[str, Any]) -> None:
        entered.set()
        raise RuntimeError("sink unavailable")

    writer = telemetry.set_buffered_telemetry_sink(
        buffered_failure, on_start=lambda: _wait_for_start(start),
    )
    try:
        delivery_telemetry.publish("unknown", "native-a", source="backend-receipt",
                                   fields={"message_id": 1})
        assert writer.stats()["accepted"] == 1, json.dumps(writer.stats())
        start.set()
        assert entered.wait(5), writer.stats()
        def unavailable(*args: object) -> None:
            raise RuntimeError("receipt unavailable")

        monkeypatch.setattr(db, "live_message_handoff_receipt", unavailable)
        delivery_telemetry.committed(db, "native-a", 1, "handoff_started")
        assert db.execute_read("SELECT id FROM live_messages") == []
    finally:
        start.set()
        telemetry.clear_telemetry_sink()
        assert writer.join(5)
    assert writer.stats()["sink_failed"] == 1


@pytest.mark.parametrize("mode", ["synchronous", "full"])
def test_slow_or_full_sink_cannot_delay_a_committed_begin(serving: Serving, mode: str) -> None:
    _, client, db, consumer = serving
    body = enroll(client, consumer)
    reply = client.post("/api/v1/live-sessions/native-a/messages", json={
        "sender": "peer", "body": "original-input", "delivery": "queue",
        "idempotency_key": "slow-sink-operation",
    })
    assert reply.status_code == 200, reply.text
    offered = poll(client, body)
    entered, release, start = Event(), Event(), Event()
    writer = None

    def blocked_sink(event: dict[str, Any]) -> None:
        entered.set()
        release.wait(5)

    try:
        if mode == "synchronous":
            telemetry.set_telemetry_sink(blocked_sink)
        else:
            writer = telemetry.set_buffered_telemetry_sink(
                blocked_sink, capacity=1, batch_size=1, on_start=lambda: _wait_for_start(start),
            )
            telemetry.emit({"kind": "event", "name": "occupy"})
            assert writer.stats()["accepted"] == 1, json.dumps(writer.stats())
            start.set()
            assert entered.wait(5), writer.stats()
            telemetry.emit({"kind": "event", "name": "queued"})
        with ThreadPoolExecutor(max_workers=1) as pool:
            try:
                pending = pool.submit(client.post, PREFIX + "/begin", json=attempt(body, offered))
                result = pending.result(timeout=3)
                assert result.status_code == 200 and result.json()["begin"]
                receipt = db.live_message_handoff_receipt(consumer.session_id, offered["id"])
                assert receipt is not None and receipt["stage"] == "sending"
                if writer is None:
                    assert not entered.is_set()
                else:
                    assert writer.stats()["full"] >= 1
            finally:
                release.set()
    finally:
        start.set()
        release.set()
        telemetry.clear_telemetry_sink()
        if writer is not None:
            assert writer.join(5)
