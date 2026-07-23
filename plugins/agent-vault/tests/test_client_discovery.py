"""Tests for the client-side endpoint discovery ladder (Phase 2 Stage B).

Clients resolve the daemon's *actual* endpoint from the rendezvous file
(env override -> rendezvous file -> WSL Windows-side file), falling back to
exactly today's fixed socket/port when nothing is advertised.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import sys
import threading
import uuid

import pytest

from agent_vault import cli, config, rendezvous, service, winpipe
from agent_vault import extensions as ext
from agent_vault.extensions import ExtensionRegistry

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_registry():
    """Install a fresh, empty, pre-loaded registry (no ambient transports)."""
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    yield reg
    ext.reset_registry()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch, tmp_path):
    """Isolate discovery from ambient config + env so tests are deterministic."""
    for var in (
        "AGENT_VAULT_ENDPOINT",
        "AGENT_VAULT_PORT",
        "AGENT_VAULT_HOST",
        "AGENT_VAULT",
        "KPDB",
        "VAULT_GROUP",
        "AGENT_VAULT_WINDOWS_RUN_DIR",
        "AGENT_VAULT_WINDOWS_MOUNT",
    ):
        monkeypatch.delenv(var, raising=False)
    # Point the global config at a nonexistent file so resolve_context() yields
    # pure defaults (port default, no kpdb).
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "none.json"))


@pytest.fixture
def run_dir(tmp_path, monkeypatch):
    d = tmp_path / "run"
    d.mkdir()
    # Isolate via the env var so BOTH cli (config.run_dir()) and service
    # (its by-name-imported run_dir) resolve to this dir at call time.
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(d))
    return d


def _write_tcp(directory, port, host="127.0.0.1"):
    return rendezvous.write_endpoint(directory, "tcp", f"{host}:{port}")


class _EchoServer:
    """A one-shot loopback server that replies with a canned JSON line."""

    def __init__(self, response, *, unix_path=None):
        self.response = response
        self.received = None
        if unix_path is not None:
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(unix_path)
            self.address = unix_path
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.bind(("127.0.0.1", 0))
            self.address = self.sock.getsockname()
        self.sock.listen(1)
        self.port = self.address[1] if unix_path is None else None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        # Serve in a loop: connect_probe opens (and closes) a connection before
        # the real send, so a one-shot accept would starve the actual request.
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            try:
                data = conn.recv(4096)
                if data:
                    self.received = data
                    conn.sendall((json.dumps(self.response) + "\n").encode())
            except OSError:
                pass
            finally:
                conn.close()

    def close(self):
        with contextlib.suppress(OSError):
            self.sock.close()


# ---------------------------------------------------------------------------
# _discover_endpoint
# ---------------------------------------------------------------------------


def test_discover_returns_none_without_file(run_dir, monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    assert cli._discover_endpoint(config.resolve_context()) is None


def test_discover_reads_live_local_tcp_file(run_dir, monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    server = _EchoServer({"ok": True})
    try:
        _write_tcp(run_dir, server.port)
        ep = cli._discover_endpoint(config.resolve_context())
        assert ep is not None
        assert ep.transport == "tcp"
        assert ep.tcp_host_port == ("127.0.0.1", server.port)
    finally:
        server.close()


def test_discover_skips_stale_file(run_dir, monkeypatch):
    """A rendezvous file whose port has no listener is treated as not present."""
    monkeypatch.setattr(cli, "IS_WSL", False)
    # A port with (almost certainly) no listener -> connect_probe fails -> stale.
    _write_tcp(run_dir, 65533)
    assert cli._discover_endpoint(config.resolve_context()) is None


def test_discover_prefers_env_override(run_dir, monkeypatch):
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setenv("AGENT_VAULT_ENDPOINT", "tcp:127.0.0.1:12321")
    _write_tcp(run_dir, 65533)  # would-be file, ignored in favor of override
    ep = cli._discover_endpoint(config.resolve_context())
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.tcp_host_port == ("127.0.0.1", 12321)
    assert ep.source == "env"


# ---------------------------------------------------------------------------
# WSL -> Windows rendezvous file
# ---------------------------------------------------------------------------


def test_windows_run_dirs_honors_explicit_override(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(tmp_path / "winrun"))
    assert cli._windows_run_dirs() == [tmp_path / "winrun"]


def test_windows_run_dirs_globs_profiles(monkeypatch, tmp_path):
    mount = tmp_path / "mnt_c"
    users = mount / "Users"
    # A real profile with an endpoint file, plus a skipped system profile.
    good = users / "example-user" / ".agent-vault" / "run"
    good.mkdir(parents=True)
    (good / "endpoint.json").write_text("{}", encoding="utf-8")
    skipped = users / "Public" / ".agent-vault" / "run"
    skipped.mkdir(parents=True)
    (skipped / "endpoint.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_MOUNT", str(mount))
    dirs = cli._windows_run_dirs()
    assert good in dirs
    assert skipped not in dirs


def test_read_windows_endpoint_tags_source(monkeypatch, tmp_path):
    winrun = tmp_path / "winrun"
    winrun.mkdir()
    _write_tcp(winrun, 40404)
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(winrun))
    ep = cli._read_windows_endpoint()
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.tcp_host_port == ("127.0.0.1", 40404)
    assert ep.source == "windows"


def test_discover_falls_back_to_windows_file_under_wsl(run_dir, monkeypatch, tmp_path):
    """No local file + WSL -> read the Windows-side rendezvous file (unprobed)."""
    monkeypatch.setattr(cli, "IS_WSL", True)
    winrun = tmp_path / "winrun"
    winrun.mkdir()
    _write_tcp(winrun, 65533)  # no listener, but Windows-side reads skip probe
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(winrun))
    ep = cli._discover_endpoint(config.resolve_context())
    assert ep is not None
    assert ep.source == "windows"
    assert ep.tcp_host_port == ("127.0.0.1", 65533)


# ---------------------------------------------------------------------------
# cli.send_command integration
# ---------------------------------------------------------------------------


def test_send_command_discovers_tcp(run_dir, monkeypatch, empty_registry):
    server = _EchoServer({"ok": True, "value": "hi"})
    try:
        _write_tcp(run_dir, server.port)
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True
    assert result["_transport"] == "discovered-tcp"


def test_send_command_env_override_no_file(run_dir, monkeypatch, empty_registry):
    server = _EchoServer({"ok": True})
    try:
        monkeypatch.setenv("AGENT_VAULT_ENDPOINT", f"tcp:127.0.0.1:{server.port}")
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["_transport"] == "discovered-tcp"


def test_send_command_legacy_tcp_fallback(run_dir, monkeypatch, empty_registry):
    """No rendezvous file -> today's legacy dial (fixed/env TCP port)."""
    server = _EchoServer({"ok": True})
    try:
        # AGENT_VAULT_PORT makes the port source non-default, so the legacy path
        # skips the unix socket and dials TCP at this port (exactly today).
        monkeypatch.setenv("AGENT_VAULT_PORT", str(server.port))
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["_transport"] == "tcp"


