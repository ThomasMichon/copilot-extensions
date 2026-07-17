"""Unit tests for endpoint_rendezvous."""

from __future__ import annotations

import json
import os
import socket

import pytest

from endpoint_rendezvous import (
    Endpoint,
    EndpointUnavailable,
    clear_endpoint,
    connect_probe,
    endpoint_file,
    is_stale,
    pid_alive,
    read_endpoint,
    resolve,
    write_endpoint,
)
from endpoint_rendezvous import rendezvous as rv

# --- Endpoint parsing / formatting -----------------------------------------


def test_parse_and_to_spec_roundtrip():
    ep = Endpoint.parse("tcp:127.0.0.1:9847")
    assert ep.transport == "tcp"
    assert ep.address == "127.0.0.1:9847"
    assert ep.to_spec() == "tcp:127.0.0.1:9847"


def test_parse_preserves_colons_in_address():
    ep = Endpoint.parse(r"pipe:\\.\pipe\agent-x")
    assert ep.transport == "pipe"
    assert ep.address == r"\\.\pipe\agent-x"


def test_parse_unix_path():
    ep = Endpoint.parse("unix:/home/u/.agent-x/run/x.sock")
    assert ep.transport == "unix"
    assert ep.address == "/home/u/.agent-x/run/x.sock"


def test_parse_rejects_missing_separator():
    with pytest.raises(ValueError):
        Endpoint.parse("127.0.0.1:9847")  # no transport prefix


def test_endpoint_rejects_bad_transport():
    with pytest.raises(ValueError):
        Endpoint(transport="http", address="x")


def test_endpoint_rejects_empty_address():
    with pytest.raises(ValueError):
        Endpoint(transport="tcp", address="")


def test_tcp_host_port():
    assert Endpoint("tcp", "127.0.0.1:52731").tcp_host_port == ("127.0.0.1", 52731)


def test_tcp_host_port_on_non_tcp_raises():
    with pytest.raises(ValueError):
        _ = Endpoint("unix", "/x.sock").tcp_host_port


# --- write / read roundtrip -------------------------------------------------


