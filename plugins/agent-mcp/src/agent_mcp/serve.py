"""Resident warmth tier: a local daemon holding warm upstream MCP sessions.

``agent-mcp call`` and the materialized stubs pay a per-call upstream cold-start
(spawn ``npx``/``bunx``/``node`` + the MCP ``initialize`` handshake) on *every*
invocation. ``agent-mcp serve`` keeps one warm :class:`OneShotSession` per bridge
and answers ``call``/``list`` requests over a local IPC socket, so repeated
calls skip the cold-start entirely.

The IPC transport is chosen per platform. On POSIX the daemon binds an
**AF_UNIX** socket at the configured path, gated by ordinary filesystem
permissions. Windows' asyncio event loops don't implement AF_UNIX, so there the
daemon binds a **loopback TCP** listener (``127.0.0.1:0``) and publishes the
chosen port plus a per-daemon auth **token** in an ``<socket>.endpoint`` sidecar
file (port-discovery); the client reads that file to dial the port and presents
the token on every request, reproducing the single-user gating the unix socket's
file permissions provide. Both ends derive the transport from the same
:data:`_HAS_AF_UNIX` probe, so they always agree.

The client (``agent-mcp call`` and thus every materialized stub, unchanged)
transparently falls back to the stateless one-shot path when the daemon is
absent, so ``serve`` is an **optional accelerator, never a dependency**.

The key observation is that :class:`OneShotSession` is already 90% of a warm
session -- it connects, runs ``initialize``, and can ``call_tool`` repeatedly; it
only tears down on ``__aexit__``. :class:`WarmPool` keeps a set of them open,
keyed by bridge config, reused across requests.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import time
from pathlib import Path

from .client import (
    OneShotSession,
    UpstreamError,
    result_is_error,
    result_structured,
    result_text,
)
from .config import BridgeConfig, load_config
from .ipc import (
    _HAS_AF_UNIX,
    _TCP_HOST,
    _connect,
    _endpoint_path,
    _read_endpoint,
    call_via_socket,
    default_socket_path,
    open_attached_session,
    request_via_socket,
    serve_socket_if_available,
)
from .session import BridgeSession

log = logging.getLogger("agent-mcp.serve")

_SWEEP_INTERVAL = 30.0  # seconds between idle sweeps
_DEFAULT_IDLE_TIMEOUT = 300.0  # evict a warm session unused this long

# The serve IPC transport + client-attach primitives now live in agent_mcp.ipc
# (stdlib-only) so the thin forwarder can import them without dragging in this
# module's heavy bridge tree. They are re-exported here for backward
# compatibility -- callers and tests still ``from agent_mcp.serve import ...``.
__all__ = [
    "_HAS_AF_UNIX",
    "_TCP_HOST",
    "Server",
    "WarmPool",
    "_connect",
    "_endpoint_path",
    "_read_endpoint",
    "call_via_socket",
    "default_socket_path",
    "open_attached_session",
    "request_via_socket",
    "serve_socket_if_available",
]


class _WarmEntry:
    """One warm upstream session plus its serialization lock + idle clock."""

    __slots__ = ("last_used", "lock", "session")

    def __init__(self, session: OneShotSession) -> None:
        self.session = session
        self.lock = asyncio.Lock()
        self.last_used = time.monotonic()


class WarmPool:
    """A pool of warm :class:`OneShotSession`s keyed by bridge config path.

    Calls to a given bridge are **serialized** by a per-entry lock (an MCP stdio
    session is a single JSON-RPC pipe; serializing is correct without request
    multiplexing). Distinct bridges run concurrently. A session that errors at
    the transport level is evicted so the next call transparently reopens it.
    """

    def __init__(self, *, idle_timeout: float = _DEFAULT_IDLE_TIMEOUT) -> None:
        self._entries: dict[str, _WarmEntry] = {}
        self._idle_timeout = idle_timeout
        self._guard = asyncio.Lock()  # guards the open/evict of the entry map

    async def _entry_for(self, key: str, cfg: BridgeConfig) -> _WarmEntry:
        entry = self._entries.get(key)
        if entry is not None:
            return entry
        # Double-checked under the guard so two concurrent first-calls to the
        # same bridge open exactly one session.
        async with self._guard:
            entry = self._entries.get(key)
            if entry is not None:
                return entry
            session = OneShotSession(cfg)
            await session.__aenter__()
            entry = _WarmEntry(session)
            self._entries[key] = entry
            log.info("warm session opened: %s", key)
            return entry

    async def call(self, key: str, cfg: BridgeConfig, tool: str, arguments: dict) -> dict:
        entry = await self._entry_for(key, cfg)
        async with entry.lock:
            entry.last_used = time.monotonic()
            try:
                return await entry.session.call_tool(tool, arguments)
            except UpstreamError:
                # A protocol/tool-level error is a normal result path -- the
                # session is still healthy, keep it warm.
                raise
            except Exception:
                # A transport-level failure likely means the upstream died;
                # evict so the next call reopens a fresh session.
                await self._evict(key)
                raise

    async def list(self, key: str, cfg: BridgeConfig) -> list[dict]:
        entry = await self._entry_for(key, cfg)
        async with entry.lock:
            entry.last_used = time.monotonic()
            try:
                return await entry.session.list_tools()
            except UpstreamError:
                raise
            except Exception:
                await self._evict(key)
                raise

    async def _evict(self, key: str) -> None:
        async with self._guard:
            entry = self._entries.pop(key, None)
        if entry is not None:
            with contextlib.suppress(Exception):
                await entry.session.__aexit__(None, None, None)
            log.info("warm session closed: %s", key)

    async def sweep_idle(self) -> None:
        now = time.monotonic()
        stale = [
            key for key, e in list(self._entries.items())
            if now - e.last_used > self._idle_timeout
        ]
        for key in stale:
            await self._evict(key)

    async def close_all(self) -> None:
        for key in list(self._entries):
            await self._evict(key)

    @property
    def size(self) -> int:
        return len(self._entries)


class Server:
    """A local-IPC server fronting a :class:`WarmPool` (AF_UNIX / loopback TCP)."""

    def __init__(self, socket_path: str | Path, *, pool: WarmPool | None = None,
                 idle_timeout: float = _DEFAULT_IDLE_TIMEOUT) -> None:
        self.socket_path = Path(socket_path)
        self.pool = pool or WarmPool(idle_timeout=idle_timeout)
        self._stop = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        # Per-daemon auth token, minted only for the loopback-TCP transport
        # (``None`` on AF_UNIX, where filesystem permissions gate access).
        self._token: str | None = None

    async def _handle(self, reader: asyncio.StreamReader,
                      writer: asyncio.StreamWriter) -> None:
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                try:
                    req = json.loads(line)
                except (ValueError, TypeError):
                    await self._send(writer, {"ok": False, "error": "invalid JSON"})
                    continue
                # A full-session attach hands the whole connection over to a
                # resident BridgeSession (the work-coalescing multiplexer, #744):
                # after this the stream carries raw MCP JSON-RPC, not ops.
                if isinstance(req, dict) and req.get("op") == "attach":
                    await self._run_session(req, reader, writer)
                    return
                resp = await self._dispatch(req)
                await self._send(writer, resp)
                if req.get("op") == "shutdown" and resp.get("ok"):
                    self._stop.set()
                    break
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    async def _dispatch(self, req: dict) -> dict:
        # Loopback-TCP transport: authorize every request against the daemon's
        # token (a no-op on AF_UNIX, where ``_token`` is None).
        if self._token is not None and req.get("token") != self._token:
            return {"ok": False, "error": "unauthorized"}
        op = req.get("op")
        if op == "ping":
            return {"ok": True, "pong": True, "sessions": self.pool.size}
        if op == "shutdown":
            return {"ok": True}
        if op in ("call", "list"):
            bridge = req.get("bridge")
            if not bridge:
                return {"ok": False, "error": "missing 'bridge'"}
            try:
                cfg = load_config(bridge)
            except Exception as exc:
                return {"ok": False, "error": f"config: {exc}"}
            key = str(bridge)
            try:
                if op == "list":
                    tools = await self.pool.list(key, cfg)
                    return {"ok": True, "tools": tools}
                tool = req.get("tool")
                if not tool:
                    return {"ok": False, "error": "missing 'tool'"}
                result = await self.pool.call(key, cfg, tool, req.get("arguments") or {})
                return {
                    "ok": True,
                    "content": result_text(result),
                    "structured": result_structured(result),
                    "isError": result_is_error(result),
                }
            except UpstreamError as exc:
                return {"ok": False, "error": str(exc)}
            except Exception as exc:
                return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": False, "error": f"unknown op: {op!r}"}

    @staticmethod
    async def _send(writer: asyncio.StreamWriter, obj: dict) -> None:
        writer.write((json.dumps(obj) + "\n").encode())
        await writer.drain()

    async def _run_session(self, req: dict, reader: asyncio.StreamReader,
                           writer: asyncio.StreamWriter) -> None:
        """Host a full MCP session for one attached client over this connection.

        After the ``attach`` op is accepted, the connection stops speaking the
        op protocol: subsequent lines are raw client->server JSON-RPC dispatched
        through a per-client :class:`~agent_mcp.session.BridgeSession`, and the
        session writes responses / decorator pushes / upstream notifications back
        over the same socket. One resident ``serve`` process can host many such
        sessions in a single interpreter (#744), each with its own upstream and
        decorator pipeline, so a stateless-per-call reuse across clients (which
        MCP's per-session initialize + notification stream forbids) is never
        attempted.
        """
        # Same token gate as the op path (a no-op on AF_UNIX where _token is None).
        if self._token is not None and req.get("token") != self._token:
            await self._send(writer, {"ok": False, "error": "unauthorized"})
            return
        bridge = req.get("bridge")
        if not bridge:
            await self._send(writer, {"ok": False, "error": "missing 'bridge'"})
            return
        try:
            cfg = load_config(bridge)
        except Exception as exc:
            await self._send(writer, {"ok": False, "error": f"config: {exc}"})
            return

        # Client-bound sink: write one complete JSON-RPC line per message and
        # await the drain so an idle client that stops reading applies real
        # backpressure (the transport buffer can't grow without bound). A
        # per-connection lock serializes concurrent dispatch/notification writes
        # so their lines and drains never interleave.
        write_lock = asyncio.Lock()

        async def sink(msg: dict) -> None:
            data = (json.dumps(msg) + "\n").encode()
            async with write_lock:
                writer.write(data)
                try:
                    await writer.drain()
                except (ConnectionResetError, BrokenPipeError):
                    pass  # a closed/broken client just drops the push

        session = BridgeSession(cfg, sink)
        try:
            await session.start()
        except Exception as exc:
            await self._send(writer, {"ok": False, "error": f"session start: {exc}"})
            return
        # Ack; from here the stream is raw MCP JSON-RPC in both directions.
        await self._send(writer, {"ok": True, "attached": True})
        log.info("session attached: %s (%d decorators)", bridge,
                 session.decorator_count)
        try:
            while not reader.at_eof():
                line = await reader.readline()
                if not line:
                    break
                text = line.strip()
                if not text:
                    continue
                try:
                    msg = json.loads(text)
                except (ValueError, TypeError):
                    log.warning("invalid JSON on attached session %s: %s",
                                bridge, text[:200])
                    continue
                session.submit(msg)
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            await session.aclose()
            log.info("session detached: %s", bridge)

    async def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if _HAS_AF_UNIX:
            # Clear a stale socket from a previous run (safe: a live server holds it).
            if self.socket_path.exists():
                self.socket_path.unlink()
            self._server = await asyncio.start_unix_server(
                self._handle, path=str(self.socket_path))
            log.info("serving on unix:%s", self.socket_path)
        else:
            # No AF_UNIX (Windows): bind loopback TCP and advertise the port +
            # a fresh auth token in the endpoint sidecar for port-discovery.
            self._token = secrets.token_hex(16)
            self._server = await asyncio.start_server(
                self._handle, host=_TCP_HOST, port=0)
            port = self._server.sockets[0].getsockname()[1]
            self._write_endpoint(port, self._token)
            log.info("serving on tcp:%s:%d (handle %s)", _TCP_HOST, port,
                     self.socket_path)
        sweeper = asyncio.create_task(self._sweep_loop())
        try:
            await self._stop.wait()
        finally:
            sweeper.cancel()
            self._server.close()
            await self._server.wait_closed()
            await self.pool.close_all()
            self._cleanup_endpoint()

    def _write_endpoint(self, port: int, token: str) -> None:
        """Publish the loopback port + token to the endpoint sidecar (owner-only)."""
        ep = _endpoint_path(self.socket_path)
        ep.write_text(json.dumps({"port": port, "token": token}), encoding="utf-8")
        # Best-effort: restrict the token file to the owner so another local user
        # can't read it (parity with the unix socket's default permissions).
        with contextlib.suppress(OSError):
            os.chmod(ep, 0o600)

    def _cleanup_endpoint(self) -> None:
        """Remove the transport's on-disk handle on clean shutdown."""
        target = self.socket_path if _HAS_AF_UNIX else _endpoint_path(self.socket_path)
        try:
            if target.exists():
                target.unlink()
        except OSError:
            pass

    def stop(self) -> None:
        self._stop.set()

    async def _sweep_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(_SWEEP_INTERVAL)
                await self.pool.sweep_idle()
        except asyncio.CancelledError:
            pass
