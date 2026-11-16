"""Original SDK wire compatibility without fabricated installation custody."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
import shutil
import time
from typing import Any, cast

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge.db import Database
from agent_bridge.inbox_custody import InboxConsumer
from agent_bridge.loop_governance import LoopGovernance
from test_live_inbox_runtime import (
    PREFIX, attempt, current, enroll, native_enroll, poll, report, request, serving as serving,
)
from test_loop_governance_custody import installation as installation
import test_retained_sdk_recovery as recovery

Serving = tuple[FastAPI, TestClient, Database, InboxConsumer]
run_consumer = cast(
    Callable[..., tuple[int, list[dict[str, Any]], list[str]]], recovery.run_consumer,
)


def test_original_sdk_wire_works_with_genuinely_unconfigured_governance(
    serving: Serving, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, db, consumer = serving
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    app.state.loop_governance = LoopGovernance()
    app.state.live_inbox_runtime = None
    original = db.enqueue_live_message(consumer.session_id, "peer", "original", time.time())
    body = enroll(client, consumer)
    assert "receiving_api" not in body
    assert body["binding"] == {"installation": "compatible-inbox-v1", "epoch": 1}
    row = poll(client, body)
    assert row["id"] == original and row["body"] == "original"
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": True}
    assert client.post(PREFIX + "/begin", json=attempt(body, row)).json() == {"begin": False}
    assert client.post(PREFIX + "/outcome", json=report(body, row, "unknown")).json() == {
        "stage": "unknown",
    }
    successor = enroll(client, replace(consumer, incarnation="reloaded"))
    assert client.post(PREFIX + "/poll", json=current(successor)).json()["messages"] == []
    app.state.loop_governance = None
    assert client.post(PREFIX + "/outcome", json=report(body, row, "consumed")).json() == {
        "stage": "consumed",
    }
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    assert db.execute_read("SELECT * FROM bridge_runtime_producers") == []


@pytest.mark.parametrize("change", ["invalid-context", "context-added", "failed-helper", "portable"])
def test_original_sdk_compatibility_does_not_replace_configured_or_portable_admission(
    serving: Serving, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    app, client, db, consumer = serving
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    app.state.loop_governance = LoopGovernance()
    if change == "invalid-context":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
        app.state.loop_governance = LoopGovernance()
    elif change == "context-added":
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "{")
    elif change == "failed-helper":
        app.state.loop_governance = LoopGovernance(helper={"error": {"reason": "unavailable"}})
    else:
        consumer = replace(consumer, portable_launch="00000000-0000-4000-8000-000000000001")
    response = client.post(PREFIX + "/negotiate", json=request(consumer))
    assert response.status_code == 409, response.text
    assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []
    assert db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


@pytest.mark.parametrize("stage", ["negotiate", "enroll", "poll", "begin"])
def test_original_sdk_receiving_rechecks_the_registered_venue_before_effects(
    serving: Serving, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    app, client, db, consumer = serving
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    app.state.loop_governance = LoopGovernance()
    app.state.live_inbox_runtime = None
    message_id = db.enqueue_live_message(consumer.session_id, "peer", "original", time.time())
    payload: dict[str, Any] = request(consumer)
    if stage != "negotiate":
        observed = client.post(PREFIX + "/negotiate", json=payload)
        assert observed.status_code == 200, observed.text
        payload.update(observed.json())
    if stage in ("poll", "begin"):
        assert client.post(PREFIX + "/enroll", json=payload).status_code == 200
        payload = current(payload)
    if stage == "begin":
        offered = poll(client, payload)
        payload = attempt(payload, offered)
    db.execute_write("UPDATE live_sessions SET venue=? WHERE session_id=?", (
        json.dumps({"kind": "codespace", "target": "fixture-venue"}), consumer.session_id,
    ))
    assert "portable_launch" not in payload["consumer"]
    response = client.post(PREFIX + "/" + stage, json=payload)
    assert response.status_code == 409, response.text
    row, = db.execute_read(
        "SELECT handoff_started_at,delivered_at,consumed_at FROM live_messages WHERE id=?",
        (message_id,),
    )
    assert tuple(row) == (None, None, None)
    if stage in ("negotiate", "enroll"):
        assert db.execute_read("SELECT * FROM bridge_inbox_consumers") == []


def test_explicit_native_receiving_keeps_its_registered_venue_support(serving: Serving) -> None:
    app, client, db, consumer = serving
    app.state.loop_governance = None
    app.state.live_inbox_runtime = None
    db.execute_write("UPDATE live_sessions SET venue=? WHERE session_id=?", (
        json.dumps({"kind": "codespace", "target": "fixture-venue"}), consumer.session_id,
    ))
    body = native_enroll(client, consumer)
    assert body["receiving_api"] == "native-sdk-v1"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_original_sdk_process_recovery_preserves_original_receipts(
    serving: Serving, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, client, db, _ = serving
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    app.state.loop_governance = LoopGovernance()
    app.state.live_inbox_runtime = None
    config: dict[str, Any] = {
        "sid": recovery.SID, "cwd": str(tmp_path), "incarnation": "consumer-original",
        "effects": str(tmp_path / "sdk-effects.jsonl"),
        "events": str(tmp_path / "sdk-events.jsonl"), "exitAt": "sdk_accepted",
    }
    code, initial_calls, logs = run_consumer(client, config, insert=True)
    assert code == 23, logs
    assert all("receiving_api" not in call.get("body", {}) for call in initial_calls)
    original = dict(db.execute_read("SELECT * FROM live_messages")[0])
    receipt = client.get(PREFIX + f"/recovery/{original['id']}").json()
    assert receipt["stage"] == "sdk_accepted"
    effects = Path(config["effects"]).read_text(encoding="utf-8").splitlines()
    assert len(effects) == 1
    options = json.loads(effects[0])
    Path(config["events"]).write_text(json.dumps({
        "id": "native-event-a", "type": "user.message",
        "data": {"source": options["source"], "content": options["prompt"]},
    }) + "\n", encoding="utf-8")
    app.state.ready = False
    app.state.session_manager.is_draining = True
    app.state.loop_governance = None
    code, calls, logs = run_consumer(client, {
        **config, "incarnation": "consumer-reloaded", "exitAt": "", "recoveryOnly": True,
    })
    assert code == 0, logs
    recovered = client.get(PREFIX + f"/recovery/{original['id']}").json()
    assert recovered["stage"] == "consumed"
    assert recovered["handoff_id"] == receipt["handoff_id"]
    assert recovered["consumer"] == receipt["consumer"]
    assert not any(call["path"].endswith(("/enroll", "/begin")) for call in calls)
    assert Path(config["effects"]).read_text(encoding="utf-8").splitlines() == effects
