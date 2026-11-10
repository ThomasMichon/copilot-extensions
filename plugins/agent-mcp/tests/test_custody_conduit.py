"""Bounded synchronous adoption without current-generation checks on the keeper."""

import json
import socket

import pytest

from agent_mcp import custody_conduit as conduit, execution_context as ec, sockio
from agent_mcp.custody_contract import CONTRACT
from agent_mcp.request_context import CONTEXT_VERSION
from .test_custody_session import configuration


class Connection:
    family = socket.AF_INET

    def __init__(self, replies, changed=None):
        self.data = b"".join((json.dumps(item) + "\n").encode() for item in replies)
        self.requests = []
        self.timeouts = []
        self.closed = False
        self.changed = changed

    def settimeout(self, value):
        self.timeouts.append(value)

    def sendall(self, value):
        self.requests.append(json.loads(value))
        if self.changed:
            self.changed(self.requests[-1])

    def recv(self, count):
        data, self.data = self.data[:count], self.data[count:]
        return data

    def close(self):
        self.closed = True


@pytest.fixture
def admission(tmp_path, monkeypatch):
    cfg = configuration()
    monkeypatch.setattr(conduit, "load_config", lambda *args, **kwargs: cfg)
    ping = {
        "ok": True, "pong": True, "ready": True, "draining": False,
        "custody_host": CONTRACT, "installation_root": str(tmp_path),
        "configuration_context": CONTEXT_VERSION, "execution_context": ec.descriptor(),
    }
    ack = {"ok": True, "attached": True, "custody": CONTRACT}

    def connect(ping=ping, ack=ack, token="private-fixture-token", changed=None):
        connection = Connection([ping, ack, {"jsonrpc": "2.0", "method": "notifications/early"}], changed)
        monkeypatch.setattr(sockio, "_connect", lambda path, **kwargs: (connection, token))
        return connection
    return tmp_path, ping, ack, connect


def test_adoption_does_not_consult_keepers_physical_generation_or_overread(admission):
    root, _, _, connect = admission
    connection = connect()
    assert conduit.attach(root / "custody.sock", "fixture", root) is connection
    assert [item["op"] for item in connection.requests] == ["ping", "attach"]
    request = connection.requests[-1]
    assert request["installation_root"] == str(root)
    assert "generation" not in request and "runtime_root" not in request
    assert request["configuration_context"]["root"] != str(root)
    assert connection.timeouts[-1] is None
    assert not connection.closed and json.loads(connection.data)["method"] == "notifications/early"


@pytest.mark.parametrize("case", ["legacy", "foreign", "context", "draining", "not-ready", "auth"])
def test_unqualified_host_never_receives_attach_or_application_bytes(admission, case):
    root, ping, _, connect = admission
    if case == "legacy":
        ping.pop("custody_host")
    elif case == "foreign":
        ping["installation_root"] = str(root.parent)
    elif case == "context":
        ping.pop("execution_context")
    elif case == "draining":
        ping["draining"] = True
    elif case == "not-ready":
        ping["ready"] = False
    connection = connect(token=None if case == "auth" else "private-fixture-token")
    with pytest.raises(OSError):
        conduit.attach(root / "custody.sock", "fixture", root)
    assert connection.closed
    assert [item["op"] for item in connection.requests] == ([] if case == "auth" else ["ping"])


@pytest.mark.parametrize("case", ["unsupported", "not-attached", "context-drift"])
def test_failed_attach_never_pumps_or_retries(admission, monkeypatch, case):
    root, _, ack, connect = admission
    if case == "unsupported":
        ack.pop("custody")
    elif case == "not-attached":
        ack["attached"] = 1

    def changed(request):
        if case == "context-drift" and request["op"] == "attach":
            monkeypatch.setenv("CUSTODY_CONDUIT_FIXTURE", "changed")
    connection = connect(changed=changed)
    with pytest.raises(OSError):
        conduit.attach(root / "custody.sock", "fixture", root)
    assert connection.closed
    assert [item["op"] for item in connection.requests] == ["ping", "attach"]


def test_slow_fragmented_ack_has_one_total_deadline(monkeypatch):
    clock = [0.0]
    connection = Connection([])
    monkeypatch.setattr(sockio.time, "monotonic", lambda: clock[0])

    def read(_count):
        clock[0] += 0.4
        return b"x"
    connection.recv = read
    with pytest.raises(TimeoutError, match="deadline"):
        sockio._read_ack_line(connection, deadline=1)
    assert len(connection.timeouts) == 3
    assert connection.timeouts[0] > connection.timeouts[-1] > 0
