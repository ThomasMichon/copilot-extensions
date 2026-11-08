"""Real HTTP receipt waits with isolated native observations and one inbox."""

from __future__ import annotations

import threading
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.live_delivery_client import send_and_wait
from agent_bridge.live_delivery_state import DeliveryStateObservation, LiveDeliveryState, OBSERVATION_TTL
from agent_bridge.live_message_receipts import record_consumption
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.routes import live_delivery_state, live_sessions


@pytest.fixture
def delivery_api(tmp_db):
    app = FastAPI()
    app.state.db = tmp_db
    app.state.live_event_store = LiveEventStore()
    app.state.live_delivery_state = LiveDeliveryState()
    app.include_router(live_sessions.router)
    app.include_router(live_delivery_state.router)
    @app.get("/health")
    def health():
        return {"protocol_version": 52, "capabilities": {"live_delivery_observation": 1}}
    tmp_db.register_live_session(
        "live-1", machine=None, cwd=None, worktree_id="wt-1", repo=None,
        branch=None, pid=123, process_started_at=100.0, role=None, now=time.time(),
    )
    return TestClient(app)


def _publish(api, **extra):
    response = api.post("/api/v1/live-sessions/live-1/delivery-state", json={
        "pid": 123, "process_started_at": 100.0, "generation": "observer-1", **extra,
    })
    assert response.status_code == 200, response.text


def _send(api, **extra):
    response = api.post("/api/v1/live-sessions/live-1/delivery", json={
        "message": {"sender": "peer", "body": "update", "idempotency_key": "operation-1"},
        "timeout": 0.0, **extra,
    })
    assert response.status_code == 200, response.text
    return response.json()


def test_accepted_behind_question_is_explicit_not_failed_and_keyed_retry_is_one_row(delivery_api, tmp_db):
    _publish(delivery_api, pending=["question"])
    result = _send(delivery_api)
    observation = result["delivery_observation"]
    assert observation["outcome"] == "blocked"
    assert observation["attention"]["reasons"] == ["question"]
    assert observation["receipt"]["stage"] == "admitted"
    mid = result["message_id"]
    assert _send(delivery_api)["message_id"] == mid
    assert len(tmp_db.list_pending_live_messages("live-1")) == 1
    delivery_api.post("/api/v1/live-sessions/live-1/messages/ack", json={"ids": [mid]})
    assert _send(delivery_api)["delivery_observation"]["receipt"]["stage"] == "sdk_accepted"
    _publish(delivery_api, pending=[], complete=True)
    after = _send(delivery_api)["delivery_observation"]
    assert after["outcome"] == "timeout" and after["attention"]["state"] == "ready"


def test_consumption_releases_wait_without_turn_end_or_reply(delivery_api, tmp_db):
    mid = _send(delivery_api)["message_id"]
    event = {"type": "user.message", "data": {
        "source": "agent-bridge",
        "transformedContent": f'<agent-message msg-id="{mid}">update</agent-message>',
    }}
    errors = []

    def consume():
        try:
            time.sleep(0.05)
            record_consumption(tmp_db, "live-1", [event])
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=consume)
    worker.start()
    try:
        sent = _send(delivery_api, timeout=1.0)
    finally:
        worker.join(timeout=2)
    assert not errors and not worker.is_alive()
    assert sent["receipt"]["stage"] == "consumed"
    assert sent["delivery_observation"]["outcome"] == "consumed"
    assert sent["delivery_observation"]["evidence"] == "attributed_root_user_message"
    _publish(delivery_api, pending=["permission"])
    result = _send(delivery_api)["delivery_observation"]
    assert result["outcome"] == "consumed" and result["attention"]["state"] == "blocked"


def test_missing_partial_stale_or_reincarnated_observer_is_unknown(delivery_api, tmp_db, monkeypatch):
    assert _send(delivery_api)["delivery_observation"]["attention"]["state"] == "unknown"
    _publish(delivery_api)
    assert _send(delivery_api)["delivery_observation"]["attention"]["state"] == "unknown"
    _publish(delivery_api, pending=["permission"], complete=True)
    assert _send(delivery_api)["delivery_observation"]["attention"]["reasons"] == ["permission"]
    clock = time.monotonic()
    monkeypatch.setattr("agent_bridge.live_delivery_state.time.monotonic",
                        lambda: clock + OBSERVATION_TTL + 1)
    assert _send(delivery_api)["delivery_observation"]["attention"]["state"] == "unknown"
    monkeypatch.undo()
    tmp_db.execute_write(
        "UPDATE live_sessions SET process_started_at=101 WHERE session_id='live-1'"
    )
    assert _send(delivery_api)["delivery_observation"]["attention"]["state"] == "unknown"
    assert delivery_api.post("/api/v1/live-sessions/live-1/delivery-state", json={
        "pid": 123, "process_started_at": 100.0, "generation": "old",
    }).status_code == 409


@pytest.mark.parametrize("receipt_available", [False, True])
def test_receipt_loss_after_admission_cannot_be_reported_as_refused(
    delivery_api, tmp_db, monkeypatch, receipt_available,
):
    admit = live_sessions.post_live_message

    async def retire_receipt(*args, **kwargs):
        result = await admit(*args, **kwargs)
        tmp_db.execute_write("DELETE FROM live_messages WHERE id=?", (result.message_id,))
        if not receipt_available:
            result.receipt = None
        return result

    monkeypatch.setattr(live_sessions, "post_live_message", retire_receipt)
    result = _send(delivery_api)
    assert result["ok"] and result["message_id"]
    assert result["delivery_observation"]["outcome"] == "unavailable"
    assert result["receipt_current"] is False
    assert (result["receipt"] is not None) is receipt_available


