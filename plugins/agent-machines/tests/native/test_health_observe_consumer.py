"""Real loopback and CLI consumers; no installed daemon or real credentials."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import pytest

from agent_procutil import no_window_kwargs
from agent_machines import health_slots


@contextmanager
def listener(response, *, drip=False, count=1):
    requests, failures = [], []
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        server.settimeout(8)
        port = server.getsockname()[1]

        def serve():
            try:
                for _ in range(count):
                    with server.accept()[0] as peer:
                        peer.settimeout(5)
                        request = b""
                        while b"\r\n\r\n" not in request:
                            chunk = peer.recv(2048)
                            if not chunk or len(request) + len(chunk) > 2048:
                                raise AssertionError("bounded HTTP request unavailable")
                            request += chunk
                        requests.append(request)
                        if drip:
                            peer.sendall(response)
                            for _ in range(20):
                                peer.sendall(b" ")
                                time.sleep(.15)
                        else:
                            peer.sendall(response)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                if not drip:
                    failures.append("unexpected closed reader")
            except Exception as error:
                failures.append(type(error).__name__)

        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        try:
            yield port, requests
        finally:
            worker.join(9)
            assert not worker.is_alive(), "owned finite listener did not exit"
            assert not failures, failures


def call(arguments):
    child = subprocess.run(
        [sys.executable, "-I", "-m", "agent_machines", "health", *arguments],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert child.returncode == 0, child.stderr
    return json.loads(child.stdout)


def response(body):
    return (
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
        + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body
    )


def roundtrip(tmp_path, monkeypatch, body):
    monkeypatch.setenv("HTTP_PROXY", "http://192.0.2.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://192.0.2.1:1")
    with listener(response(body)) as (port, requests):
        observed = call([
            "observe", "--bridge-port", str(port),
            "--publisher", "example-worker", "--continuity", "example-generation",
        ])
    assert len(requests) == 1 and requests[0].startswith(b"GET /health HTTP/1.1\r\n")
    assert b"Authorization:" not in requests[0] and b"Cookie:" not in requests[0]
    assert "private-value" not in json.dumps(observed)
    assert observed["domains"]["local_listener"]["state"] == "ok"
    assert observed["domains"]["logical_agents"]["count"] is None
    assert observed["domains"]["ssh_authenticated"]["direction"] is None
    source = tmp_path / "observation.json"
    source.write_text(json.dumps(observed), encoding="utf-8")
    store = tmp_path / "store"
    store.mkdir()
    (store / "policy.json").write_text(json.dumps({
        "schema": health_slots.POLICY_SCHEMA, "version": 1,
        "publisher": "example-worker", "continuity": "example-generation",
    }), encoding="utf-8")
    arguments = ["record", "--input", str(source), "--store-dir", str(store)]
    preview = call(arguments)
    assert len(list(store.iterdir())) == 1
    recorded = call([*arguments, "--apply", "--expect", preview["preview_token"]])
    assert recorded["applied"] and recorded["changed"]
    now = datetime.now(timezone.utc)
    view = call(["inspect", "--store-dir", str(store), "--now", now.isoformat()])
    assert view["observed_hours"] == 1
    sample = next(hour["sample"] for hour in view["hours"] if hour["state"] == "observed")
    assert sample["domains"]["local_listener"]["state"] == "ok"
    older = call([
        "inspect", "--store-dir", str(store), "--now", (now + timedelta(hours=2)).isoformat(),
    ])
    sample = next(hour["sample"] for hour in older["hours"] if hour["state"] == "observed")
    assert sample["domains"]["local_listener"]["state"] == "stale"
    assert sample["domains"]["local_listener"]["observed_state"] == "ok"
    assert older["carrier_receipt"] == "not-observed"
    before = {path.name: path.read_bytes() for path in store.iterdir()}
    chart = tmp_path / "composed.svg"
    report = call([
        "report", "--store-dir", str(store), "--now", (now + timedelta(hours=2)).isoformat(),
        "--chart", str(chart),
    ])
    assert report["health"] == {"state": "available", "view": older}
    assert report["coverage"]["accepted_records"] == 0
    assert all(hour["state"] == "no-observation" for hour in report["hours"])
    assert "observed ok;" in chart.read_text(encoding="utf-8")
    assert "input-supplied-not-verified" in chart.read_text(encoding="utf-8")
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before
    unsafe_chart = store / "unrelated.svg"
    refused = subprocess.run(
        [sys.executable, "-I", "-m", "agent_machines", "health", "report",
         "--store-dir", str(store), "--chart", str(unsafe_chart)],
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert refused.returncode == 2, refused.stderr
    assert not unsafe_chart.exists()
    assert {path.name: path.read_bytes() for path in store.iterdir()} == before


def test_native_http_observation_to_normal_slot_and_age_qualified_reader(tmp_path, monkeypatch):
    roundtrip(tmp_path, monkeypatch, json.dumps({
        "status": "ok", "service": "agent-bridge", "protocol_version": 52,
        "min_protocol_version": 1, "live_host_count": 123,
        "elapsed_seconds": .25, "timestamp_ns": 1_794_000_000_000_000_000,
        "credential_relay_ready": True, "readiness_error": "private-value",
    }).encode())


def test_original_bridge_owner_response_to_native_local_health_consumer(tmp_path, monkeypatch):
    interpreter = os.environ.get("AGENT_MACHINES_TEST_BRIDGE_PYTHON")
    if not interpreter:
        pytest.skip("explicit source-bound Bridge test interpreter required")
    code = """
