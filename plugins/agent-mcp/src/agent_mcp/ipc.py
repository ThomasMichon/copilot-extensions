"""Serve IPC transport -- the **async** client for ``call`` + the server side.

The serve-host discovery + sidecar helpers (``default_socket_path``,
``serve_socket_if_available``, endpoint parsing) live in the stdlib-only
:mod:`agent_mcp.sockio` and are re-exported here. This module adds the
**asyncio** client used by the one-shot ``call`` fast-path and shared with the
:mod:`agent_mcp.serve` host: the ``request_via_socket`` / ``call_via_socket`` op
helpers and the ``open_attached_session`` attach.

The thin per-session **forwarder** (:mod:`agent_mcp.forward`) deliberately does
**not** import this module -- it uses :mod:`agent_mcp.sockio`'s synchronous
socket client instead, so it never pulls in ``asyncio`` (~7 MiB of RSS paid once
per MCP session). Both ends speak the same wire protocol -- the ``attach`` op and
line-delimited JSON-RPC -- so an async and a sync client interoperate with one
host.

The async client selects its transport with the :data:`_HAS_AF_UNIX` probe (an
AF_UNIX socket on POSIX, a loopback-TCP listener + token sidecar where asyncio
has no AF_UNIX); the sync forwarder detects the transport by artifact instead.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path

from .request_context import BRIDGE_OPS, capture_context, supports_context
from .sockio import (
    _TCP_HOST,
    _endpoint_path,
    _read_endpoint,
    default_home_dir,
    default_socket_path,
    serve_socket_if_available,
)

# Whether this runtime's asyncio exposes AF_UNIX sockets (POSIX yes, Windows no).
# Authoritative for the async server's *bind* choice (serve.serve_forever) and
# the async client's dial; the forwarder (agent_mcp.sockio) instead detects the
# transport by artifact so it needs no asyncio. The discovery + sidecar helpers
# now live in agent_mcp.sockio (stdlib-only) and are re-exported here so callers
# and tests still ``from agent_mcp.ipc import ...`` / ``from agent_mcp.serve``.
_HAS_AF_UNIX = hasattr(asyncio, "start_unix_server")

__all__ = [
    "_HAS_AF_UNIX",
    "_TCP_HOST",
    "_connect",
    "_endpoint_path",
    "_read_endpoint",
    "aclose_writer",
    "call_via_socket",
    "default_home_dir",
    "default_socket_path",
    "list_tools_via_socket",
    "open_attached_session",
    "request_via_socket",
    "serve_socket_if_available",
]


async def _join_cleanup(task: asyncio.Task) -> None:
    """Keep stream cleanup owned until terminal, then propagate cancellation."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            interrupted = True
    await task
    if interrupted:
        raise asyncio.CancelledError


async def aclose_writer(writer: asyncio.StreamWriter) -> None:
    """Close a stream writer and await its teardown deterministically.

    Awaiting ``wait_closed()`` after ``close()`` avoids "unclosed transport"
    warnings when these helpers run under a short-lived ``asyncio.run()`` loop
    that would otherwise be torn down before the transport finishes closing. The
    wait is best-effort -- a broken/already-closed transport just returns.
    """
    writer.close()
    with contextlib.suppress(Exception):
        await writer.wait_closed()


async def _connect(socket_path: str | Path, *, limit: int | None = None):
    """Open a client connection to the serve daemon (platform transport).

    Returns ``(reader, writer, token)``: the token is ``None`` on AF_UNIX and the
    daemon's shared secret on the loopback-TCP transport. Raises ``OSError`` when
    the daemon can't be reached so callers fall back to the cold path.
    """
    options = {} if limit is None else {"limit": limit}
    if _HAS_AF_UNIX:
        reader, writer = await asyncio.open_unix_connection(str(socket_path), **options)
        return reader, writer, None
    ep = _read_endpoint(socket_path)
    if ep is None:
        raise OSError(f"no serve endpoint for {socket_path}")
    # #4366: a transient ConnectionRefusedError against a live, already-
    # advertised endpoint has been observed under heavy Windows host load
    # (the daemon's asyncio.start_server() completes -- and its endpoint
    # sidecar is written -- only once the listener is bound and accepting,
    # so this is not an endpoint-before-listener ordering race; the cause is
    # unconfirmed, consistent with transient host/OS-level contention).
    # Retry a ConnectionRefusedError a few times with a short backoff before
    # giving up. Any other OSError (e.g. the daemon has genuinely exited)
    # still surfaces immediately, unretried.
    attempts, delay = 5, 0.05
    for attempt in range(attempts):
        try:
            reader, writer = await asyncio.open_connection(_TCP_HOST, ep["port"], **options)
            return reader, writer, ep.get("token")
        except ConnectionRefusedError:
            if attempt == attempts - 1:
                raise
            await asyncio.sleep(delay)
            delay *= 2


