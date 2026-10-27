"""Opt-in Windows process/transport proof, outside the required unit packet."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job
from zdd import routing

from agent_worktrees import locks
from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import status_monitor_recovery_contract as recovery

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows Job/native identity proof"),
    pytest.mark.skipif(os.environ.get("AGENT_WORKTREES_NATIVE_RECOVERY") != "1",
                       reason="opt in with AGENT_WORKTREES_NATIVE_RECOVERY=1"),
]


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(0.01)
    raise AssertionError("isolated receiver did not reach its bounded gate")


@contextmanager
def start_provider(tmp_path, mode="normal"):
    tmp_path.mkdir(parents=True, exist_ok=True)
    helper = Path(__file__).with_name("native_guarded_recovery_provider.py")
    python, extra_env = direct_python(sys.executable)
    with (tmp_path / "provider.log").open("w", encoding="utf-8") as log:
        proc, job = spawn_sync_in_kill_on_close_job(
            [python, str(helper), str(tmp_path), mode],
            require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, creationflags=subprocess.CREATE_NO_WINDOW,
            env={**os.environ, **extra_env},
        )
        assert job is not None
        try:
            ready = tmp_path / "ready.json"
            def started():
                assert proc.poll() is None, (tmp_path / "provider.log").read_text()
                return ready.exists()
            wait_for(started)
            data = json.loads(ready.read_text())
            assert data["pid"] == proc.pid and data["start"] == locks.process_start_time(proc.pid)
            route = routing.publish_active(
                tmp_path, bind="127.0.0.1", port=data["port"], pid=proc.pid,
                process_start_time=data["start"], version="fixture", generation=3,
            )
            token_path = tmp_path / "retained-token"
            token_path.write_text(data["token"], encoding="utf-8")
            data["process"] = proc
            yield tmp_path, data, route, token_path
        finally:
            (tmp_path / "release").touch()
            (tmp_path / "stop").touch()
            job.terminate_and_wait(5)
            proc.wait(timeout=5)
            assert job.handle is None and proc.returncode is not None


@pytest.fixture
def provider(tmp_path, request):
    with start_provider(tmp_path, getattr(request, "param", "normal")) as selected:
        yield selected


def retained_client(provider):
    root, data, route, token_path = provider
    retained = recovery.negotiate(root, token_path, control.ControlClient, timeout=3)
    client = control.ControlClient(
        route.base_url, token=data["token"], config_dir=root, timeout=3,
        guarded_predecessor=retained,
    )
    return retained, client


def test_native_guarded_receipt_and_new_rollback_route_generation(provider, monkeypatch):
    monkeypatch.setattr(control, "load_or_create_control_token", lambda: pytest.fail("minted token"))
    retained, client = retained_client(provider)
    assert client.health()["health_calls"] == 2
    root, data, route, _ = provider
    restored = routing.publish_active(
        root, bind=route.bind, port=route.port, pid=data["pid"], version=route.version,
        process_start_time=data["start"], generation=5,
    )
    result = client.undrain()
    assert result["route"] == restored.to_dict()
    assert result["receiver"] == retained.receiver.to_dict()
    assert result["draining"] is False and client.health()["opened"] == 1


@pytest.mark.parametrize("provider", ["old-provider"], indirect=True)
def test_native_old_provider_refuses_without_admission_effect(provider):
    root, data, route, token_path = provider
    with pytest.raises(recovery.RecoveryUnavailable, match="unsupported"):
        recovery.negotiate(root, token_path, control.ControlClient, timeout=3)
    health = control.ControlClient(route.base_url, token=data["token"]).health()
    assert health["opened"] == 0 and health["health_calls"] == 2
    assert recovery.active_endpoint(root) == route


@pytest.mark.parametrize("change", ["pid", "start", "runtime", "port", "route-generation"])
def test_native_wrong_receiver_or_route_never_reopens(provider, change):
    retained, client = retained_client(provider)
    root, _, _, _ = provider
    payload = retained.operation(root, client.base_url)
    if change == "pid":
        payload["expectedReceiver"]["pid"] += 1
    elif change == "start":
        payload["expectedReceiver"]["processStartTime"] = "wrong-native-start"
    elif change == "runtime":
        payload["expectedReceiver"]["runtimeGeneration"] = "different-runtime"
    elif change == "port":
        payload["expectedRoute"]["port"] += 1
    else:
        payload["expectedRoute"]["generation"] += 1
    assert client._request(recovery.ACTION, payload)["status"] == "refused"
    assert client.health()["opened"] == 0


@pytest.mark.parametrize("mutation", ["duplicate", "version", "truncated", "payload"])
def test_native_actual_parser_rejects_bad_frames(provider, mutation):
    retained, client = retained_client(provider)
    root, data, _, _ = provider
    frame = json.dumps({"version": 1, "token": data["token"], "action": recovery.ACTION,
                        "payload": retained.operation(root, client.base_url)}).encode() + b"\n"
    if mutation == "duplicate":
        frame = frame.replace(b'"version": 1', b'"version": 1, "version": 1')
    elif mutation == "version":
        frame = frame.replace(b'"version": 1', b'"version": true')
    elif mutation == "truncated":
        frame = frame[:-1]
    else:
        value = json.loads(frame)
        value["payload"] = []
        frame = json.dumps(value).encode() + b"\n"
    with socket.create_connection((client.host, client.port), timeout=3) as sock:
        sock.sendall(frame)
        sock.shutdown(socket.SHUT_WR)
        reply = sock.makefile("rb").readline(recovery.FRAME_BYTES)
    assert recovery.decode_response(reply)["result"]["status"] == "refused"
    assert client.health()["opened"] == 0


@pytest.mark.parametrize("provider", ["untyped-reply"], indirect=True)
def test_native_untyped_ack_is_not_success(provider):
    _, client = retained_client(provider)
    with pytest.raises(recovery.RecoveryUnavailable, match="unconfirmed"):
        client.undrain()
    assert client.health()["opened"] == 1


@pytest.mark.parametrize("provider", ["before-gate", "locked-gate"], indirect=True)
def test_native_route_race_is_fenced_at_effect(provider, request):
    root, data, route, _ = provider
    _, client = retained_client(provider)
    responses, failures, writer_errors = [], [], []
    writer = None
    def recover():
        try:
            responses.append(client.undrain())
        except recovery.RecoveryUnavailable as exc:
            failures.append(exc)
    caller = threading.Thread(target=recover)
    caller.start()
    try:
        wait_for(lambda: (root / "entered").exists())
        written = threading.Event()
        attempted = threading.Event()
        def publish():
            attempted.set()
            try:
                routing.publish_active(root, bind=route.bind, port=route.port, pid=os.getpid(),
                                       version="third", process_start_time=locks.process_start_time(os.getpid()))
                written.set()
            except BaseException as exc:
                writer_errors.append(exc)
        writer = threading.Thread(target=publish)
        writer.start()
        assert attempted.wait(2)
        before_gate = request.node.callspec.params["provider"] == "before-gate"
        if before_gate:
            assert written.wait(2), writer_errors
        else:
            assert not written.wait(0.2)
        (root / "release").touch()
        caller.join(timeout=5)
        writer.join(timeout=5)
        assert not caller.is_alive() and not writer.is_alive() and not writer_errors
        assert written.is_set() and recovery.active_endpoint(root).pid == os.getpid()
        if before_gate:
            assert failures and not responses and client.health()["opened"] == 0
        else:
            assert responses and not failures and client.health()["opened"] == 1
    finally:
        (root / "release").touch()
        caller.join(timeout=5)
        if writer is not None:
            writer.join(timeout=5)
