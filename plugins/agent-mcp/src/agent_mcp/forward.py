"""Thin per-session forwarder: the light client half of the #744 multiplexer.

Today every MCP session Copilot opens spawns a full ``agent-mcp bridge``
interpreter -- it parses config, builds credential injectors, constructs the
decorator pipeline, and connects the upstream, all per session. Run at 5-10
concurrent worktree sessions with several servers each, that is dozens of heavy
Python interpreters resident at once (the dominant process-count / RAM
multiplier the effort set out to remove).

This module is the replacement child. When a resident ``agent-mcp serve``
session-host is available it **attaches** a full MCP session there (the host owns
the upstream + decorator pipeline; see :mod:`agent_mcp.serve`) and then does
nothing but **pump bytes**: client->server JSON-RPC from its stdin into the
socket, server->client messages from the socket to its stdout. It imports only
the standard library plus the stdlib-only :mod:`agent_mcp.ipc`; the heavy bridge
tree is imported **lazily, and only on the fallback path**. So the happy-path
forwarder is a near-empty interpreter -- that is where the RAM win comes from.

The multiplexer is always **optional with a correct inline fallback**: if no host
is advertised, or the attach is refused, or multiplexing is disabled
(``AGENT_MCP_NO_MULTIPLEX`` / ``AGENT_MCP_NO_SERVE``), the forwarder runs the
exact same in-process :class:`~agent_mcp.bridge.Bridge` a plain
``agent-mcp bridge`` would -- so behaviour is identical whether or not a host is
present. Once a session is *attached*, a mid-session host death is **not** retried
as a fresh direct bridge (the MCP session's ``initialize`` + notification stream
can't be replayed); the forwarder exits and the runtime respawns it, which then
takes the fallback or a fresh attach cleanly.
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
from pathlib import Path

from . import ipc

_FALSEY = {"0", "false", "off", "no", ""}

# How long to wait for a race-spawned serve host's socket to appear before
# giving up and running the session as a direct in-process bridge.
_ENSURE_SERVE_TIMEOUT = 5.0


def _env_disabled(name: str) -> bool:
    """True when ``name`` is set to a truthy (disabling) value."""
    raw = os.environ.get(name)
    return raw is not None and raw.strip().lower() not in _FALSEY


def _multiplex_enabled() -> bool:
    """Whether to attempt the serve-host attach (else always run direct)."""
    return not _env_disabled("AGENT_MCP_NO_MULTIPLEX")


def _ensure_serve_enabled() -> bool:
    """Whether the forwarder may spawn a serve host on demand when none is up.

    Disabled by ``AGENT_MCP_NO_ENSURE_SERVE`` (only attach to an already-running
    host) or by ``AGENT_MCP_NO_SERVE`` (bypass serve entirely).
    """
    return not (_env_disabled("AGENT_MCP_NO_ENSURE_SERVE")
                or os.environ.get("AGENT_MCP_NO_SERVE"))


def _looks_like_path(ref: str) -> bool:
    """Whether a bridge ref denotes a *file path* rather than a bare name.

    Mirrors :func:`agent_mcp.config.resolve_config_path`: a value is a path when
    it carries an explicit extension or contains a path separator. A bare bridge
    name (no separator, no suffix) is NOT a path -- both ends resolve it the same
    way from ``~/.agent-mcp/bridges`` / plugin roots -- so it must pass through
    untouched even if a same-named file happens to exist in the CWD.
    """
    return bool(Path(ref).suffix) or os.sep in ref or "/" in ref


def _abs_bridge_ref(bridge: str) -> str:
    """Resolve a config *file* ref to an absolute path so the host -- whose CWD
    may differ from ours -- loads the same config. A bare bridge *name* passes
    through unchanged (see :func:`_looks_like_path`).
    """
    if _looks_like_path(bridge):
        try:
            p = Path(bridge)
            if p.exists():
                return str(p.resolve())
        except OSError:
            pass
    return str(bridge)


def _serve_handle(socket_path: str | None) -> str:
    """The socket handle both the forwarder and a spawned host agree on."""
    return (socket_path or os.environ.get("AGENT_MCP_SERVE_SOCKET")
            or str(ipc.default_socket_path()))


def _spawn_serve_host(handle: str) -> None:
    """Spawn a detached ``agent-mcp serve`` host bound to ``handle``.

    Fully detached (its own session/process group) so it **outlives** this
    forwarder and serves later sessions too; the host installs no parent-death
    watchdog, and its single-instance lease collapses concurrent spawns from
    racing forwarders into exactly one live host. Best-effort: a failure to spawn
    just leaves the caller to fall back to a direct bridge.
    """
    import subprocess

    cmd = [sys.executable, "-m", "agent_mcp", "serve", "--socket", handle]
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "posix":
        kwargs["start_new_session"] = True
    else:  # Windows: detach from the console + this process's job/group
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        if flags:
            kwargs["creationflags"] = flags
    subprocess.Popen(cmd, **kwargs)  # noqa: S603 - fixed argv, our own runtime


def _ensure_serve(socket_path: str | None):
    """Return a live serve handle, spawning a host on demand; ``None`` on failure.

    Idempotent under concurrency: if a host is already advertised it is returned
    immediately; otherwise a detached host is spawned and we wait (bounded) for
    its socket to appear. Racing forwarders may each spawn -- the host lease
    guarantees only one wins and all of them converge on its socket.
    """
    import time

    handle = _serve_handle(socket_path)
    existing = ipc.serve_socket_if_available(handle)
    if existing is not None:
        return existing
    try:
        _spawn_serve_host(handle)
    except OSError:
        return None
    deadline = time.monotonic() + _ENSURE_SERVE_TIMEOUT
    while time.monotonic() < deadline:
        sock = ipc.serve_socket_if_available(handle)
        if sock is not None:
            return sock
        time.sleep(0.05)
    return None


def run(bridge: str, socket_path: str | None = None) -> int:
    """Forward one session through a resident serve host, or fall back to direct.

    Returns a process exit code. The direct fallback is taken (with the heavy
    bridge tree imported lazily) whenever multiplexing is disabled, no host can
    be reached or spawned, or the attach is refused.
    """
    if not _multiplex_enabled():
        return _run_direct(bridge)
    socket = ipc.serve_socket_if_available(socket_path)
    if socket is None and _ensure_serve_enabled():
        socket = _ensure_serve(socket_path)
    if socket is None:
        return _run_direct(bridge)
    result = asyncio.run(_attach_and_pump(socket, _abs_bridge_ref(bridge)))
    if result is None:
        # Attach was refused/unreachable before any MCP traffic -> safe to fall
        # back to a direct in-process bridge with no lost session state.
        return _run_direct(bridge)
    return result


def _run_direct(bridge: str) -> int:
    """The inline fallback: run the full in-process bridge (lazy heavy import)."""
    from .bridge import Bridge
    from .config import ConfigError, load_config

    try:
        cfg = load_config(bridge)
    except ConfigError as exc:
        print(f"agent-mcp forward: {exc}", file=sys.stderr)
        return 1
    return asyncio.run(Bridge(cfg).run())


async def _attach_and_pump(socket, ref: str) -> int | None:
    """Attach a session on the host and pump until either side ends.

    Returns ``None`` if the attach itself fails (caller falls back to direct);
    otherwise pumps to completion and returns ``0``.
    """
    try:
        reader, writer = await ipc.open_attached_session(socket, ref)
    except OSError:
        return None
    await _pump(reader, writer)
    return 0


async def _pump(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Bidirectionally pump line-delimited JSON-RPC between stdio and the socket.

    stdin is blocking, so it is drained on a daemon thread that hands lines to the
    event loop; the socket side is native asyncio. Either end reaching EOF (the
    client closing stdin on session end, or the host going away) stops the pump.
    A parent-death watchdog covers the Windows ``cmd``-shim grandchild case where
    stdin EOF never arrives (see :mod:`agent_mcp.watchdog`).
    """
    # watchdog is stdlib-only and light; import it here so the module's top-level
    # footprint stays minimal and the direct-fallback path doesn't pay for it.
    from .watchdog import install_parent_death_watchdog, reap_descendants_on_exit

    loop = asyncio.get_running_loop()
    stop = asyncio.Event()

    def _signal_stop() -> None:
        try:
            loop.call_soon_threadsafe(stop.set)
        except RuntimeError:
            pass  # loop already closing

    reap_descendants_on_exit()
    install_parent_death_watchdog(_signal_stop)

    in_queue: asyncio.Queue[str | None] = asyncio.Queue()

    def _stdin_reader() -> None:
        # The watchdog/EOF can end the pump (and close the loop) while this daemon
        # thread is still blocked on stdin; guard the wakeups so a late read
        # doesn't raise during interpreter shutdown.
        try:
            for line in sys.stdin:
                loop.call_soon_threadsafe(in_queue.put_nowait, line)
            loop.call_soon_threadsafe(in_queue.put_nowait, None)
        except (RuntimeError, ValueError):
            pass

    threading.Thread(target=_stdin_reader, name="agent-mcp-fwd-stdin",
                     daemon=True).start()

    async def _client_to_host() -> None:
        try:
            while True:
                line = await in_queue.get()
                if line is None:
                    break  # stdin EOF -> client closed the session
                writer.write(line.encode() if line.endswith("\n")
                             else (line + "\n").encode())
                try:
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    break
        finally:
            stop.set()

    async def _host_to_client() -> None:
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break  # host detached/died
                sys.stdout.write(line.decode(errors="replace"))
                sys.stdout.flush()
        finally:
            stop.set()

    tasks = [asyncio.create_task(_client_to_host()),
             asyncio.create_task(_host_to_client())]
    try:
        await stop.wait()
    finally:
        await ipc.aclose_writer(writer)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
