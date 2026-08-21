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

from .sockio import (
    _TCP_HOST,
    _endpoint_path,
    _read_endpoint,
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
    "default_socket_path",
    "open_attached_session",
    "request_via_socket",
    "serve_socket_if_available",
]


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


async def _connect(socket_path: str | Path):
    """Open a client connection to the serve daemon (platform transport).

    Returns ``(reader, writer, token)``: the token is ``None`` on AF_UNIX and the
    daemon's shared secret on the loopback-TCP transport. Raises ``OSError`` when
    the daemon can't be reached so callers fall back to the cold path.
    """
    if _HAS_AF_UNIX:
        reader, writer = await asyncio.open_unix_connection(str(socket_path))
        return reader, writer, None
    ep = _read_endpoint(socket_path)
    if ep is None:
        raise OSError(f"no serve endpoint for {socket_path}")
    reader, writer = await asyncio.open_connection(_TCP_HOST, ep["port"])
    return reader, writer, ep.get("token")


async def request_via_socket(socket_path: str | Path, request: dict) -> dict | None:
    """Send one op ``request`` to the serve daemon; return the parsed response.

    Handles the platform transport (AF_UNIX or loopback TCP) and injects the
    auth token on the TCP transport. Returns ``None`` if the daemon closes
    without replying. Raises ``OSError`` if the daemon can't be reached.
    """
    reader, writer, token = await _connect(socket_path)
    try:
        req = dict(request)
        if token is not None:
            req["token"] = token
        writer.write((json.dumps(req) + "\n").encode())
        await writer.drain()
        line = await reader.readline()
        if not line:
            return None
        return json.loads(line)
    finally:
        await aclose_writer(writer)


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
    reader, writer, token = await _connect(socket_path)
    req: dict = {"op": "attach", "bridge": str(bridge)}
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
