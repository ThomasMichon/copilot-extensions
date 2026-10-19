"""An opted-in transport deadline is absolute, including fragmented responses."""

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from work_coalescing_singleton import client


class _Socket:
    def __init__(self, clock):
        self.clock, self.timeouts, self.responses = clock, [], []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def sendall(self, _):
        self.clock[0] += 1

    def shutdown(self, _):
        pass

    def recv(self, _):
        chunk, elapsed = self.responses.pop(0)
        self.clock[0] += elapsed
        return chunk


def _connection(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(client, "time", SimpleNamespace(
        monotonic=lambda: clock[0], time=lambda: 1_000.0,
    ))
    sock = _Socket(clock)
    dials = []

    def connect(address, *, timeout):
        dials.append(timeout)
        clock[0] += 1
        return sock

    monkeypatch.setattr(client.socket, "create_connection", connect)
    return clock, sock, dials


def _request(**kwargs):
    return client.request(
        "127.0.0.1", 12345, "test-owned-token", kind="health", key="test", payload={},
        request_deadline_s=30, **kwargs,
    )


def test_absolute_completion_charges_dial_send_and_each_response_fragment(monkeypatch):
    _, sock, dials = _connection(monkeypatch)
    sock.responses = [(b'{"version":1,', 1), (b'"result":{"ok":true}}\n', 0)]
    assert _request(completion_timeout_s=5) == {"ok": True}
    assert dials == [5]
    assert sock.timeouts == [4, 3, 2]


def test_fragments_cannot_reset_completion_deadline(monkeypatch):
    _, sock, _ = _connection(monkeypatch)
    sock.responses = [(b'{"version":1,', 3), (b'"result":{}}\n', 0)]
    with pytest.raises(client.DaemonUnavailable, match="completion deadline"):
        _request(completion_timeout_s=5)
    assert len(sock.responses) == 1


def test_late_complete_response_does_not_become_success(monkeypatch):
    _, sock, _ = _connection(monkeypatch)
    sock.responses = [(json.dumps({"version": 1, "result": {"ok": True}}).encode() + b"\n", 4)]
    with pytest.raises(client.DaemonUnavailable, match="completion deadline"):
        _request(completion_timeout_s=5)


def test_default_transport_preserves_ordinary_server_deadline_grace(monkeypatch):
    _, sock, dials = _connection(monkeypatch)
    sock.responses = [(b'{"version":1,', 1), (b'"result":{}}\n', 0)]
    assert _request() == {}
    assert dials == [31]
    assert sock.timeouts == [31, 31, 31]


def test_real_tcp_incomplete_response_does_not_use_ordinary_extra_grace():
    release = threading.Event()
    errors = []
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        listener.settimeout(3)
        host, port = listener.getsockname()

        def serve():
            try:
                with listener.accept()[0] as connection:
                    connection.settimeout(3)
                    assert connection.recv(65536)
                    connection.sendall(b'{"version":1,')
                    assert release.wait(timeout=3)
            except BaseException as error:
                errors.append(error)

        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            started = time.monotonic()
            with pytest.raises(client.DaemonUnavailable):
                client.request(
                    host, port, "test-owned-token", kind="health", key="test", payload={},
                    request_deadline_s=30, completion_timeout_s=0.2,
                )
            assert time.monotonic() - started < 1
        finally:
            release.set()
            thread.join(timeout=3)
        assert not thread.is_alive()
        assert not errors


@pytest.mark.parametrize("invalid", [0, -1, True, "5", float("nan"), float("inf")])
def test_invalid_completion_budget_refuses_before_dial(monkeypatch, invalid):
    monkeypatch.setattr(client.socket, "create_connection", lambda *_args, **_kwargs: pytest.fail("no dial"))
    with pytest.raises(ValueError, match="finite and positive"):
        _request(completion_timeout_s=invalid)
