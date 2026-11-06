"""Passive Bridge projection; unknown is not listener or authentication proof."""

from __future__ import annotations

import json

import pytest

from agent_machines import health_observe as observe
from agent_machines import health_slots
from agent_machines.__main__ import main


class Response:
    status = 200
    length = 0

    def __init__(self, body):
        self.body = body

    def getheader(self, name, default=""):
        assert name == "Content-Type"
        return "application/json"

    def read(self, size):
        assert size == health_slots.MAX_BYTES + 1
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


@pytest.fixture
def owner(monkeypatch):
    class Socket:
        def __init__(self, deadline):
            pass

        def _timeout(self):
            pass

        def connect(self, target):
            assert target == ("127.0.0.1", 12345)

    class Connection:
        body = json.dumps({
            "service": "agent-bridge", "status": "ok",
            "min_protocol_version": 1, "protocol_version": 52,
            "ready": False, "credential_relay_ready": True, "live_host_count": 99,
            "elapsed_seconds": .25, "timestamp_ns": 1_794_000_000_000_000_000,
            "private": "do-not-project",
        }).encode()
        closed = 0
        requests = []

        def __init__(self, host, port):
            assert (host, port) == ("127.0.0.1", 12345)

        def request(self, method, path, headers):
            self.requests.append((method, path, headers))

        def getresponse(self):
            return Response(self.body)

        def close(self):
            type(self).closed += 1

    monkeypatch.setattr(observe, "_DeadlineSocket", Socket)
    monkeypatch.setattr(observe.http.client, "HTTPConnection", Connection)
    return Connection


def test_normal_cli_projects_only_selected_listener_and_drops_owner_details(owner, capsys):
    assert main([
        "health", "observe", "--bridge-port", "12345",
        "--publisher", "example-worker", "--continuity", "example-generation",
    ]) == 0
    raw = capsys.readouterr().out
    result = health_slots.validate_record(json.loads(raw))
    listener = result["domains"]["local_listener"]
    assert listener["state"] == "ok" and listener["evidence"] == "listener-check"
    assert listener["observed_at_ms"] == result["observed_at_ms"]
    assert all(value["state"] == "unknown" for name, value in result["domains"].items() if name != "local_listener")
    assert result["domains"]["logical_agents"]["count"] is None
    assert result["domains"]["ssh_authenticated"]["direction"] is None
    assert "do-not-project" not in raw and "live_host_count" not in raw
    assert owner.requests == [("GET", "/health", {"Connection": "close"})]
    assert owner.closed == 1


@pytest.mark.parametrize(("body", "state", "reason"), [
    (b'{"service":"other","status":"ok","min_protocol_version":1,"protocol_version":52}', "unknown", "invalid"),
    (b'{"service":"agent-bridge","status":"ok"}', "unsupported", "not-observed"),
    (b'{"min_protocol_version":2,"protocol_version":52}', "unsupported", "not-observed"),
    (b'{"min_protocol_version":true,"protocol_version":52}', "unsupported", "not-observed"),
    (b'{"status":"ok","status":"failed"}', "unknown", "invalid"),
    (b'["not","a","health","object"]', "unknown", "invalid"),
    (b'{"value":NaN}', "unknown", "invalid"),
    (b'\xff', "unknown", "invalid"),
], ids=["foreign", "legacy", "new-floor", "bool", "duplicate", "array", "nan", "utf8"])
def test_unqualified_owner_response_stays_unknown_or_unsupported(owner, body, state, reason):
    owner.body = body
    result = observe.observe_bridge(12345, "example", "generation")
    listener = result["domains"]["local_listener"]
    assert listener == {
        "state": state, "reason": reason, "evidence": "not-observed", "observed_at_ms": None,
    }


@pytest.mark.parametrize(("error", "reason"), [(TimeoutError(), "timeout"), (OSError("private"), "unavailable")])
def test_transport_errors_are_unknown_without_raw_messages(owner, monkeypatch, error, reason):
    def fail(_self):
        raise error

    monkeypatch.setattr(owner, "getresponse", fail)
    result = observe.observe_bridge(12345, "example", "generation")
    assert result["domains"]["local_listener"]["reason"] == reason
    assert result["domains"]["local_listener"]["state"] == "unknown"
    assert "private" not in json.dumps(result) and owner.closed == 1


def test_invalid_identity_is_rejected_before_any_owner_request(owner):
    with pytest.raises(health_slots.HealthError):
        observe.observe_bridge(12345, "../private", "generation")
    assert owner.requests == []


@pytest.mark.parametrize("port", [0, 65536, True])
def test_invalid_port_never_contacts_an_owner(owner, port):
    with pytest.raises(health_slots.HealthError, match="invalid-bridge-port"):
        observe.observe_bridge(port, "example", "generation")
    assert owner.requests == []


def test_wall_clock_regression_does_not_make_fresh_proof(owner, monkeypatch):
    times = iter([1_000_000_000, 900_000_000])
    monkeypatch.setattr(observe.time, "time_ns", lambda: next(times))
    result = observe.observe_bridge(12345, "example", "generation")
    assert result["domains"]["local_listener"]["state"] == "unknown"
    assert result["domains"]["local_listener"]["reason"] == "clock-skew"


def test_wire_deadline_and_byte_exhaustion_are_enforced_before_another_read(monkeypatch):
    connection = observe._DeadlineSocket(10)
    try:
        monkeypatch.setattr(observe.time, "monotonic", lambda: 10)
        with pytest.raises(TimeoutError):
            connection.recv_into(bytearray(1))
        monkeypatch.setattr(observe.time, "monotonic", lambda: 9)
        connection.remaining = 0
        with pytest.raises(observe._WireLimit):
            connection.recv_into(bytearray(1))
    finally:
        connection.close()