async def request_via_socket(socket_path: str | Path, request: dict) -> dict | None:
    """Send one op ``request`` to the serve daemon; return the parsed response.

    Handles the platform transport (AF_UNIX or loopback TCP) and injects the
    auth token on the TCP transport. Returns ``None`` if the daemon closes
    without replying. Raises ``OSError`` if the daemon can't be reached.
    """
    from . import execution_context, incoming

    req = dict(request)
    application = req.get("op") in {"call", "list"}
    if application:
        admission = await asyncio.to_thread(incoming.ensure_current, socket_path)
        req.update(await asyncio.to_thread(
            execution_context.request_fields, socket_path, req.get("bridge"),
        ))
        if admission is not None:
            req["generation"] = admission.generation
            req["runtime_root"] = admission.root
    reader, writer, token = await _connect(socket_path)
    sent = False
    try:
        req = await _qualified_request(reader, writer, token, req)
        if token is not None:
            req["token"] = token
        sent = True
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        if not line:
            if req.get("op") == "call":
                raise incoming.OutcomeUnknown("warm call outcome unknown; no cold replay")
            return None
        reply = json.loads(line)
        return incoming.validate_call_reply(reply) if req.get("op") == "call" else reply
    except (OSError, ValueError) as exc:
        if sent and req.get("op") == "call":
            raise incoming.OutcomeUnknown("warm call outcome unknown; no cold replay") from exc
        raise
    finally:
        await aclose_writer(writer)


async def _qualified_request(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
    token: str | None, request: dict,
) -> dict:
    req = dict(request)
    if req.get("op") not in BRIDGE_OPS:
        return req
    hello: dict = {"op": "ping"}
    if token is not None:
        hello["token"] = token
    writer.write((json.dumps(hello) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    try:
        capable = bool(line) and supports_context(json.loads(line))
    except (ValueError, TypeError):
        capable = False
    if not capable:
        raise OSError("resident does not support caller configuration context; use the direct bridge")
    req.setdefault("configuration_context", capture_context(str(req.get("bridge", ""))))
    return req


async def call_via_socket(socket_path: str | Path, bridge: str, tool: str,
                          arguments: dict) -> dict:
    """Send one ``call`` over the serve socket and return the parsed response.

    Raises ``OSError`` if the socket can't be reached (caller falls back to the
    cold one-shot path).
    """
    resp = await request_via_socket(
        socket_path,
        {"op": "call", "bridge": bridge, "tool": tool, "arguments": arguments},
    )
    from .incoming import validate_call_reply

    validate_call_reply(resp)
    if resp["ok"] is False and resp.get("shared_execution") is False:
        from .execution_context import SharingUnavailable

        raise SharingUnavailable("shared execution refused before effects")
    return resp


async def list_tools_via_socket(socket_path: str | Path, bridge: str) -> dict:
    """Send one ``list`` over the serve socket and return the parsed response.

    Returns the bridge's tool catalog exactly as a live session would see it
    (the bridge's ``tools:`` allow/deny filter applies) -- the same result
    ``materialize``'s cold ``OneShotSession.list_tools()`` path already
    returns, so consulting a warm daemon changes nothing about *what*
    materialize projects, only *how fast* and at what contention cost it
    gets there. Raises ``OSError`` if the socket can't be reached (caller
    falls back to the cold one-shot path).
    """
    resp = await request_via_socket(
        socket_path,
        {"op": "list", "bridge": bridge},
    )
    if resp is None:
        raise OSError("serve socket closed without a response")
    return resp


async def open_attached_session(socket_path: str | Path, bridge: str | Path):
    """Attach a full MCP session on the serve daemon; return ``(reader, writer)``.

    Sends the ``attach`` op and verifies the ack; thereafter the caller speaks
    line-delimited MCP JSON-RPC directly over the returned stream while the
    daemon runs that session's upstream + decorator pipeline (the #744
    multiplexer). Raises ``OSError`` if the daemon can't be reached or refuses
    the attach, so a consumer falls back to a direct, in-process bridge.
    """
    from . import execution_context, incoming

    admission = await asyncio.to_thread(incoming.ensure_current, socket_path)
    fields = await asyncio.to_thread(execution_context.request_fields, socket_path, bridge)
    reader, writer, token = await _connect(socket_path)
    req: dict = {"op": "attach", "bridge": str(bridge)}
    req.update(fields)
    if admission is not None:
        req.update(generation=admission.generation, runtime_root=admission.root)
    try:
        req = await _qualified_request(reader, writer, token, req)
    except BaseException:
        await aclose_writer(writer)
        raise
    if token is not None:
        req["token"] = token
    writer.write((json.dumps(req) + "\n").encode())
    await writer.drain()
    line = await reader.readline()
    if not line:
        await aclose_writer(writer)
        raise OSError("serve closed before attach ack")
    try:
        ack = json.loads(line)
    except (ValueError, TypeError) as exc:
        await aclose_writer(writer)
        raise OSError(f"bad attach ack: {exc}") from exc
    if not (isinstance(ack, dict) and ack.get("ok") and ack.get("attached")):
        await aclose_writer(writer)
        reason = ack.get("error") if isinstance(ack, dict) else ack
        raise OSError(f"attach refused: {reason}")
    return reader, writer