@pytest.mark.skipif(sys.platform == "win32", reason="Unix sockets are POSIX-only")
def test_send_command_discovers_unix(run_dir, monkeypatch, empty_registry, tmp_path):
    sock_path = str(tmp_path / "disc.sock")
    server = _EchoServer({"ok": True}, unix_path=sock_path)
    try:
        rendezvous.write_endpoint(run_dir, "unix", sock_path)
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["_transport"] == "discovered-unix"


# ---------------------------------------------------------------------------
# service.send_command (internal lifecycle client) integration
# ---------------------------------------------------------------------------


def test_service_send_command_discovers_tcp(run_dir, monkeypatch):
    server = _EchoServer({"ok": True, "action": "pong"})
    try:
        _write_tcp(run_dir, server.port)
        result = service.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True


def test_service_send_command_legacy_when_no_file(run_dir, monkeypatch):
    """No file -> service falls back to the configured/legacy TCP port."""
    server = _EchoServer({"ok": True})
    try:
        monkeypatch.setenv("AGENT_VAULT_PORT", str(server.port))
        result = service.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True


# ---------------------------------------------------------------------------
# Named-pipe discovery (Stage C, Windows only)
# ---------------------------------------------------------------------------


class _PipeEcho:
    """Proactor-loop named-pipe server in a thread; echoes a canned reply."""

    def __init__(self, pipe_path, response):
        self.pipe_path = pipe_path
        self.response = response
        self.loop = None
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("pipe server did not start")

    def _run(self):
        self.loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._start())
        self.loop.run_forever()

    async def _start(self):
        async def handle(reader, writer):
            try:
                await reader.readline()
                writer.write((json.dumps(self.response) + "\n").encode())
                await writer.drain()
            finally:
                writer.close()

        self.servers = await winpipe.start_pipe_server(self.pipe_path, handle)
        self._ready.set()

    def close(self):
        if self.loop is not None:
            def _shutdown():
                for s in getattr(self, "servers", []):
                    with contextlib.suppress(Exception):
                        s.close()
                self.loop.stop()

            self.loop.call_soon_threadsafe(_shutdown)
            self.thread.join(timeout=3)
            with contextlib.suppress(Exception):
                self.loop.close()


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_send_command_discovers_pipe(run_dir, monkeypatch, empty_registry):
    pipe = rf"\\.\pipe\agent-vault-test-{uuid.uuid4().hex}"
    server = _PipeEcho(pipe, {"ok": True, "value": "pong"})
    try:
        rendezvous.write_endpoint(run_dir, "pipe", pipe)
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True
    assert result["_transport"] == "discovered-pipe"


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_send_command_pipe_falls_back_to_tcp(run_dir, monkeypatch, empty_registry):
    """A discovered pipe with no server falls through to the legacy TCP dial."""
    dead_pipe = rf"\\.\pipe\agent-vault-dead-{uuid.uuid4().hex}"
    rendezvous.write_endpoint(run_dir, "pipe", dead_pipe)
    server = _EchoServer({"ok": True})
    try:
        monkeypatch.setenv("AGENT_VAULT_PORT", str(server.port))
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    # Pipe dial failed -> legacy TCP served it.
    assert result["_transport"] == "tcp"


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_service_send_command_discovers_pipe(run_dir, monkeypatch):
    pipe = rf"\\.\pipe\agent-vault-svc-{uuid.uuid4().hex}"
    server = _PipeEcho(pipe, {"ok": True})
    try:
        rendezvous.write_endpoint(run_dir, "pipe", pipe)
        result = service.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True