@pytest.mark.parametrize("timeout", [-1, 601, "NaN"])
def test_invalid_wait_refuses_before_admission(delivery_api, tmp_db, timeout):
    response = delivery_api.post("/api/v1/live-sessions/live-1/delivery", json={
        "message": {"sender": "peer", "body": "update"}, "timeout": timeout,
    })
    assert response.status_code == 422
    assert not tmp_db.list_pending_live_messages("live-1")


@pytest.mark.parametrize("extra", [
    {"wait": True},
    {"plugin_readiness": {"owner": "bridge", "plugins": []}},
])
def test_combined_reply_or_startup_wait_is_refused_before_admission(delivery_api, tmp_db, extra):
    response = delivery_api.post("/api/v1/live-sessions/live-1/delivery", json={
        "message": {"sender": "peer", "body": "update", **extra}, "timeout": 0,
    })
    assert response.status_code == 422
    assert not tmp_db.list_pending_live_messages("live-1")


def test_current_client_refuses_unsupported_provider_before_send():
    class OldProvider:
        _base = "http://example.invalid"

        def _request(self, *args, **kwargs):
            if args[:2] == ("GET", "/health"):
                return {"protocol_version": 52, "capabilities": {}}
            pytest.fail("unsupported provider received an effect")

    with pytest.raises(BridgeClientError, match="request was not sent"):
        send_and_wait(OldProvider(), "live-1", timeout=1, sender="peer", body="update")


@pytest.mark.parametrize("value", ["nan", "-1", "601"])
def test_invalid_cli_timeout_is_parser_error_before_client_creation(value, monkeypatch):
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: pytest.fail("invalid CLI touched provider"))
    with pytest.raises(SystemExit) as exc:
        cli.main(["delivery-receipt", "live-1", "1", "--timeout", value])
    assert exc.value.code == 2


def test_observation_rejects_duplicate_categories():
    state = LiveDeliveryState()
    with pytest.raises(ValueError, match="duplicate"):
        state.publish("live-1", DeliveryStateObservation(
            pid=123, process_started_at=100, generation="g", pending=["question", "question"],
        ))


def test_real_cli_parser_send_and_existing_receipt_share_one_http_ledger(
    delivery_api, tmp_db, monkeypatch, capsys,
):
    import json

    class HttpClient(BridgeClient):
        def __init__(self):
            super().__init__("http://fixture", "fixture", connect_grace=0)

        def _request(self, method, path, body=None, *, params=None, _endpoint=None, **_kwargs):
            assert _endpoint in (None, self._base)
            response = delivery_api.request(method, path, json=body, params=params)
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            return response.json()

    client = HttpClient()
    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    monkeypatch.setattr(cli, "_live_reply_to", lambda _args: None)
    _publish(delivery_api, pending=["permission"])
    args = cli.build_parser().parse_args([
        "--json", "send", "live-1", "update", "--wait", "--wait-timeout", "0",
    ])
    args.func(args)
    sent = json.loads(capsys.readouterr().out)
    assert sent["accepted"] is True and sent["delivered"] is False
    assert sent["delivery_observation"]["outcome"] == "blocked"
    mid = sent["message_id"]
    args = cli.build_parser().parse_args(["--json", "delivery-receipt", "live-1", str(mid)])
    args.func(args)
    observed = json.loads(capsys.readouterr().out)
    assert observed["receipt"]["message_id"] == mid
    assert observed["attention"]["reasons"] == ["permission"]
    assert len(tmp_db.list_pending_live_messages("live-1")) == 1


def test_module_cli_observes_over_loopback_without_starting_service(delivery_api, tmp_path):
    from agent_procutil import direct_python, no_window_kwargs
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import json
    import os
    import subprocess
    import sys

    mid = _send(delivery_api)["message_id"]
    _publish(delivery_api, pending=["question"])

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.forward("GET")

        def do_POST(self):
            self.forward("POST")

        def forward(self, method):
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            response = delivery_api.request(method, self.path, json=json.loads(raw) if raw else None)
            self.send_response(response.status_code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(response.content)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    config = tmp_path / "cli-config"
    config.mkdir()
    (config / "auth.yaml").write_text("token: fixture-token\n", encoding="utf-8")
    interpreter, interpreter_env = direct_python(sys.executable)
    env = {
        **os.environ, **interpreter_env, "AGENT_BRIDGE_CONFIG_DIR": str(config),
        "AGENT_BRIDGE_BASE_URL": f"http://127.0.0.1:{server.server_port}",
    }
    try:
        result = subprocess.run(
            [interpreter, "-m", "agent_bridge", "--json", "delivery-receipt", "live-1", str(mid)],
            capture_output=True, text=True, timeout=15, env=env,
            **no_window_kwargs(),
        )
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    assert not worker.is_alive()
    assert result.returncode == 0, result.stderr
    observed = json.loads(result.stdout)
    assert observed["receipt"]["message_id"] == mid
    assert observed["outcome"] == "blocked" and observed["attention"]["reasons"] == ["question"]


def test_extension_pending_snapshot_and_capability_wire():
    from agent_procutil import no_window_kwargs
    from pathlib import Path
    import re
    import subprocess

    result = subprocess.run(
        ["node", "--test", "--test-reporter=tap", str(Path(__file__).with_name("delivery-state.test.mjs"))],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert re.search(r"^# pass 2$", result.stdout, re.MULTILINE)
    assert re.search(r"^# fail 0$", result.stdout, re.MULTILINE)
