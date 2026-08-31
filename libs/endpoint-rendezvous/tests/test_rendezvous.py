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


def test_clear_endpoint_removes_owned_record(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)

    clear_endpoint(tmp_path, owner_pid=1001)

    assert read_endpoint(tmp_path) is None


def test_clear_endpoint_preserves_successor_owned_record(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)

    clear_endpoint(tmp_path, owner_pid=1001)

    ep = read_endpoint(tmp_path)
    assert ep is not None
    assert ep.pid == 2002
    assert ep.tcp_host_port == ("127.0.0.1", 41002)


def test_clear_endpoint_cutover_retains_newest_successor_record(tmp_path):
    write_endpoint(tmp_path, "tcp", "127.0.0.1:41001", pid=1001)
    write_endpoint(tmp_path, "tcp", "127.0.0.1:41002", pid=2002)

    clear_endpoint(tmp_path, owner_pid=1001)

    ep = read_endpoint(tmp_path)
    assert ep is not None
    assert ep.pid == 2002
    assert ep.tcp_host_port == ("127.0.0.1", 41002)


def test_clear_endpoint_removes_non_utf8_record(tmp_path):
    endpoint_file(tmp_path).write_bytes(b"\xff")

    clear_endpoint(tmp_path, owner_pid=1001)

    assert not endpoint_file(tmp_path).exists()


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


def test_openprocess_denied_means_alive():
    # A denied OpenProcess (ERROR_ACCESS_DENIED = 5) means the process exists
    # -- e.g. a coordinator under a Scheduled Task (LogonType S4U) queried by a
    # client in another logon session. Any other failure is treated as gone.
    assert rv._openprocess_denied_means_alive(rv._ERROR_ACCESS_DENIED) is True
    assert rv._openprocess_denied_means_alive(5) is True
    assert rv._openprocess_denied_means_alive(87) is False  # ERROR_INVALID_PARAMETER
    assert rv._openprocess_denied_means_alive(0) is False


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


# --- Alternate endpoints (multi-transport advertisement) -------------------


def test_no_alt_record_omits_key():
    """A single-endpoint record is byte-identical to before (no ``alt`` key)."""
    rec = Endpoint(transport="tcp", address="127.0.0.1:5").to_record()
    assert "alt" not in rec


def test_alt_roundtrips_through_record():
    ep = Endpoint(
        transport="pipe",
        address=r"\\.\pipe\agent-vault",
        alt=(Endpoint(transport="tcp", address="127.0.0.1:52731"),),
    )
    rec = ep.to_record()
    assert rec["alt"] == [{"transport": "tcp", "endpoint": "127.0.0.1:52731"}]
    back = Endpoint.from_record(rec, source="windows")
    assert back.transport == "pipe"
    assert [(e.transport, e.address) for e in back.alt] == [("tcp", "127.0.0.1:52731")]


def test_old_record_without_alt_reads_as_empty():
    back = Endpoint.from_record(
        {"schema": 1, "transport": "tcp", "endpoint": "127.0.0.1:5"}
    )
    assert back.alt == ()


def test_from_record_skips_malformed_alt_entries():
    """A bad alt entry is dropped; the primary and the valid alts survive."""
    back = Endpoint.from_record(
        {
            "schema": 1,
            "transport": "pipe",
            "endpoint": r"\\.\pipe\y",
            "alt": [
                {"transport": "bogus", "endpoint": "x"},
                {"transport": "tcp", "endpoint": ""},
                {"transport": "tcp", "endpoint": "127.0.0.1:9"},
            ],
        }
    )
    assert [(e.transport, e.address) for e in back.alt] == [("tcp", "127.0.0.1:9")]


def test_usable_returns_self_when_primary_accepted():
    ep = Endpoint(transport="tcp", address="127.0.0.1:5")
    assert ep.usable(lambda t: t == "tcp") is ep


def test_usable_falls_to_matching_alt_and_keeps_source():
    ep = Endpoint(
        transport="pipe",
        address=r"\\.\pipe\agent-vault",
        source="windows",
        alt=(Endpoint(transport="tcp", address="127.0.0.1:52731"),),
    )
    pick = ep.usable(lambda t: t == "tcp")
    assert pick is not None
    assert (pick.transport, pick.address) == ("tcp", "127.0.0.1:52731")
    assert pick.source == "windows"


def test_usable_none_when_nothing_accepted():
    ep = Endpoint(transport="pipe", address=r"\\.\pipe\x")
    assert ep.usable(lambda t: t == "tcp") is None


def test_write_endpoint_persists_alt(tmp_path):
    write_endpoint(tmp_path, "pipe", r"\\.\pipe\agent-vault", alt=[("tcp", "127.0.0.1:40404")])
    back = read_endpoint(tmp_path)
    assert back is not None
    assert back.transport == "pipe"
    assert [(e.transport, e.address) for e in back.alt] == [("tcp", "127.0.0.1:40404")]
