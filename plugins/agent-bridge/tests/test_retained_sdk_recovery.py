"""Controlled Node SDK + actual ASGI/SQLite across consumer process loss.

These controls verify the consumer wiring and durable effect-owner boundaries,
not native SDK execution, platform qualification or installed adoption.
"""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import threading
from pathlib import Path
from types import SimpleNamespace

from agent_procutil import no_window_kwargs
from fastapi.testclient import TestClient
import pytest

from agent_bridge.app_routes import configure_serving_app
from agent_bridge.db import Database
from agent_bridge.live_inbox_runtime import LiveInboxRuntime
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.models import ServiceConfig
from test_loop_governance_custody import installation as installation

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
SID = "native-a"
PREFIX = f"/api/v1/live-sessions/{SID}/retained-inbox"
CHILD = Path(__file__).parent / "fixtures/retained-sdk-consumer.mjs"


def run_consumer(client, config, *, insert=False, before_request=None):
    """Route one independent consumer's JSON transport into the actual ASGI app."""
    messages: queue.Queue[str | None] = queue.Queue()
    calls, logs = [], []
    inserted = False
    process = subprocess.Popen(
        ["node", str(CHILD), json.dumps(config)], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, **no_window_kwargs(),
    )

    def read_output():
        assert process.stdout is not None
        for line in process.stdout:
            messages.put(line)
        messages.put(None)

    reader = threading.Thread(target=read_output, daemon=True)
    reader.start()
    try:
        while True:
            line = messages.get(timeout=15)
            if line is None:
                break
            message = json.loads(line)
            if "log" in message:
                logs.append(message["log"])
                continue
            if message.get("done"):
                continue
            calls.append(message)
            if before_request is not None:
                before_request(message)
            response = client.request(message["method"], message["path"], json=message.get("body"))
            if insert and not inserted and message["path"] == PREFIX + "/enroll" and response.is_success:
                admitted = client.post(f"/api/v1/live-sessions/{SID}/messages", json={
                    "sender": "peer", "body": "original body", "idempotency_key": "operation-a",
                    "expected_session_id": SID, "delivery": "steer",
                })
                assert admitted.status_code == 200, admitted.text
                inserted = True
            assert process.stdin is not None
            process.stdin.write(json.dumps({
                "id": message["id"], "ok": response.is_success,
                "status": response.status_code, "body": response.json(),
            }) + "\n")
            process.stdin.flush()
        assert process.wait(timeout=10) in (0, 23)
        if insert:
            assert inserted, logs
        return process.returncode, calls, logs
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        reader.join(timeout=10)
        assert not reader.is_alive()
        for stream in (process.stdin, process.stdout):
            if stream is not None:
                stream.close()


