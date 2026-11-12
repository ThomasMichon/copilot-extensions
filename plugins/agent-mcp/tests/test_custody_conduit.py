"""Bounded synchronous adoption without current-generation checks on the keeper."""

import json
import socket

import pytest

from agent_mcp import custody_conduit as conduit, execution_context as ec, sockio
from agent_mcp.checkpoint import CheckpointRefused
from agent_mcp.custody_contract import CONTRACT, host_descriptor
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
    with pytest.raises(ConnectionError) as failure:
        conduit.attach(root / "custody.sock", "fixture", root)
    assert not isinstance(failure.value, ec.SharingUnavailable)
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


def test_required_trace_mode_refuses_old_running_host_before_attach(admission):
    root, _, _, connect = admission
    connection = connect()
    with pytest.raises(ec.SharingUnavailable, match="cannot enforce"):
        conduit.attach(root / "custody.sock", "fixture", root, required_trace_bookkeeping=1)
    assert connection.closed
    assert [item["op"] for item in connection.requests] == ["ping"]


@pytest.mark.parametrize("capable", [False, True])
def test_trace_preflight_reads_actual_keeper_without_attaching(admission, capable):
    root, ping, _, connect = admission
    if capable:
        ping["custody_host"] = host_descriptor()
    connection = connect()
    if capable:
        conduit.probe(root / "custody.sock", root, required_trace_bookkeeping=1)
    else:
        with pytest.raises(ec.SharingUnavailable, match="cannot enforce"):
            conduit.probe(root / "custody.sock", root, required_trace_bookkeeping=1)
    assert connection.closed
    assert [item["op"] for item in connection.requests] == ["ping"]


@pytest.mark.parametrize("confirmation", [None, 0, True, 2, "1", 1])
def test_required_mode_needs_actual_attach_confirmation(admission, confirmation):
    root, ping, ack, connect = admission
    ping["custody_host"] = host_descriptor()
    ack["custody"] = host_descriptor()
    if confirmation is not None:
        ack["checkpoint_trace_bookkeeping"] = confirmation
    connection = connect()
    if type(confirmation) is int and confirmation == 1:
        assert conduit.attach(
            root / "custody.sock", "fixture", root, required_trace_bookkeeping=1,
        ) is connection
        assert not connection.closed
    else:
        with pytest.raises(ConnectionError, match="not confirmed") as failure:
            conduit.attach(root / "custody.sock", "fixture", root, required_trace_bookkeeping=1)
        assert not isinstance(failure.value, ec.SharingUnavailable)
        assert connection.closed
    assert [item["op"] for item in connection.requests] == ["ping", "attach"]
    assert connection.requests[-1]["checkpoint_trace_bookkeeping"] == 1


def test_required_trace_mode_preserves_normalized_context_during_handshake(admission, monkeypatch):
    root, ping, ack, connect = admission
    ping["custody_host"] = host_descriptor()
    ack.update(custody=host_descriptor(), checkpoint_trace_bookkeeping=1)
    monkeypatch.setenv("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", "1000")

    def changed(request):
        if request["op"] == "attach":
            monkeypatch.setenv("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", "2000")
    connection = connect(changed=changed)
    assert conduit.attach(
        root / "custody.sock", "fixture", root, required_trace_bookkeeping=1,
    ) is connection
    assert not connection.closed


@pytest.mark.parametrize("mode", [True, -1, 2, "1", None])
def test_invalid_required_mode_refuses_before_connect(admission, mode):
    root, _, _, connect = admission
    connection = connect()
    with pytest.raises(ValueError):
        conduit.attach(root / "custody.sock", "fixture", root, required_trace_bookkeeping=mode)
    assert not connection.requests


@pytest.mark.parametrize("reply", [b"", b"{invalid}\n", b'{"ok":true', b"[]\n"])
def test_lost_or_malformed_attach_outcome_is_terminal(admission, reply):
    root, _, _, connect = admission
    connection = connect()

    def changed(request):
        if request["op"] == "attach":
            connection.data = reply
    connection.changed = changed
    with pytest.raises(ConnectionError) as failure:
        conduit.attach(root / "custody.sock", "fixture", root)
    assert not isinstance(failure.value, ec.SharingUnavailable)
    assert connection.closed
    assert [item["op"] for item in connection.requests] == ["ping", "attach"]


@pytest.mark.parametrize("error_type", [CheckpointRefused, OSError])
def test_post_attach_identity_read_failure_is_terminal(admission, monkeypatch, error_type):
    root, _, _, connect = admission
    connection = connect()

    def changed(request):
        if request["op"] == "attach":
            def unavailable(*_args):
                raise error_type("fixture caller cwd disappeared")
            monkeypatch.setattr(conduit.Binding, "identity", unavailable)
    connection.changed = changed
    with pytest.raises(ConnectionError) as failure:
        conduit.attach(root / "custody.sock", "fixture", root)
    assert not isinstance(failure.value, ec.SharingUnavailable)
    assert isinstance(failure.value.__cause__, error_type)
    assert connection.closed
    assert [item["op"] for item in connection.requests] == ["ping", "attach"]
