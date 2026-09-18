"""Registry lookup diagnostics use real HTTP conversion and native status routing."""

import asyncio
from http.client import IncompleteRead
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shlex
import socket
import threading
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.models import LiveSessionInfo
from agent_bridge.native_manager import NativeManager
from agent_bridge.native_runtime import NativeRuntime
from agent_bridge.native_store import NativeError
from agent_bridge.routes.native import router
from ssh_manager.native_channel import serve


def assert_captured_receipt(value, case):
    path = Path(__file__).parents[1] / "contract/fixtures/http/current/native-registry-status.json"
    expected = json.loads(path.read_text())["cases"][case]["public_receipt"]
    assert {**value, "owner": "/fixture-owner"} == expected


@pytest.fixture
def registry():
    token = "registry-fixture-token"
    secret = f"fixture-response-body {token} http://registry.invalid/private FIXTURE_ENV=private"
    state = {"mode": "fresh", "requests": []}

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            state["requests"].append((self.path, self.headers.get("Authorization")))
            mode = state["mode"]
            if mode == "disconnect":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.close_connection = True
                return
            status = {"absent": 404, "auth": 401, "server": 503}.get(mode, 200)
            stamp = time.time() - (3600 if mode == "stale" else 0)
            body = json.dumps(
                {"detail": secret} if status != 200 else
                LiveSessionInfo(
                    session_id="session-example", status="expired" if mode == "nonlive" else "live",
                    registered_at=stamp, updated_at=stamp,
                ).model_dump(mode="json")
            ).encode()
            if mode == "json":
                body = secret.encode()
            elif mode == "encoding":
                body = b"\xff" + secret.encode()
            elif mode == "truncated":
                body = b'{"partial":"' + secret.encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body) + (100 if mode == "truncated" else 0)))
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": .01}, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    state.update(client=BridgeClient(url, token, connect_grace=0), secrets=[secret, token, url, "FIXTURE_ENV"])
    try:
        yield state
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        assert not thread.is_alive()


async def provider_status(read_status, *, overrides=None, exit_code=0, method="status"):
    """Use the real shared channel, replacing only remote command execution."""
    loop = asyncio.get_running_loop()
    answered = asyncio.Event()
    frames = []

    class Output:
        def write(self, raw):
            value = json.loads(raw)
            if value.get("id") == "request":
                frames.append(value)
                loop.call_soon_threadsafe(answered.set)

        def flush(self):
            pass

    class Input:
        sent = False

        async def next(self):
            if self.sent:
                await asyncio.wait_for(answered.wait(), 2)
                return None
            self.sent = True
            return json.dumps({
                "id": "request", "method": method,
                "executionId": "execution-example", "generation": "generation-example",
            }).encode()

    async def execute(name, command, **kwargs):
        words = shlex.split(shlex.split(command)[2])
        assert words[:2] == ["agent-bridge", "native-host"]
        if words[2] == "capabilities":
            value = {"capability": "codespace-native-host-v1", "supported": True}
            code = 0
        else:
            assert words[2] == method
            value = {**read_status(), **(overrides or {})}
            code = exit_code
        return SimpleNamespace(exit_code=code, stdout=json.dumps(value))

    async def no_retirement(_):
        pytest.fail("status diagnostic must not retire an execution")

    args = SimpleNamespace(
        name="example-venue", effort="fixture-owner",
        execution_id="execution-example", generation="generation-example",
        retirement_only=True, native_input=Input(), local_forward=[], reverse_forward=[],
    )
    await serve(
        args, SimpleNamespace(exec_command=execute), object(), "",
        require_owner=lambda: None, mark_launch=lambda: pytest.fail("status must not launch"),
        retire=no_retirement, output=Output(),
    )
    return frames[0]


