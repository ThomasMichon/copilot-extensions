"""Tests for the client-side endpoint discovery ladder (Phase 2 Stage B).

Clients resolve the daemon's *actual* endpoint from the rendezvous file
(env override -> rendezvous file -> WSL Windows-side file), falling back to
exactly today's fixed socket/port when nothing is advertised.
"""

from __future__ import annotations

import asyncio
import contextlib
import gc
import json
import socket
import sys
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from agent_vault import cli, config, rendezvous, service, winpipe
from agent_vault import extensions as ext
from agent_vault.extensions import ExtensionRegistry, TransportContext

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def empty_registry() -> Iterator[ExtensionRegistry]:
    """Install a fresh, empty, pre-loaded registry (no ambient transports)."""
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    yield reg
    ext.reset_registry()


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Isolate discovery from ambient config + env so tests are deterministic."""
    for var in (
        "AGENT_VAULT_ENDPOINT",
        "AGENT_VAULT_PORT",
        "AGENT_VAULT_HOME",
        "AGENT_VAULT_INSTALLATION_ID",
        "AGENT_VAULT_HOST",
        "AGENT_VAULT",
        "KPDB",
        "VAULT_GROUP",
        "AGENT_VAULT_CORE_RUN_DIR",
        "AGENT_VAULT_CACHE_DIR",
        "AGENT_VAULT_WINDOWS_RUN_DIR",
        "AGENT_VAULT_WINDOWS_MOUNT",
    ):
        monkeypatch.delenv(var, raising=False)
    # Point the global config at a nonexistent file so resolve_context() yields
    # pure defaults (port default, no kpdb).
    monkeypatch.setenv("AGENT_VAULT_CONFIG", str(tmp_path / "none.json"))
    # A WSL test host may have a real Windows-side daemon advertised under
    # /mnt/c. Keep that live endpoint out of tests unless a case opts in.
    monkeypatch.setenv(
        "AGENT_VAULT_WINDOWS_MOUNT", str(tmp_path / "no-windows-mount")
    )


@pytest.fixture
def run_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "run"
    d.mkdir()
    # Isolate via the env var so BOTH cli (config.run_dir()) and service
    # (its by-name-imported run_dir) resolve to this dir at call time.
    monkeypatch.setenv("AGENT_VAULT_RUN_DIR", str(d))
    return d


def _write_tcp(directory: Path, port: int | None, host: str = "127.0.0.1") -> Path:
    return rendezvous.write_endpoint(directory, "tcp", f"{host}:{port}")


def _write_pipe_with_tcp_alt(
    directory: Path, port: int, pipe: str = r"\\.\pipe\agent-vault", host: str = "127.0.0.1",
) -> Path:
    return rendezvous.write_endpoint(
        directory, "pipe", pipe, alt=[("tcp", f"{host}:{port}")]
    )


def _write_scoped_tcp(directory: Path, port: int | None, installation_id: str) -> Path:
    return rendezvous.write_endpoint(
        directory,
        "tcp",
        f"127.0.0.1:{port}",
        installation_id=installation_id,
    )


class _EchoServer:
    """A one-shot loopback server that replies with a canned JSON line."""

    def __init__(self, response: dict[str, Any], *, unix_path: str | None = None) -> None:
        self.response = response
        self.received: bytes | None = None
        self.address: str | tuple[str, int]
        self.sock: socket.socket
        if unix_path is not None:
            if sys.platform == "win32":
                raise RuntimeError("Unix socket fixture requires POSIX")
            self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.sock.bind(unix_path)
            self.address = unix_path
        else:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.sock.bind(("127.0.0.1", 0))
            self.address = self.sock.getsockname()
        self.sock.listen(1)
        self.port = int(self.address[1]) if unix_path is None else None
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
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

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self.sock.close()


# ---------------------------------------------------------------------------
# _discover_endpoint
# ---------------------------------------------------------------------------


def test_ping_transports_each_callers_current_vault_context(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    server = _EchoServer({"ok": True})
    try:
        _write_tcp(run_dir, server.port)
        for name in ("first.kdbx", "second.kdbx"):
            selected = str(tmp_path / name)
            monkeypatch.setenv("KPDB", selected)
            response = cli.send_command({"action": "ping"})
            assert response is not None and response["ok"]
            assert server.received is not None
            assert json.loads(server.received)["kpdb"] == selected
    finally:
        server.close()


@pytest.mark.parametrize("transport", ["unix", "pipe"])
def test_strict_failed_selection_never_contacts_legacy_tcp(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
    transport: str,
) -> None:
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setattr(cli, "IS_WINDOWS", transport == "pipe")
    endpoint = rendezvous.Endpoint.parse(
        r"pipe:\\.\pipe\example-unreachable" if transport == "pipe" else "unix:/example-unreachable",
    )
    monkeypatch.setattr(cli, "_discover_endpoint", lambda *_a, **_kw: endpoint)
    monkeypatch.setattr(cli, "pipe_send", lambda *_a, **_kw: None)
    monkeypatch.setattr(cli, "_send_socket", lambda *_a, **_kw: None)
    server = _EchoServer({"ok": True, "value": "unrelated authority"})
    monkeypatch.setenv("AGENT_VAULT_PORT", str(server.port))
    try:
        assert cli.send_command({"action": "get"}, strict_endpoint=True) is None
        assert server.received is None
        response = cli.send_command({"action": "get"})
        assert response is not None and response["value"] == "unrelated authority"
    finally:
        server.close()


def test_discover_returns_none_without_file(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "IS_WSL", False)
    assert cli._discover_endpoint(config.resolve_context()) is None


def test_discover_reads_live_local_tcp_file(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_discover_skips_stale_file(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A rendezvous file whose port has no listener is treated as not present."""
    monkeypatch.setattr(cli, "IS_WSL", False)
    # A port with (almost certainly) no listener -> connect_probe fails -> stale.
    _write_tcp(run_dir, 65533)
    assert cli._discover_endpoint(config.resolve_context()) is None


