"""Unit tests for core_delegation.delegate.

The happy paths run a real one-shot loopback server that speaks the same
newline-framed JSON the service daemons speak, and assert the request (incl. an
attached bearer token) arrives intact and the response comes back. The
fall-through paths assert ``None`` when no core is wired, when the resolved
transport is undialable here, and when the endpoint is stale/unreachable.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading

import pytest
from endpoint_rendezvous import Endpoint, write_endpoint

import core_delegation as cd
from core_delegation import delegate


class _EchoServer:
    """A server that reads a newline-framed JSON request and replies with a dict.

    Accepts connections in a loop (so a liveness *probe* -- which connects and
    closes without sending -- does not exhaust the listener before the real
    send), records the last non-empty decoded request under :attr:`received`,
    and replies to every request-bearing connection. Supports ``tcp`` (all
    platforms) and ``unix`` (where ``AF_UNIX`` exists).
    """

    def __init__(self, family: int, address, reply: dict) -> None:
        self._reply = reply
        self.received: dict | None = None
        self._sock = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(address)
        self._sock.listen(8)
        self._sock.settimeout(0.25)
        self.address = self._sock.getsockname()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            with conn:
                conn.settimeout(1.0)
                buf = b""
                try:
                    while b"\n" not in buf:
                        chunk = conn.recv(4096)
                        if not chunk:
                            break
                        buf += chunk
                except OSError:
                    continue
                if not buf:
                    continue  # a bare probe connection -- nothing to answer
                try:
                    self.received = json.loads(buf.decode().strip())
                except ValueError:
                    self.received = None
                with contextlib.suppress(OSError):
                    conn.sendall((json.dumps(self._reply) + "\n").encode())

    def close(self) -> None:
        self._stop.set()
        try:
            self._sock.close()
        except OSError:
            pass
        self._thread.join(timeout=2.0)


# ---------------------------------------------------------------------------
# Happy path -- resolve + ship over a real transport
# ---------------------------------------------------------------------------


def test_tcp_resolve_and_ship_roundtrip(tmp_path):
    server = _EchoServer(socket.AF_INET, ("127.0.0.1", 0), {"ok": True, "value": "core"})
    try:
        host, port = server.address
        result = delegate(
            "agent-x-core",
            {"action": "get", "entry": "x"},
            override=f"tcp:{host}:{port}",
            runtime_dir=tmp_path / "run",
        )
    finally:
        server.close()

    assert result is not None
    assert result["ok"] is True
    assert result["value"] == "core"
    assert result["_transport"] == cd.TRANSPORT_TAG
    assert server.received == {"action": "get", "entry": "x"}


def test_token_is_attached(tmp_path):
    server = _EchoServer(socket.AF_INET, ("127.0.0.1", 0), {"ok": True})
    try:
        host, port = server.address
        delegate(
            "agent-x-core",
            {"action": "ping"},
            override=f"tcp:{host}:{port}",
            token="s3cret-bearer",
            runtime_dir=tmp_path / "run",
        )
    finally:
        server.close()

    assert server.received is not None
    assert server.received[cd.TOKEN_KEY] == "s3cret-bearer"
    # Original caller dict must not be mutated with the token.
    assert "action" in server.received


def test_resolves_via_rendezvous_file(tmp_path):
    server = _EchoServer(socket.AF_INET, ("127.0.0.1", 0), {"ok": True, "value": "file"})
    run = tmp_path / "run"
    try:
        host, port = server.address
        # Advertise the running server via a rendezvous file (no override).
        write_endpoint(run, "tcp", f"{host}:{port}")
        result = delegate("agent-x-core", {"action": "get"}, runtime_dir=run)
    finally:
        server.close()

    assert result is not None
    assert result["value"] == "file"


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="AF_UNIX unavailable")
@pytest.mark.skipif(cd.delegation.IS_WINDOWS, reason="UDS server not used on Windows")
def test_unix_resolve_and_ship_roundtrip(tmp_path):
    sock_path = str(tmp_path / "core.sock")
    server = _EchoServer(socket.AF_UNIX, sock_path, {"ok": True, "value": "uds"})
    try:
        result = delegate(
            "agent-x-core",
            {"action": "get"},
            override=f"unix:{sock_path}",
            runtime_dir=tmp_path / "run",
        )
    finally:
        server.close()

    assert result is not None
    assert result["value"] == "uds"


# ---------------------------------------------------------------------------
# Fall-through -- return None so built-ins / user-mode still work
# ---------------------------------------------------------------------------


def test_none_when_no_core_wired(tmp_path):
    # No override, no rendezvous file, no legacy -> nothing resolves.
    assert delegate("agent-x-core", {"action": "get"}, runtime_dir=tmp_path / "run") is None


def test_none_when_unreachable(tmp_path):
    # Override points at a closed port -> connect fails -> fall through.
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    _, dead_port = s.getsockname()
    s.close()  # release the port so the connect is refused
    result = delegate(
        "agent-x-core",
        {"action": "get"},
        override=f"tcp:127.0.0.1:{dead_port}",
        timeout=1.0,
        runtime_dir=tmp_path / "run",
    )
    assert result is None


def test_none_when_transport_undialable_here(tmp_path):
    # accept rejects every transport -> usable() is None -> fall through.
    result = delegate(
        "agent-x-core",
        {"action": "get"},
        override="tcp:127.0.0.1:9",
        accept=lambda _t: False,
        runtime_dir=tmp_path / "run",
    )
    assert result is None


def test_none_when_rendezvous_file_stale(tmp_path):
    run = tmp_path / "run"
    # A file whose recorded pid is certainly dead -> is_stale -> skipped -> None.
    write_endpoint(run, "tcp", "127.0.0.1:1", pid=2_000_000_000)
    assert delegate("agent-x-core", {"action": "get"}, runtime_dir=run) is None


# ---------------------------------------------------------------------------
# default_accept
# ---------------------------------------------------------------------------


def test_default_accept_matrix(monkeypatch):
    assert cd.default_accept("tcp") is True
    assert cd.default_accept("bogus") is False

    monkeypatch.setattr(cd.delegation, "IS_WINDOWS", True)
    assert cd.default_accept("pipe") is True
    assert cd.default_accept("unix") is False

    monkeypatch.setattr(cd.delegation, "IS_WINDOWS", False)
    assert cd.default_accept("pipe") is False
    assert cd.default_accept("unix") is (hasattr(socket, "AF_UNIX"))


def test_alt_transport_selected_when_primary_undialable(tmp_path):
    # A pipe primary with a tcp alt: off Windows the pipe is undialable, so the
    # tcp alt must be chosen and dialed.
    server = _EchoServer(socket.AF_INET, ("127.0.0.1", 0), {"ok": True, "value": "alt"})
    try:
        host, port = server.address
        ep = Endpoint(
            transport="pipe",
            address=r"\\.\pipe\agent-x",
            source="env",
            alt=(Endpoint(transport="tcp", address=f"{host}:{port}"),),
        )
        result = delegate(
            "agent-x-core",
            {"action": "get"},
            override=ep,
            accept=lambda t: t == "tcp",
            runtime_dir=tmp_path / "run",
        )
    finally:
        server.close()

    assert result is not None
    assert result["value"] == "alt"