@pytest.fixture
def native_status(tmp_path, monkeypatch, registry):
    runtime = NativeRuntime(
        tmp_path / "runtime", catalog=tmp_path / "catalog",
        launch=lambda *a, **k: pytest.fail("status must not launch"),
    )
    identity = ("execution-example", "generation-example")
    runtime.store.reserve(*identity, "request-example", "example-venue", str(tmp_path), "hash", {
        "hostNonce": "fixture-host-nonce", "sessionId": "session-example",
    })
    runtime.catalog.mkdir()
    runtime._state_path(identity[0]).write_text(json.dumps({
        "mode": "native", "session_id": identity[0], "execution_generation": identity[1],
        "nonce": "fixture-host-nonce", "host_pid": 1234, "child_pid": 1235, "port": 12345,
        "host_start_ticks": "host-ticks", "child_start_ticks": "child-ticks",
        "boot_id": "fixture-boot", "native_started": True,
    }))
    monkeypatch.setattr("agent_bridge.native_runtime.process_matches", lambda *a: True)
    manager = NativeManager(tmp_path / "controller", lambda: pytest.fail("status must not allocate a provider"))
    manager.store(create=True).reserve(*identity, "request-example", "example-venue", str(tmp_path), "hash", {
        "spec": {"codespace": "example-venue"}, "launchRequested": True,
    })

    class Transport:
        async def request(self, method, params=None):
            assert method == "status"
            reply = await provider_status(lambda: runtime.status(*identity, db=registry["client"]))
            if not reply["ok"]:
                raise NativeError("provider_operation_failed", reply["error"], 503)
            return reply["result"]

        async def close(self):
            pass

    manager.transports[identity[0]] = Transport()
    app = FastAPI()
    app.state.native_manager = manager
    app.include_router(router)
    return runtime, manager, app, identity


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["fresh", "absent", "stale", "nonlive", "late"])
async def test_native_registration_states_remain_distinct_from_lookup_failure(native_status, registry, mode):
    runtime, manager, app, identity = native_status
    registry["mode"] = mode
    if mode == "late":
        runtime.store.update(*identity, sessionId=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        response = await client.get(f"/api/v1/native-executions/{identity[0]}?generation={identity[1]}")
    value = response.json()
    assert response.status_code == 200 and value["error"] is None
    assert value["ready"] is (mode == "fresh") and value["represented"] is (mode == "fresh")
    assert value["state"] == ("ready" if mode == "fresh" else "starting" if mode == "late" else "unrepresented")
    assert value["sessionId"] == (None if mode == "late" else "session-example")
    if mode in {"fresh", "absent", "stale"}:
        assert_captured_receipt(value, "recovered" if mode == "fresh" else mode)
    assert len(registry["requests"]) == (0 if mode == "late" else 1)
    with pytest.raises(NativeError, match="blocks ACP"):
        manager.assert_acp_allowed("example-venue")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,error_type", [
    ("auth", BridgeClientError), ("server", BridgeClientError), ("disconnect", BridgeConnectionError),
    ("json", json.JSONDecodeError), ("encoding", UnicodeDecodeError), ("truncated", IncompleteRead),
])
async def test_native_lookup_failure_is_sanitized_and_clears_after_recovery(native_status, registry, mode, error_type):
    runtime, manager, app, identity = native_status
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://fixture") as client:
        url = f"/api/v1/native-executions/{identity[0]}?generation={identity[1]}"
        assert (await client.get(url)).json()["ready"] is True
        registry["mode"] = mode
        with pytest.raises(error_type):
            registry["client"].get_live_session("session-example")
        failed = (await client.get(url)).json()
        assert_captured_receipt(failed, "lookup_failure")
        assert failed["error"] == "registration_lookup_failed"
        assert failed["state"] == "unrepresented" and not failed["ready"] and not failed["represented"]
        assert (failed["executionId"], failed["generation"], failed["sessionId"]) == (*identity, "session-example")
        runtime_row = runtime.store.get(*identity)
        assert runtime_row["state"] == "unrepresented" and not runtime_row["data"]["represented"]
        assert runtime_row["data"]["sessionId"] == "session-example"
        assert manager.store().get(*identity)["data"]["error"] == "registration_lookup_failed"
        assert identity[0] in manager.transports
        for secret in registry["secrets"]:
            assert secret not in json.dumps(failed)
        with pytest.raises(NativeError, match="blocks ACP"):
            manager.assert_acp_allowed("example-venue")
        registry["mode"] = "absent"
        absent = (await client.get(url)).json()
        assert absent["error"] is None and not absent["ready"]
        registry["mode"] = "fresh"
        recovered = (await client.get(url)).json()
        assert recovered["ready"] and recovered["represented"] and recovered["error"] is None
        assert recovered["sessionId"] == "session-example"
        assert_captured_receipt(recovered, "recovered")


def test_unexpected_registry_fault_is_not_swallowed(native_status):
    runtime, _, _, identity = native_status

    class FaultyRegistry:
        def get_live_session(self, session):
            raise RuntimeError("unexpected fixture programming fault")

    with pytest.raises(RuntimeError, match="unexpected fixture programming fault"):
        runtime.status(*identity, db=FaultyRegistry())


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,exit_code,method", [
    ({}, 0, "status"), ({"generation": "wrong"}, 0, "status"),
    ({"executionId": None}, 0, "status"), ({"represented": True}, 0, "status"),
    ({"error": "other_operation_failure"}, 0, "status"), ({}, 78, "status"), ({}, 0, "stop"),
])
async def test_provider_passes_only_identity_bound_status_diagnostics(overrides, exit_code, method):
    value = {
        "executionId": "execution-example", "generation": "generation-example",
        "state": "unrepresented", "represented": False, "retired": False,
        "error": "registration_lookup_failed",
    }
    frame = await provider_status(lambda: value, overrides=overrides, exit_code=exit_code, method=method)
    assert frame["ok"] is (not overrides and exit_code == 0 and method == "status")
    if frame["ok"]:
        assert frame["result"]["error"] == value["error"]