def test_discover_prefers_env_override(run_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setenv("AGENT_VAULT_ENDPOINT", "tcp:127.0.0.1:12321")
    _write_tcp(run_dir, 65533)  # would-be file, ignored in favor of override
    ep = cli._discover_endpoint(config.resolve_context())
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.tcp_host_port == ("127.0.0.1", 12321)
    assert ep.source == "env"


def test_discover_rejects_foreign_installation_endpoint(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cli, "IS_WSL", False)
    server = _EchoServer({"ok": True})
    try:
        _write_scoped_tcp(run_dir, server.port, "other-marketplace/agent-vault")
        monkeypatch.setenv(
            "AGENT_VAULT_INSTALLATION_ID",
            "expected-marketplace/agent-vault",
        )
        assert cli._discover_endpoint(config.resolve_context()) is None
    finally:
        server.close()


# ---------------------------------------------------------------------------
# WSL -> Windows rendezvous file
# ---------------------------------------------------------------------------


def test_windows_run_dirs_honors_explicit_override(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(tmp_path / "winrun"))
    assert cli._windows_run_dirs() == [tmp_path / "winrun"]


def test_windows_run_dirs_globs_profiles(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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


def test_read_windows_endpoint_tags_source(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    winrun = tmp_path / "winrun"
    winrun.mkdir()
    _write_tcp(winrun, 40404)
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(winrun))
    ep = cli._read_windows_endpoint()
    assert ep is not None
    assert ep.transport == "tcp"
    assert ep.tcp_host_port == ("127.0.0.1", 40404)
    assert ep.source == "windows"


def test_discover_falls_back_to_windows_file_under_wsl(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
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


def test_wsl_discovers_pipe_primary_then_resolves_tcp_alt(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """#3426: a WSL guest reads the Windows daemon's *pipe-primary* record and,
    unable to open the pipe, resolves the advertised TCP **alternate** -- the port
    the interop relay dials. Before this fix the pipe-only record left WSL with no
    port and the vault was unreachable."""
    monkeypatch.setattr(cli, "IS_WSL", True)
    winrun = tmp_path / "winrun"
    winrun.mkdir()
    _write_pipe_with_tcp_alt(winrun, 45454)
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(winrun))
    ep = cli._discover_endpoint(config.resolve_context())
    assert ep is not None
    assert ep.transport == "pipe"
    assert ep.source == "windows"
    # The pipe is unusable from WSL; usable() yields the TCP alt with the source
    # preserved so the caller keeps the cross-boundary host.
    tcp = ep.usable(lambda t: t == "tcp")
    assert tcp is not None
    assert tcp.tcp_host_port == ("127.0.0.1", 45454)
    assert tcp.source == "windows"



# ---------------------------------------------------------------------------
# cli.send_command integration
# ---------------------------------------------------------------------------


def test_send_command_discovers_tcp(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
) -> None:
    server = _EchoServer({"ok": True, "value": "hi"})
    try:
        _write_tcp(run_dir, server.port)
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True
    assert result["_transport"] == "discovered-tcp"


def test_send_command_omits_client_kpdb_for_windows_source(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    empty_registry: ExtensionRegistry,
) -> None:
    """#3426: a cross-boundary (windows-source) request must not forward the
    guest's kpdb/group -- the host daemon owns its own DB, and the guest's Linux
    path does not exist on the host (would fail 'KeePass database not found')."""
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setenv("KPDB", "/home/guest/OneDrive/Personal.kdbx")
    monkeypatch.setenv("VAULT_GROUP", "Guest")
    winrun = tmp_path / "winrun"
    winrun.mkdir()
    _write_pipe_with_tcp_alt(winrun, 45999)  # no listener -> falls to the capture xport
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(winrun))
    captured: dict[str, Any] = {}

    def _capture(
        request: dict[str, Any], timeout: float | None, ctx: TransportContext,
    ) -> dict[str, Any]:
        captured.update(request)
        return {"ok": True}

    empty_registry.register_transport(_capture, name="capture")
    resp = cli.send_command({"action": "has", "entry": "x"}, timeout=1.0)
    assert resp and resp.get("ok")
    assert "kpdb" not in captured
    assert "group" not in captured
    assert "vault" not in captured


def test_send_command_injects_client_kpdb_for_local(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
) -> None:
    """A local daemon *does* receive the caller's vault selection (unchanged)."""
    monkeypatch.setattr(cli, "IS_WSL", False)
    monkeypatch.setenv("KPDB", "/local/db.kdbx")
    # Isolate from any *real* daemon listening on the default port (e.g. a
    # deployed AgentVault service on the machine running the suite): force the
    # built-in transports to miss so the request reaches the capture stand-in.
    # kpdb injection happens in send_command *before* dispatch, so capturing via
    # the fallback still validates it.
    monkeypatch.setattr(cli, "_send_socket", lambda request, timeout=5.0, path=None: None)
    monkeypatch.setattr(cli, "_send_tcp", lambda request, host, port, timeout: None)
    captured: dict[str, Any] = {}

    def _capture(
        request: dict[str, Any], timeout: float | None, ctx: TransportContext,
    ) -> dict[str, Any]:
        captured.update(request)
        return {"ok": True}

    empty_registry.register_transport(_capture, name="capture")
    resp = cli.send_command({"action": "has", "entry": "x"}, timeout=1.0)
    assert resp and resp.get("ok")
    # kpdb is injected for a local daemon (exact value is path-normalized per OS).
    assert captured.get("kpdb")


def test_send_command_env_override_no_file(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
) -> None:
    server = _EchoServer({"ok": True})
    try:
        monkeypatch.setenv("AGENT_VAULT_ENDPOINT", f"tcp:127.0.0.1:{server.port}")
        result = cli.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["_transport"] == "discovered-tcp"


def test_send_command_legacy_tcp_fallback(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
) -> None:
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
def test_send_command_discovers_unix(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry, tmp_path: Path,
) -> None:
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
# ensure_service -- WSL must never spawn a shadowing LOCAL daemon (#3464)
# ---------------------------------------------------------------------------


def test_ensure_service_wsl_never_starts_local_for_windows_daemon(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """#3464(b): on WSL, when the reachable daemon is the cross-boundary Windows
    daemon (a pipe-primary Windows-side rendezvous record) but an interop ping
    momentarily fails, ``ensure_service`` must NOT spawn a local daemon (which
    would bind the unix socket + write a local rendezvous file that shadows the
    Windows daemon). It fails fast instead."""
    monkeypatch.setattr(cli, "IS_WSL", True)
    winrun = tmp_path / "winrun"
    winrun.mkdir()
    _write_pipe_with_tcp_alt(winrun, 45999)  # no listener -> ping "fails"
    monkeypatch.setenv("AGENT_VAULT_WINDOWS_RUN_DIR", str(winrun))
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: None)
    started = {"called": False}

    def _boom(*a: Any, **k: Any) -> bool:
        started["called"] = True
        return True

    monkeypatch.setattr(cli, "start_service", _boom)
    assert cli.ensure_service() is False
    assert started["called"] is False


def test_ensure_service_wsl_starts_local_without_windows_daemon(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic WSL install with no cross-boundary Windows daemon still starts a
    local daemon (the #3464 guard is scoped to windows-source endpoints only)."""
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: None)
    started = {"called": False}

    def _start(*a: Any, **k: Any) -> bool:
        started["called"] = True
        return True

    monkeypatch.setattr(cli, "start_service", _start)
    assert cli.ensure_service() is True
    assert started["called"] is True


def test_ensure_service_returns_true_when_ping_ok(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the daemon answers ping, ensure_service short-circuits to True and
    never consults discovery or start_service."""
    monkeypatch.setattr(cli, "IS_WSL", True)
    monkeypatch.setattr(cli, "send_command", lambda *a, **k: {"ok": True})

    def _boom(*a: Any, **k: Any) -> None:
        raise AssertionError("start_service must not be called when ping succeeds")

    monkeypatch.setattr(cli, "start_service", _boom)
    assert cli.ensure_service() is True


# ---------------------------------------------------------------------------
# service.send_command (internal lifecycle client) integration
# ---------------------------------------------------------------------------


def test_service_send_command_discovers_tcp(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = _EchoServer({"ok": True, "action": "pong"})
    try:
        _write_tcp(run_dir, server.port)
        result = service.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True


def test_service_send_command_legacy_when_no_file(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
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

    def __init__(self, pipe_path: str, response: dict[str, Any]) -> None:
        self.pipe_path = pipe_path
        self.response = response
        self.loop: asyncio.AbstractEventLoop | None = None
        self.handlers: set[asyncio.Task[None]] = set()
        self._ready = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("pipe server did not start")

    def _run(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("pipe fixture requires Windows")
        loop = asyncio.ProactorEventLoop()
        self.loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._start())
            loop.run_forever()
        finally:
            loop.close()

    async def _start(self) -> None:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            task = asyncio.current_task()
            assert task is not None
            self.handlers.add(task)
            try:
                await reader.readline()
                writer.write((json.dumps(self.response) + "\n").encode())
                await writer.drain()
            finally:
                try:
                    writer.close()
                    await writer.wait_closed()
                finally:
                    self.handlers.discard(task)

        self.servers = await winpipe.start_pipe_server(self.pipe_path, handle)
        self._ready.set()

    async def _shutdown(self) -> None:
        for server in self.servers:
            server.close()
        # Let pending accept/connection callbacks register their handlers before
        # waiting; writer.wait_closed() retains each transport through settlement.
        await asyncio.sleep(0)
        while self.handlers:
            await asyncio.gather(*self.handlers)

    def close(self) -> None:
        loop = self.loop
        if loop is not None and not loop.is_closed():
            shutdown = asyncio.run_coroutine_threadsafe(self._shutdown(), loop)
            try:
                shutdown.result(timeout=3)
            finally:
                loop.call_soon_threadsafe(loop.stop)
                self.thread.join(timeout=3)
            assert not self.thread.is_alive(), "pipe fixture thread did not stop"
            assert loop.is_closed(), "pipe fixture loop did not close"
            assert not self.handlers, "pipe fixture handlers did not settle"


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_send_command_discovers_pipe(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
) -> None:
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
    del server
    gc.collect()


@pytest.mark.skipif(sys.platform != "win32", reason="named pipes are Windows-only")
def test_send_command_pipe_falls_back_to_tcp(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch, empty_registry: ExtensionRegistry,
) -> None:
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
@pytest.mark.filterwarnings("error::pytest.PytestUnraisableExceptionWarning")
def test_service_send_command_discovers_pipe(
    run_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe = rf"\\.\pipe\agent-vault-svc-{uuid.uuid4().hex}"
    server = _PipeEcho(pipe, {"ok": True})
    try:
        rendezvous.write_endpoint(run_dir, "pipe", pipe)
        result = service.send_command({"action": "ping"})
    finally:
        server.close()
    assert result is not None
    assert result["ok"] is True
    del server
    gc.collect()
