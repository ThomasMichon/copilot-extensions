"""Resident warmth tier: a local daemon holding warm upstream MCP sessions.

``agent-mcp call`` and the materialized stubs pay a per-call upstream cold-start
(spawn ``npx``/``bunx``/``node`` + the MCP ``initialize`` handshake) on *every*
invocation. ``agent-mcp serve`` keeps one warm :class:`OneShotSession` per bridge
and answers ``call``/``list`` requests over a unix-domain socket, so repeated
calls skip the cold-start entirely.

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
import stat
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

log = logging.getLogger("agent-mcp.serve")

_SWEEP_INTERVAL = 30.0  # seconds between idle sweeps
_DEFAULT_IDLE_TIMEOUT = 300.0  # evict a warm session unused this long


def default_socket_path() -> Path:
    """The default serve socket: ``$AGENT_MCP_HOME/serve.sock``."""
    home = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp"))
    return home / "serve.sock"


def serve_socket_if_available(explicit: str | None = None) -> Path | None:
    """Return the serve socket path if a live socket file exists, else ``None``.

    Honors ``AGENT_MCP_NO_SERVE`` (force the cold path) and
    ``AGENT_MCP_SERVE_SOCKET`` (override the path). Only an actual socket file
    counts -- a stale regular file or missing path yields ``None`` so the caller
    falls back to the one-shot cold path.
    """
    if os.environ.get("AGENT_MCP_NO_SERVE"):
        return None
    raw = explicit or os.environ.get("AGENT_MCP_SERVE_SOCKET")
    path = Path(raw) if raw else default_socket_path()
    try:
        if path.exists() and stat.S_ISSOCK(path.stat().st_mode):
            return path
    except OSError:
        return None
    return None


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
    """A unix-socket server fronting a :class:`WarmPool`."""

    def __init__(self, socket_path: str | Path, *, pool: WarmPool | None = None,
                 idle_timeout: float = _DEFAULT_IDLE_TIMEOUT) -> None:
        self.socket_path = Path(socket_path)
        self.pool = pool or WarmPool(idle_timeout=idle_timeout)
        self._stop = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None

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

    async def serve_forever(self) -> None:
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Clear a stale socket from a previous run (safe: a live server holds it).
        if self.socket_path.exists():
            self.socket_path.unlink()
        self._server = await asyncio.start_unix_server(
            self._handle, path=str(self.socket_path))
        log.info("serving on %s", self.socket_path)
        sweeper = asyncio.create_task(self._sweep_loop())
        try:
            await self._stop.wait()
        finally:
            sweeper.cancel()
            self._server.close()
            await self._server.wait_closed()
            await self.pool.close_all()
            if self.socket_path.exists():
                try:
                    self.socket_path.unlink()
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


async def call_via_socket(socket_path: str | Path, bridge: str, tool: str,
                          arguments: dict) -> dict:
    """Send one ``call`` over the serve socket and return the parsed response.

    Raises ``OSError`` if the socket can't be reached (caller falls back to the
    cold one-shot path).
    """
    reader, writer = await asyncio.open_unix_connection(str(socket_path))
    try:
        req = {"op": "call", "bridge": bridge, "tool": tool, "arguments": arguments}
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        if not line:
            raise OSError("serve socket closed without a response")
        return json.loads(line)
    finally:
        writer.close()
