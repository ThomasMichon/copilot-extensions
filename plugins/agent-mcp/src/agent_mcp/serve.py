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
    filter_tools,
    result_is_error,
    result_structured,
    result_text,
    tool_visible,
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


def _control_token_path(config_dir: str | Path, pid: int) -> Path:
    """Owner-only, **pid-keyed** sidecar carrying one generation's control-
    channel auth token -- lets a cutover orchestrator authenticate to a
    daemon it did not itself spawn (the currently-active one, being cut over
    away from).

    Keyed by pid (not a single shared filename) so that a *second* cutover
    attempt in the same home can never read a stale/wrong token left behind
    by an unrelated generation (e.g. a passive daemon from a prior, rolled-
    back cutover attempt that also started a control listener and would
    otherwise clobber a single shared file). The cutover script always reads
    this keyed on the *specific* pid it just resolved from the routing
    table, so there is nothing to disambiguate.
    """
    return Path(config_dir) / f"serve-control-{pid}.token"

try:
    from single_instance_lease import AlreadyRunningError, SingleInstance
except ImportError:  # pragma: no cover - the vendored lib is always installed
    AlreadyRunningError = None  # type: ignore[assignment,misc]
    SingleInstance = None  # type: ignore[assignment,misc]

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
    "_control_token_path",
    "_endpoint_path",
    "_read_endpoint",
    "call_via_socket",
    "default_socket_path",
    "flip_data_handle",
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
        self._key_locks: dict[str, asyncio.Lock] = {}
        self._idle_timeout = idle_timeout
        self._guard = asyncio.Lock()  # guards the open/evict of the entry map

    async def _key_lock(self, key: str) -> asyncio.Lock:
        async with self._guard:
            return self._key_locks.setdefault(key, asyncio.Lock())

    async def _entry_for_locked(self, key: str, cfg: BridgeConfig) -> _WarmEntry:
        """Select/open a config-matched entry while the key lock is held."""
        async with self._guard:
            entry = self._entries.get(key)
            if entry is not None and entry.session.cfg != cfg:
                self._entries.pop(key, None)
        if entry is not None and entry.session.cfg != cfg:
            await entry.session.__aexit__(None, None, None)
            log.info("warm session replaced after config change: %s", key)
            entry = None
        if entry is None:
            session = OneShotSession(cfg)
            await session.__aenter__()
            entry = _WarmEntry(session)
            async with self._guard:
                self._entries[key] = entry
            log.info("warm session opened: %s", key)
        return entry

    async def call(self, key: str, cfg: BridgeConfig, tool: str, arguments: dict) -> dict:
        if not tool_visible(tool, cfg.tools):
            raise UpstreamError(
                f"tools/call '{tool}': blocked by bridge tools filter",
                code=-32601,
            )
        key_lock = await self._key_lock(key)
        async with key_lock:
            entry = await self._entry_for_locked(key, cfg)
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
                await self._evict_locked(key, entry)
                raise

    async def list(self, key: str, cfg: BridgeConfig) -> list[dict]:
        key_lock = await self._key_lock(key)
        async with key_lock:
            entry = await self._entry_for_locked(key, cfg)
            entry.last_used = time.monotonic()
            try:
                return filter_tools(await entry.session.list_tools(), cfg.tools)
            except UpstreamError:
                raise
            except Exception:
                await self._evict_locked(key, entry)
                raise

    async def _evict_locked(self, key: str, expected: _WarmEntry | None = None) -> None:
        """Evict the current entry while the per-key lock is held."""
        async with self._guard:
            entry = self._entries.get(key)
            if expected is not None and entry is not expected:
                return
            entry = self._entries.pop(key, None)
        if entry is not None:
            with contextlib.suppress(Exception):
                await entry.session.__aexit__(None, None, None)
            log.info("warm session closed: %s", key)

    async def _evict(self, key: str) -> None:
        key_lock = await self._key_lock(key)
        async with key_lock:
            await self._evict_locked(key)

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
    """A local-IPC server fronting a :class:`WarmPool` (AF_UNIX / loopback TCP).

    The host is a **losable, refcounted, idle-exiting** accelerator (#744):

    * A **single-instance lease** (keyed on the socket handle's directory, i.e.
      ``AGENT_MCP_HOME``) guarantees at most one live host per home. A second host
      that can't take the lease stands down before binding the socket -- so the
      thin forwarders can race-spawn the host on demand without ever creating a
      duplicate.
    * Attached multiplexer sessions are **refcounted**; when the last one detaches
      and no ``call``/``list`` activity or warm session remains for
      ``idle_timeout`` seconds, the host **evicts itself**, freeing its RAM until
      the next forwarder spawns a fresh one.
    """

    def __init__(self, socket_path: str | Path, *, pool: WarmPool | None = None,
                 idle_timeout: float = _DEFAULT_IDLE_TIMEOUT,
                 enable_lease: bool = True,
                 lease_service: str = "agent-mcp-serve",
                 passive: bool = False,
                 control_port: int | None = None,
                 control_token: str | None = None,
                 version: str | None = None) -> None:
        self.socket_path = Path(socket_path)
        self.pool = pool or WarmPool(idle_timeout=idle_timeout)
        self._idle_timeout = idle_timeout
        self._stop = asyncio.Event()
        self._server: asyncio.AbstractServer | None = None
        # Per-daemon auth token, minted only for the loopback-TCP transport
        # (``None`` on AF_UNIX, where filesystem permissions gate access).
        self._token: str | None = None
        # Attached-session refcount + last-activity clock drive the host's own
        # idle self-eviction (distinct from the WarmPool's per-session idle).
        self._attached = 0
        self._last_active = time.monotonic()
        # Single-instance lease: one host per AGENT_MCP_HOME. Disabled only in
        # tests that intentionally run two hosts on one home, and permanently
        # for a ``--passive`` cutover instance (see ``passive`` below).
        self._enable_lease = enable_lease and SingleInstance is not None
        self._lease_service = lease_service
        self._lease = None
        # Zero-downtime cutover state (docs/patterns/graceful-daemon-cutover.md,
        # libs/zdd). ``passive`` marks a daemon spawned *beside* an already-active
        # one during a cutover: it must bind its own distinct socket_path (the
        # caller's job, e.g. ``serve-g<N>.sock``) and never contend for the
        # home-wide single-instance lease -- the routing flip that promotes it,
        # not a lease, is what makes it the one real clients reach. ``draining``
        # is armed by the ``drain`` control op (refuse new ``attach`` sessions;
        # already-attached ones and in-flight ``call``/``list`` requests are
        # unaffected) and cleared by ``undrain`` (cutover rollback).
        self._passive = passive
        self._draining = False
        self._control_port_request = control_port
        self._control_server: asyncio.AbstractServer | None = None
        # A caller-supplied token (the cutover CLI injects one via
        # AGENT_MCP_CONTROL_TOKEN so it knows the token of a daemon it spawns
        # *before* that daemon publishes anything) always wins; otherwise mint
        # one so a plain, non-cutover ``serve`` still has a control channel a
        # future cutover attempt can authenticate to via the token sidecar
        # (see ``_start_control_listener``).
        self._control_token = control_token or secrets.token_hex(16)
        self._version = version

    @property
    def attached(self) -> int:
        """Number of live attached multiplexer sessions (test/diagnostic hook)."""
        return self._attached

    @property
    def draining(self) -> bool:
        """Whether this generation is refusing new attach sessions (test hook)."""
        return self._draining

    def _touch(self) -> None:
        """Mark host activity so the idle self-eviction clock resets."""
        self._last_active = time.monotonic()

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
                    if self._draining:
                        # Cutover in progress: refuse new long-lived sessions so
                        # this generation trends to idle (the safe cutover point,
                        # see docs/patterns/graceful-daemon-cutover.md). The
                        # client's existing fallback (a live host that refuses an
                        # attach -> direct in-process bridge, no respawn) already
                        # covers this refusal correctly; already-attached sessions
                        # are untouched and keep running to their natural close.
                        await self._send(writer, {
                            "ok": False, "attached": False,
                            "error": "serve: draining for cutover, "
                                     "reconnect to pick up the new generation",
                        })
                        return
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
        self._touch()  # any op is host activity; reset the idle self-evict clock
        if op == "ping":
            return {"ok": True, "pong": True, "sessions": self.pool.size,
                    "attached": self._attached}
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
        self._attached += 1
        self._touch()
        log.info("session attached: %s (%d decorators; %d live)", bridge,
                 session.decorator_count, self._attached)
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
            # The refcount MUST drop on detach even if teardown fails, or the host
            # would believe a session is still attached and never idle-evict. Drop
            # it first, then tear the session down fail-soft.
            self._attached -= 1
            self._touch()  # last detach starts the host's idle-eviction clock
            try:
                await session.aclose()
            except Exception as exc:  # teardown must not wedge the handler/refcount
                log.warning("session aclose error for %s: %s", bridge, exc)
            log.info("session detached: %s (%d live)", bridge, self._attached)

    def _acquire_lease(self) -> bool:
        """Take the single-instance lease; return ``False`` to stand down.

        Keyed on the socket handle's directory (``AGENT_MCP_HOME``) so at most one
        host runs per home. A second host that loses the race returns ``False``
        and must exit **before** binding the socket, so it never disturbs the live
        host's handle.

        A ``--passive`` cutover instance skips this entirely: it binds its own
        distinct ``socket_path`` (the caller's job -- e.g. a generation-suffixed
        path), so it never contends with the active daemon's lease at all. What
        makes it the daemon real clients reach is the routing flip the cutover
        orchestrator performs after health-gating it, not lease ownership.
        """
        if self._passive or not self._enable_lease:
            return True
        self._lease = SingleInstance(
            self.socket_path.parent, service=self._lease_service, logger=log)
        try:
            self._lease.acquire()
        except AlreadyRunningError as exc:
            log.info("serve: another host already holds the lease (%s); "
                     "standing down", exc.holder_pid)
            self._lease = None
            return False
        return True

    def _release_lease(self) -> None:
        if self._lease is not None:
            with contextlib.suppress(Exception):
                self._lease.release()
            self._lease = None

    async def serve_forever(self) -> None:
        # Take the single-instance lease before binding so a losing host never
        # touches the winner's socket handle. Standing down is a clean no-op exit.
        if not self._acquire_lease():
            return
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if _HAS_AF_UNIX:
                # Clear a stale socket from a previous run (safe: we hold the lease).
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
            await self._start_control_listener()
            sweeper = asyncio.create_task(self._sweep_loop())
            try:
                await self._stop.wait()
            finally:
                sweeper.cancel()
                self._server.close()
                await self._server.wait_closed()
                await self._stop_control_listener()
                await self.pool.close_all()
                self._cleanup_endpoint()
        finally:
            self._release_lease()

    async def _start_control_listener(self) -> None:
        """Bind the always-on lifecycle control listener (loopback TCP),
        publish it to the ``zdd`` routing table for cutover discovery, and
        write its auth token to an owner-only sidecar so an orchestrator that
        did *not* spawn this daemon (i.e. the currently-active generation
        being cut over away from) can still authenticate to it.

        Separate from the data-plane transport above (AF_UNIX on POSIX, or the
        same-purpose loopback TCP on Windows): the control channel exists purely
        so a `cutover` orchestrator can health/drain/undrain/shutdown *this*
        generation without needing to speak agent-mcp's full data-plane op set,
        and without requiring the data transport to be TCP. Every daemon --
        passive or promoted -- binds one and publishes it, so the very next
        cutover after this ships can always find and drive it. (The first
        cutover ever run against a pre-existing daemon that predates this
        feature has nothing to dial here -- see docs/patterns/
        graceful-daemon-cutover.md and the agent-mcp cutover effort for that
        one-time bootstrap boundary.)
        """
        from zdd import routing

        self._control_server = await asyncio.start_server(
            self._handle_control, host=_TCP_HOST,
            port=self._control_port_request or 0)
        control_port = self._control_server.sockets[0].getsockname()[1]
        token_path = _control_token_path(self._config_dir(), os.getpid())
        token_path.write_text(self._control_token, encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(token_path, 0o600)
        with contextlib.suppress(Exception):
            routing.publish_active(
                self._config_dir(), bind=_TCP_HOST, port=control_port,
                pid=os.getpid(), version=self._version, demote_existing=True,
            )
        log.info("serve: control channel on tcp:%s:%d", _TCP_HOST, control_port)

    async def _stop_control_listener(self) -> None:
        if self._control_server is None:
            return
        self._control_server.close()
        with contextlib.suppress(Exception):
            await self._control_server.wait_closed()
        from zdd import routing
        with contextlib.suppress(Exception):
            routing.clear_if_owner(self._config_dir(), os.getpid())
        with contextlib.suppress(OSError):
            _control_token_path(self._config_dir(), os.getpid()).unlink()


    def _config_dir(self) -> Path:
        """Where the ``zdd`` control routing table + cutover breadcrumb live.

        Deliberately the socket handle's own directory (``AGENT_MCP_HOME``),
        matching every other per-home artifact (the lease, the data endpoint
        sidecar) -- one home, one place to look.
        """
        return self.socket_path.parent

    async def _handle_control(self, reader: asyncio.StreamReader,
                              writer: asyncio.StreamWriter) -> None:
        """Serve the lifecycle control op set: ``ping``, ``drain``, ``undrain``,
        ``shutdown``. Deliberately does not carry ``call``/``list``/``attach`` --
        those stay data-plane-only; control exists purely for cutover signaling."""
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
                resp = self._control_dispatch(req)
                await self._send(writer, resp)
                if req.get("op") == "shutdown" and resp.get("ok"):
                    self._stop.set()
                    break
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            with contextlib.suppress(Exception):
                writer.close()

    def _control_dispatch(self, req: dict) -> dict:
        if req.get("token") != self._control_token:
            return {"ok": False, "error": "unauthorized"}
        op = req.get("op")
        if op in ("ping", "health"):
            return {"ok": True, "pong": True, "draining": self._draining,
                    "attached": self._attached, "sessions": self.pool.size}
        if op == "drain":
            self._draining = True
            # Only attached (long-lived multiplexer) sessions are "in-flight,
            # non-resumable work" in this daemon's sense -- a warm WarmPool
            # entry is just a cached one-shot connection with no undelivered
            # state, safe to drop on retire (pool.close_all() at shutdown).
            # ``busy_sessions`` is the exact key zdd.cutover's orchestrator
            # reads for both its busy-oracle poll and its failure message.
            return {"ok": True, "busy_sessions": self._attached,
                    "sessions": self.pool.size}
        if op == "undrain":
            self._draining = False
            return {"ok": True}
        if op == "shutdown":
            return {"ok": True}
        return {"ok": False, "error": f"unknown control op: {op!r}"}

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
        # Check at least twice per idle window (capped at the 30s default) so a
        # small idle_timeout evicts promptly and a large one stays cheap.
        interval = _SWEEP_INTERVAL
        if self._idle_timeout > 0:
            interval = max(0.05, min(_SWEEP_INTERVAL, self._idle_timeout / 2))
        try:
            while True:
                await asyncio.sleep(interval)
                await self.pool.sweep_idle()
                self._maybe_idle_evict()
        except asyncio.CancelledError:
            pass

    def _maybe_idle_evict(self) -> None:
        """Self-evict when nothing has used the host for ``idle_timeout``.

        The host is losable: once no multiplexer session is attached, no warm
        ``call`` session remains, and no op has arrived for the idle window, it
        stops itself and releases its RAM. A fresh forwarder simply re-spawns one.
        A non-positive ``idle_timeout`` disables self-eviction (run forever).
        """
        if self._idle_timeout <= 0:
            return
        if self._attached > 0 or self.pool.size > 0:
            return
        if time.monotonic() - self._last_active > self._idle_timeout:
            log.info("serve: idle for %.0fs with no attached sessions; "
                     "evicting host", self._idle_timeout)
            self._stop.set()


def flip_data_handle(new_data_socket_path: str | Path, *,
                     legacy_path: str | Path | None = None) -> None:
    """Atomically repoint the **fixed, client-facing** data handle at a newly
    promoted generation's real socket -- the actual "cutover" clients feel.

    Deliberately separate from the ``zdd`` control-plane routing table (which
    only the cutover orchestrator and control ops consult): every existing data
    client -- :func:`serve_socket_if_available`, the forwarder's ``attach``, one-
    shot ``call``/``materialize`` -- already re-resolves the *fixed* handle fresh
    on every invocation and needs **zero code changes** to follow a cutover,
    because that fixed handle is what this function repoints:

    * **POSIX** -- the fixed handle is a **symlink** to the real, generation-
      suffixed socket file. Repointing is ``os.symlink`` to a temp name +
      ``os.replace`` (atomic rename), so a reader never observes a missing or
      half-written link. Upgrades the very first cutover's fixed path in place
      even when it is still a plain (pre-cutover) socket file, not yet a link.
    * **Windows** -- the fixed handle has no real socket at all, only its
      ``<fixed>.endpoint`` sidecar (port + token). Repointing copies the new
      generation's own ``<new_data_socket_path>.endpoint`` content over the
      fixed sidecar, atomically (temp file + ``os.replace``).

    Called by the ``cutover`` CLI after ``CutoverOrchestrator`` commits -- never
    by the daemon itself (a promoted daemon does not know or care that it was
    promoted; it just keeps answering requests on the socket it already bound).
    """
    from .ipc import default_socket_path
    fixed = Path(legacy_path) if legacy_path is not None else default_socket_path()
    new_path = Path(new_data_socket_path)
    fixed.parent.mkdir(parents=True, exist_ok=True)
    if _HAS_AF_UNIX:
        tmp_link = fixed.with_name(fixed.name + f".tmp-{os.getpid()}")
        with contextlib.suppress(OSError):
            tmp_link.unlink()
        os.symlink(new_path, tmp_link)
        os.replace(tmp_link, fixed)
        return
    new_ep = _endpoint_path(new_path)
    fixed_ep = _endpoint_path(fixed)
    data = new_ep.read_text(encoding="utf-8")
    tmp_ep = fixed_ep.with_name(fixed_ep.name + f".tmp-{os.getpid()}")
    tmp_ep.write_text(data, encoding="utf-8")
    with contextlib.suppress(OSError):
        os.chmod(tmp_ep, 0o600)
    os.replace(tmp_ep, fixed_ep)