def test_write_read_roundtrip(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:52731", pid=4321, started_at="2026-07-16T22:41:09Z")
    ep = read_endpoint(tmp_path)
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.address == "127.0.0.1:52731"
    assert ep.pid == 4321
    assert ep.started_at == "2026-07-16T22:41:09Z"
    assert ep.source == "file"


def test_write_defaults_pid_and_timestamp(tmp_path):
    write_endpoint(tmp_path, "unix", "/run/x.sock")
    ep = read_endpoint(tmp_path)
    assert ep.pid == os.getpid()
    assert ep.started_at and ep.started_at.endswith("Z")


def test_on_disk_json_uses_endpoint_key(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:1", pid=7)
    data = json.loads(endpoint_file(tmp_path).read_text(encoding="utf-8"))
    assert data == {
        "schema": 1,
        "transport": "tcp",
        "endpoint": "127.0.0.1:1",
        "pid": 7,
        "started_at": data["started_at"],
    }


def test_write_is_atomic_no_temp_left(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:1")
    leftovers = [p.name for p in tmp_path.iterdir() if p.name != "endpoint.json"]
    assert leftovers == []


def test_write_creates_runtime_dir(tmp_path):
    nested = tmp_path / "a" / "b" / "run"
    write_endpoint(nested, "tcp", "127.0.0.1:1")
    assert endpoint_file(nested).exists()


def test_clear_endpoint(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:1")
    clear_endpoint(tmp_path)
    assert read_endpoint(tmp_path) is None
    clear_endpoint(tmp_path)  # idempotent, no raise


# --- read robustness --------------------------------------------------------


def test_read_missing_returns_none(tmp_path):
    assert read_endpoint(tmp_path) is None


def test_read_malformed_json_returns_none(tmp_path):
    endpoint_file(tmp_path).write_text("{not json", encoding="utf-8")
    assert read_endpoint(tmp_path) is None


def test_read_wrong_schema_returns_none(tmp_path):
    endpoint_file(tmp_path).write_text(
        json.dumps({"schema": 999, "transport": "tcp", "endpoint": "127.0.0.1:1"}),
        encoding="utf-8",
    )
    assert read_endpoint(tmp_path) is None


# --- pid liveness / staleness ----------------------------------------------


def test_pid_alive_self():
    assert pid_alive(os.getpid()) is True


def test_pid_alive_nonpositive():
    assert pid_alive(0) is False
    assert pid_alive(-1) is False
    assert pid_alive(None) is False


def test_is_stale_none():
    assert is_stale(None) is True


def test_is_stale_dead_pid(monkeypatch):
    monkeypatch.setattr(rv, "pid_alive", lambda pid: False)
    assert is_stale(Endpoint("tcp", "127.0.0.1:1", pid=123)) is True


def test_is_stale_live_pid(monkeypatch):
    monkeypatch.setattr(rv, "pid_alive", lambda pid: True)
    assert is_stale(Endpoint("tcp", "127.0.0.1:1", pid=123)) is False


def test_is_stale_no_pid_no_probe():
    assert is_stale(Endpoint("tcp", "127.0.0.1:1")) is False


def test_is_stale_probe_refused():
    ep = Endpoint("tcp", "127.0.0.1:1")
    assert is_stale(ep, probe=lambda e: False) is True
    assert is_stale(ep, probe=lambda e: True) is False


# --- connect_probe ----------------------------------------------------------


def test_connect_probe_refused_tcp():
    # Bind then close to obtain a port that is definitely not listening.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    assert connect_probe(Endpoint("tcp", f"127.0.0.1:{port}"), timeout=0.2) is False


def test_connect_probe_open_tcp():
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]
    try:
        assert connect_probe(Endpoint("tcp", f"127.0.0.1:{port}"), timeout=0.5) is True
    finally:
        srv.close()


def test_connect_probe_pipe_unprobed():
    assert connect_probe(Endpoint("pipe", r"\\.\pipe\agent-x")) is True


# --- resolve ladder ---------------------------------------------------------


def test_resolve_override_spec_wins(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:1", pid=os.getpid())
    ep = resolve(tmp_path, override="unix:/x.sock", legacy="tcp:127.0.0.1:9847")
    assert ep.transport == "unix"
    assert ep.address == "/x.sock"
    assert ep.source == "env"


def test_resolve_override_endpoint_wins(tmp_path):
    ep = resolve(tmp_path, override=Endpoint("tcp", "127.0.0.1:5"))
    assert ep.address == "127.0.0.1:5"


def test_resolve_file_when_live(tmp_path, monkeypatch):
    monkeypatch.setattr(rv, "pid_alive", lambda pid: True)
    write_endpoint(tmp_path, "tcp", "127.0.0.1:52731", pid=222)
    ep = resolve(tmp_path, legacy="tcp:127.0.0.1:9847")
    assert ep.address == "127.0.0.1:52731"
    assert ep.source == "file"


def test_resolve_stale_file_falls_back_to_legacy(tmp_path, monkeypatch):
    monkeypatch.setattr(rv, "pid_alive", lambda pid: False)
    write_endpoint(tmp_path, "tcp", "127.0.0.1:52731", pid=222)
    ep = resolve(tmp_path, legacy="tcp:127.0.0.1:9847")
    assert ep.address == "127.0.0.1:9847"
    assert ep.source == "legacy"


def test_resolve_no_file_uses_legacy(tmp_path):
    ep = resolve(tmp_path, legacy="tcp:127.0.0.1:9847")
    assert ep.address == "127.0.0.1:9847"
    assert ep.source == "legacy"


def test_resolve_nothing_raises(tmp_path):
    with pytest.raises(EndpointUnavailable):
        resolve(tmp_path)
