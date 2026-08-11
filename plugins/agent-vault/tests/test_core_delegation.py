"""Tests for the agent-vault core-delegation extension (``core_ext``).

Verifies the wiring end to end through the CLI's client-transport seam:

- with **no core wired**, ``send_command`` falls through to the built-ins (the
  fallback transport returns ``None``) -- standalone-reachability preserved;
- with a core **wired** (a real loopback listener advertised via
  ``AGENT_VAULT_CORE_ENDPOINT``) and the built-ins failing, the request is
  delegated to the core and the bearer token is attached on the wire.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading

from agent_vault import cli, core_ext
from agent_vault import extensions as ext
from agent_vault.extensions import ExtensionRegistry, TransportContext, reset_registry


class _EchoServer:
    """Loopback server: reads one newline-framed JSON request, replies with a dict."""

    def __init__(self, reply: dict) -> None:
        self._reply = reply
        self.received: dict | None = None
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.25)
        self.host, self.port = self._sock.getsockname()
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
                    continue
                with contextlib.suppress(ValueError):
                    self.received = json.loads(buf.decode().strip())
                with contextlib.suppress(OSError):
                    conn.sendall((json.dumps(self._reply) + "\n").encode())

    def close(self) -> None:
        self._stop.set()
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=2.0)


def _install_registry() -> ExtensionRegistry:
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    return reg


def _clean_env(monkeypatch, tmp_path) -> None:
    for var in ("KPDB", "AGENT_VAULT", "VAULT_GROUP", "AGENT_VAULT_PORT", "AGENT_VAULT_ENDPOINT"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "no-config.json"))
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(tmp_path / "run"))
    # Point the core runtime dir at an empty dir so, absent an override, no core
    # is discovered.
    monkeypatch.setenv("AGENT_VAULT_CORE_RUN_DIR", str(tmp_path / "core"))
    monkeypatch.delenv("AGENT_VAULT_CORE_ENDPOINT", raising=False)
    monkeypatch.delenv("AGENT_VAULT_CORE_TOKEN", raising=False)


def test_register_adds_fallback_transport():
    reg = ExtensionRegistry()
    core_ext.register(reg)
    try:
        names = [r.name for r in reg.transports]
        assert "core-delegation" in names
        # Registered as a fallback (after the built-ins), not before_builtin.
        assert reg._transports_before == []
        assert [r.name for r in reg._transports_after] == ["core-delegation"]
    finally:
        pass


def test_no_core_wired_falls_through(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    reg = _install_registry()
    core_ext.register(reg)
    try:
        # Built-ins fail; with no core wired the fallback returns None too.
        monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
        monkeypatch.setattr(cli, "_send_tcp", lambda req, host, port, timeout: None)
        assert cli.send_command({"action": "ping"}) is None
    finally:
        reset_registry()


def test_wired_core_receives_delegated_request_with_token(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    server = _EchoServer({"ok": True, "value": "from-core"})
    reg = _install_registry()
    core_ext.register(reg)
    try:
        monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
        monkeypatch.setattr(cli, "_send_tcp", lambda req, host, port, timeout: None)
        monkeypatch.setenv("AGENT_VAULT_CORE_ENDPOINT", f"tcp:{server.host}:{server.port}")
        monkeypatch.setenv("AGENT_VAULT_CORE_TOKEN", "bearer-xyz")

        result = cli.send_command({"action": "get", "entry": "x"})
        assert result is not None
        assert result["ok"] is True
        assert result["value"] == "from-core"
        assert result["_transport"] == "ext:core-delegation"
    finally:
        reset_registry()
        server.close()

    assert server.received is not None
    assert server.received["action"] == "get"
    assert server.received["_token"] == "bearer-xyz"


def test_core_transport_returns_none_when_unset(monkeypatch, tmp_path):
    _clean_env(monkeypatch, tmp_path)
    ctx = TransportContext(kpdb=None, group=None, vault_name=None, port=9999)
    assert core_ext.core_transport({"action": "ping"}, 2.0, ctx) is None


# ---------------------------------------------------------------------------
# S3 -- user-mode / standalone default is the guaranteed behavior
# ---------------------------------------------------------------------------


class _HangServer:
    """Accepts a connection and holds it open without ever replying.

    Models a wired-but-misbehaving core: reachable (the connect succeeds) but it
    never sends a response. A correct client must time out and give up, never
    block forever.
    """

    def __init__(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.25)
        self.host, self.port = self._sock.getsockname()
        self._held: list[socket.socket] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self._sock.accept()
            except (TimeoutError, OSError):
                continue
            self._held.append(conn)  # hold it open; never reply

    def close(self) -> None:
        self._stop.set()
        for c in self._held:
            with contextlib.suppress(OSError):
                c.close()
        with contextlib.suppress(OSError):
            self._sock.close()
        self._thread.join(timeout=2.0)


def test_core_timeout_is_bounded(monkeypatch):
    # An unbounded (None) or oversized caller timeout is clamped to the cap; a
    # smaller caller timeout is respected as-is.
    monkeypatch.delenv(core_ext.CORE_TIMEOUT_ENV, raising=False)
    assert core_ext._core_timeout(None) == core_ext.DEFAULT_CORE_TIMEOUT
    assert core_ext._core_timeout(1000.0) == core_ext.DEFAULT_CORE_TIMEOUT
    assert core_ext._core_timeout(2.5) == 2.5

    monkeypatch.setenv(core_ext.CORE_TIMEOUT_ENV, "3")
    assert core_ext._core_timeout(None) == 3.0
    assert core_ext._core_timeout(100.0) == 3.0

    # A malformed / non-positive override falls back to the default cap.
    monkeypatch.setenv(core_ext.CORE_TIMEOUT_ENV, "not-a-number")
    assert core_ext._core_timeout(None) == core_ext.DEFAULT_CORE_TIMEOUT
    monkeypatch.setenv(core_ext.CORE_TIMEOUT_ENV, "0")
    assert core_ext._core_timeout(None) == core_ext.DEFAULT_CORE_TIMEOUT


def test_hanging_core_does_not_hang_the_cli(monkeypatch, tmp_path):
    # A wired-but-hanging core, reached with the CLI's unbounded (None) timeout,
    # must NOT block: the bounded cap forces a fast give-up and fall-through.
    _clean_env(monkeypatch, tmp_path)
    server = _HangServer()
    reg = _install_registry()
    core_ext.register(reg)
    try:
        monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
        monkeypatch.setattr(cli, "_send_tcp", lambda req, host, port, timeout: None)
        monkeypatch.setenv("AGENT_VAULT_CORE_ENDPOINT", f"tcp:{server.host}:{server.port}")
        monkeypatch.setenv(core_ext.CORE_TIMEOUT_ENV, "1")  # 1s cap

        import time as _time

        start = _time.monotonic()
        # cmd_get-style unbounded call: the cap must still bound it.
        result = cli.send_command({"action": "get", "entry": "x"}, timeout=None)
        elapsed = _time.monotonic() - start

        assert result is None  # gave up, did not hang
        assert elapsed < 10.0  # bounded by the ~1s cap, not the unbounded caller
    finally:
        reset_registry()
        server.close()


def test_wired_core_never_preempts_a_working_local_daemon(monkeypatch, tmp_path):
    # With a core wired AND a working local built-in transport, the local daemon
    # wins and the core is never consulted -- standalone/local is the default;
    # a wired core is purely additive.
    _clean_env(monkeypatch, tmp_path)
    server = _EchoServer({"ok": True, "value": "from-core"})
    reg = _install_registry()
    core_ext.register(reg)
    try:
        monkeypatch.setattr(cli, "_send_socket", lambda req, timeout=5.0: None)
        monkeypatch.setattr(
            cli, "_send_tcp",
            lambda req, host, port, timeout: {"ok": True, "value": "from-local"},
        )
        monkeypatch.setenv("AGENT_VAULT_CORE_ENDPOINT", f"tcp:{server.host}:{server.port}")

        result = cli.send_command({"action": "get", "entry": "x"})
        assert result is not None
        assert result["value"] == "from-local"  # local daemon served it
    finally:
        reset_registry()
        server.close()

    assert server.received is None  # the wired core was never consulted