@pytest.mark.parametrize("boundary,stage,effects", [
    ("poll", "offered", 0),
    ("begin", "sending", 0),
    ("before-sdk", "sending", 0),
    ("sdk", "sending", 1),
    ("sdk_accepted", "sdk_accepted", 1),
    ("consumed", "consumed", 1),
])
def test_original_sdk_handoff_survives_process_loss_and_closed_frontend(
    installation, tmp_db, tmp_path, boundary, stage, effects,
):
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
    config = {
        "sid": SID, "cwd": str(tmp_path), "incarnation": "consumer-original",
        "effects": str(tmp_path / "sdk-effects.jsonl"),
        "events": str(tmp_path / "sdk-events.jsonl"), "exitAt": boundary,
        "record": boundary == "consumed",
    }
    with TestClient(app, headers=headers, client=("127.0.0.1", 12345)) as client:
        code, calls, logs = run_consumer(client, config, insert=True)
        assert code == 23, logs
        original = dict(tmp_db.execute_read("SELECT * FROM live_messages")[0])
        receipt = client.get(PREFIX + f"/recovery/{original['id']}").json()
        assert receipt["stage"] == stage, logs
        assert original["body"] == "original body"
        assert receipt["consumer"]["incarnation"] == "consumer-original"
        assert not any("/messages/ack" in call["path"] or "/controls" in call["path"] for call in calls)
        effect_path = Path(config["effects"])
        saved_effects = effect_path.read_text().splitlines() if effect_path.exists() else []
        assert len(saved_effects) == effects

        governance = app.state.loop_governance
        runtime = app.state.live_inbox_runtime
        owner = governance.runtime_installation(tmp_db)
        tmp_db.close_runtime_epoch(
            owner, tmp_db.runtime_epoch(owner), transition_id="transition-a",
            target_assembly="retained-a", predecessor=runtime._producer,
        )
        tmp_db.deregister_live_session(SID)
        path = tmp_db.db_path
        tmp_db.close()
        reopened = Database(path)
        try:
            app.state.db = reopened
            app.state.session_manager.is_draining = True
            app.state.loop_governance = None
            app.state.live_inbox_runtime = None
            recovered_config = {
                **config, "incarnation": "consumer-reloaded", "exitAt": "",
                "recoveryOnly": True, "record": False,
            }
            code, recovered_calls, logs = run_consumer(client, recovered_config)
            assert code == 0, logs
            assert client.get(PREFIX + f"/recovery/{original['id']}").json() == receipt
            assert not any(call["path"].endswith(("/negotiate", "/enroll", "/poll", "/begin"))
                           for call in recovered_calls)
            assert dict(reopened.execute_read("SELECT * FROM live_messages")[0]) == original

            if effects and stage != "consumed":
                options = json.loads(saved_effects[0])
                Path(config["events"]).write_text(json.dumps({
                    "id": "native-event-a", "type": "user.message",
                    "data": {"source": options["source"], "content": options["prompt"]},
                }) + "\n", encoding="utf-8")
                code, final_calls, logs = run_consumer(client, recovered_config)
                assert code == 0, logs
                final_receipt = client.get(PREFIX + f"/recovery/{original['id']}").json()
                assert final_receipt["stage"] == "consumed"
                assert final_receipt["consumer"] == receipt["consumer"]
                assert final_receipt["handoff_id"] == receipt["handoff_id"]
                outcomes = [call for call in final_calls if call["path"].endswith("/outcome")]
                assert len(outcomes) == 1
                assert outcomes[0]["body"]["consumer"] == receipt["consumer"]
                assert outcomes[0]["body"]["outcome"] == "consumed"
            assert (effect_path.read_text().splitlines() if effect_path.exists() else []) == saved_effects
            retained = reopened.execute_read("SELECT * FROM live_messages")[0]
            for key in ("id", "body", "handoff_json", "runtime_custody_json", "handoff_started_at"):
                assert retained[key] == original[key]
        finally:
            reopened.close()


def test_same_sdk_consumer_recovers_actual_frontend_restart_without_replacing_its_cas(
    installation, tmp_db, tmp_path,
):
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
    config = {
        "sid": SID, "cwd": str(tmp_path), "incarnation": "consumer-original",
        "effects": str(tmp_path / "sdk-effects.jsonl"),
        "events": str(tmp_path / "sdk-events.jsonl"), "exitAt": "", "retry": True,
    }
    restarted = False

    def restart(message):
        nonlocal restarted
        if not restarted and message["path"].endswith("/poll"):
            restarted = True
            app.state.live_inbox_runtime = LiveInboxRuntime()
            app.state.loop_governance = LoopGovernance()

    with TestClient(app, headers=headers, client=("127.0.0.1", 12345)) as client:
        code, calls, logs = run_consumer(client, config, insert=True, before_request=restart)
        assert code == 0 and restarted, logs
        enrollments = [call["body"] for call in calls if call["path"].endswith("/enroll")]
        assert len(enrollments) == 2
        assert enrollments[1] == enrollments[0]
        assert enrollments[0]["expected_incarnation"] is None
        assert len([call for call in calls if call["path"].endswith("/negotiate")]) == 2
        assert len(Path(config["effects"]).read_text().splitlines()) == 1
        retained, = tmp_db.execute_read("SELECT * FROM live_messages")
        receipt = client.get(PREFIX + f"/recovery/{retained['id']}").json()
        assert receipt["stage"] == "sdk_accepted"
        assert receipt["consumer"] == enrollments[0]["consumer"]