import asyncio, json, sys
from pathlib import Path
from types import SimpleNamespace
from agent_bridge.routes import health as owner
owner.get_default_manager = lambda: SimpleNamespace(carrier_diagnostics=lambda: {})
owner.config_dir = lambda: Path(sys.argv[1])
owner.slot_descriptor = lambda *args, **kwargs: {}
owner.observe_worktree_writer = lambda: {"installation_root": None, "runtime_generation": None}
request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
print(json.dumps(asyncio.run(owner.health(request))))
"""
    producer = subprocess.run(
        [interpreter, "-I", "-c", code, str(tmp_path)], capture_output=True,
        text=True, timeout=15, **no_window_kwargs(),
    )
    assert producer.returncode == 0, producer.stderr
    body = json.loads(producer.stdout)
    assert body["service"] == "agent-bridge" and body["status"] == "ok"
    roundtrip(tmp_path, monkeypatch, producer.stdout.encode("utf-8"))


QUALIFYING_BODY = b'{"service":"agent-bridge","status":"ok","min_protocol_version":1,"protocol_version":52}'


@pytest.mark.parametrize(("wire", "drip", "reason"), [
    (b"HTTP/1.1 302 Found\r\nLocation: http://192.0.2.1/\r\nContent-Length: 0\r\n\r\n", False, "unavailable"),
    (response(QUALIFYING_BODY.ljust(health_slots.MAX_BYTES, b" ")), False, "invalid"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 20\r\n\r\n", True, "timeout"),
    (response(QUALIFYING_BODY).replace(
        b"Content-Length: " + str(len(QUALIFYING_BODY)).encode(),
        b"Content-Length: " + str(len(QUALIFYING_BODY) + 50).encode(),
    ), False, "invalid"),
    (response(QUALIFYING_BODY).replace(
        b"Content-Length: " + str(len(QUALIFYING_BODY)).encode(),
        b"Content-Length: 1000000000000000000000000000000",
    ), False, "invalid"),
    (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n\r\n"
     + f"{len(QUALIFYING_BODY):x}\r\n".encode() + QUALIFYING_BODY + b"\r\n", False, "unavailable"),
], ids=["redirect", "wire-limit", "drip-deadline", "truncated-length", "huge-length", "truncated-chunks"])
def test_native_http_refuses_redirect_overflow_and_continuous_trickle(wire, drip, reason):
    with listener(wire, drip=drip) as (port, requests):
        result = call([
            "observe", "--bridge-port", str(port), "--publisher", "example", "--continuity", "generation",
        ])
    assert len(requests) == 1
    assert result["domains"]["local_listener"] == {
        "state": "unknown", "evidence": "not-observed", "reason": reason, "observed_at_ms": None,
    }


def test_native_observe_closed_stdout_preserves_exit_two():
    reader, writer = os.pipe()
    os.close(reader)
    try:
        with listener(response(QUALIFYING_BODY)) as (port, requests):
            child = subprocess.run([
                sys.executable, "-I", "-m", "agent_machines", "health", "observe",
                "--bridge-port", str(port), "--publisher", "example", "--continuity", "generation",
            ], stdout=writer, stderr=subprocess.PIPE, text=True, timeout=15, **no_window_kwargs())
    finally:
        os.close(writer)
    assert len(requests) == 1
    assert child.returncode == 2, child.stderr
    assert json.loads(child.stderr) == {"ok": False, "error": "observation-unavailable"}
