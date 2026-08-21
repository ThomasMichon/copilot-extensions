"""Serve IPC transport + client attach -- the light half of ``serve`` (stdlib-only).

Split out of :mod:`agent_mcp.serve` so the thin per-session **forwarder** (the
#744 multiplexer client, :mod:`agent_mcp.forward`) can locate and attach to a
resident ``serve`` session-host **without importing the heavy bridge tree**
(config parsing, credential injectors, the decorator pipeline, transports, the
upstream client). Importing this module pulls in only the standard library, so
the per-session forwarder child stays small -- that reduced footprint, replacing
one full ``agent-mcp bridge`` interpreter per session with one thin forwarder, is
the whole point of the multiplexer.

The transport is chosen per platform by the single :data:`_HAS_AF_UNIX` probe,
exactly as before: an **AF_UNIX** socket on POSIX (gated by filesystem
permissions), or a **loopback TCP** listener plus a per-daemon auth token
advertised in an ``<socket>.endpoint`` sidecar on Windows (where asyncio has no
AF_UNIX). Both the server (:mod:`agent_mcp.serve`) and every client
(``call``/forwarder) import these primitives, so the two ends always agree.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
from pathlib import Path

# Whether this runtime's asyncio exposes AF_UNIX sockets (POSIX yes, Windows no).
# The single switch that selects the serve IPC transport on both server + client.
_HAS_AF_UNIX = hasattr(asyncio, "start_unix_server")

# Loopback host for the Windows/no-AF_UNIX TCP transport.
_TCP_HOST = "127.0.0.1"


def default_socket_path() -> Path:
    """The default serve socket handle: ``$AGENT_MCP_HOME/serve.sock``.

    On POSIX this is the AF_UNIX socket path itself; on Windows it is a logical
    handle whose ``.endpoint`` sidecar (see :func:`_endpoint_path`) carries the
    live loopback port + token.
    """
    home = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp"))
    return home / "serve.sock"


def _endpoint_path(socket_path: str | Path) -> Path:
    """The TCP endpoint sidecar for a serve handle (loopback port-discovery)."""
    return Path(str(socket_path) + ".endpoint")


def _read_endpoint(socket_path: str | Path) -> dict | None:
    """Parse the ``<socket>.endpoint`` sidecar, or ``None`` if absent/invalid."""
    try:
        data = json.loads(_endpoint_path(socket_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("port"), int):
        return data
    return None


def serve_socket_if_available(explicit: str | None = None) -> Path | None:
    """Return the serve handle if a live daemon is advertised, else ``None``.

    Honors ``AGENT_MCP_NO_SERVE`` (force the cold path) and
    ``AGENT_MCP_SERVE_SOCKET`` (override the path). On POSIX only an actual
    AF_UNIX socket file counts; on Windows the presence of a parseable
    ``.endpoint`` sidecar counts. A stale handle (crashed daemon) still yields a
    path, but the client's connect then fails and falls back to the cold path --
    the same self-healing the unix socket already had.
    """
    if os.environ.get("AGENT_MCP_NO_SERVE"):
        return None
    raw = explicit or os.environ.get("AGENT_MCP_SERVE_SOCKET")
    path = Path(raw) if raw else default_socket_path()
    if _HAS_AF_UNIX:
        try:
            if path.exists() and stat.S_ISSOCK(path.stat().st_mode):
                return path
        except OSError:
            return None
        return None
    return path if _read_endpoint(path) is not None else None


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
