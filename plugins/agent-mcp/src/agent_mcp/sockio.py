"""Synchronous serve-socket client -- the asyncio-free half for the forwarder.

The thin per-session ``forward`` child (the #744 multiplexer client) must be as
small as possible: it is spawned once per MCP session, so every megabyte it
imports is paid N times over on a busy host. ``asyncio`` alone costs ~7 MiB of
RSS, so the forwarder does **not** use it. This module provides everything the
forwarder needs -- serve-host discovery, the attach handshake, and the
bidirectional byte pump -- using only ``socket`` + threads from the standard
library.

Transport is detected by **artifact**, not a platform probe: a resident host
publishes either an AF_UNIX socket file (POSIX) or a ``<socket>.endpoint``
sidecar carrying a loopback port + token (Windows). The client dials whichever
is present, so it always matches what the host actually bound without importing
``asyncio`` to ask. The async client + the server live in :mod:`agent_mcp.ipc`
and :mod:`agent_mcp.serve`; this module is import-compatible with them on the
wire (the same ``attach`` op + line-delimited JSON-RPC).
"""

from __future__ import annotations

import json
import os
import socket
import stat
import sys
import threading
from pathlib import Path

_TCP_HOST = "127.0.0.1"


def default_socket_path() -> Path:
    """The default serve socket handle: ``$AGENT_MCP_HOME/serve.sock``."""
    home = Path(os.environ.get("AGENT_MCP_HOME", Path.home() / ".agent-mcp"))
    return home / "serve.sock"


def _endpoint_path(socket_path: str | Path) -> Path:
    """The TCP endpoint sidecar for a serve handle (loopback port-discovery)."""
    return Path(str(socket_path) + ".endpoint")


def _read_endpoint(socket_path: str | Path) -> dict | None:
    """Parse the ``<socket>.endpoint`` sidecar, or ``None`` if absent/invalid.

    A malformed or out-of-range ``port`` is treated as invalid (``None``) rather
    than passed through -- otherwise a stale/garbage sidecar would reach
    ``socket.create_connection`` and raise ``OverflowError`` (not ``OSError``),
    escaping the caller's fall-back path.
    """
    try:
        data = json.loads(_endpoint_path(socket_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    port = data.get("port")
    if isinstance(port, int) and 0 < port <= 65535:
        return data
    return None


def _is_socket_file(path: Path) -> bool:
    try:
        return path.exists() and stat.S_ISSOCK(path.stat().st_mode)
    except OSError:
        return False


def serve_socket_if_available(explicit: str | None = None) -> Path | None:
    """Return the serve handle if a live host is advertised, else ``None``.

    Honors ``AGENT_MCP_NO_SERVE`` (force the cold path) and
    ``AGENT_MCP_SERVE_SOCKET`` (override the path). Transport-agnostic: a handle
    counts if *either* a parseable ``.endpoint`` sidecar (loopback-TCP host) or an
    actual AF_UNIX socket file is present. A stale handle (crashed host) still
    yields a path, but the subsequent connect fails and the caller falls back --
    the same self-healing the socket file always had.
    """
    if os.environ.get("AGENT_MCP_NO_SERVE"):
        return None
    raw = explicit or os.environ.get("AGENT_MCP_SERVE_SOCKET")
    path = Path(raw) if raw else default_socket_path()
    if _read_endpoint(path) is not None:
        return path
    return path if _is_socket_file(path) else None


def _connect(path: str | Path) -> tuple[socket.socket, str | None]:
    """Open a blocking client connection; return ``(sock, token)``.

    Dials loopback TCP when an endpoint sidecar is present (token from it), else
    an AF_UNIX socket. Raises ``OSError`` when the host can't be reached.
    """
    ep = _read_endpoint(path)
    if ep is not None:
        sock = socket.create_connection((_TCP_HOST, ep["port"]))
        return sock, ep.get("token")
    if not hasattr(socket, "AF_UNIX"):
        raise OSError(f"no serve endpoint for {path}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(path))
    return sock, None


def _read_ack_line(sock: socket.socket) -> bytes:
    """Read one newline-terminated line directly off the socket.

    Reads a byte at a time so it consumes **exactly** up to (and including) the
    first newline and no further -- a buffered reader (``socket.makefile``) could
    read ahead past the ack and swallow an early host notification that
    :func:`pump` must still deliver. Only used once, for the small attach ack.
    """
    buf = bytearray()
    while True:
        b = sock.recv(1)
        if not b:
            break
        buf += b
        if b == b"\n":
            break
    return bytes(buf)


def attach(path: str | Path, bridge: str | Path) -> socket.socket:
    """Attach a full MCP session on the host; return the raw connected socket.

    Sends the ``attach`` op and verifies the ack; thereafter the caller speaks
    line-delimited MCP JSON-RPC directly over the socket while the host runs that
    session's upstream + decorator pipeline. Raises ``OSError`` if the host can't
    be reached or refuses the attach (so the forwarder falls back to a direct
    in-process bridge).
    """
    sock, token = _connect(path)
    req: dict = {"op": "attach", "bridge": str(bridge)}
    if token is not None:
        req["token"] = token
    try:
        sock.sendall((json.dumps(req) + "\n").encode())
        line = _read_ack_line(sock)
    except OSError:
        _close(sock)
        raise
    if not line:
        _close(sock)
        raise OSError("serve closed before attach ack")
    try:
        ack = json.loads(line)
    except (ValueError, TypeError) as exc:
        _close(sock)
        raise OSError(f"bad attach ack: {exc}") from exc
    if not (isinstance(ack, dict) and ack.get("ok") and ack.get("attached")):
        _close(sock)
        reason = ack.get("error") if isinstance(ack, dict) else ack
        raise OSError(f"attach refused: {reason}")
    return sock


def _close(sock: socket.socket) -> None:
    try:
        sock.close()
    except OSError:
        pass


def pump(sock: socket.socket) -> None:
    """Bidirectionally pump line-delimited JSON-RPC between stdio and ``sock``.

    stdin -> socket and socket -> stdout run on two daemon threads (blocking I/O,
    no event loop). The **host->client** direction is authoritative for session
    end: on stdin EOF the client half-closes the socket (so the host drains and
    closes), and the pump keeps forwarding the host's final replies until the
    host closes the connection. A parent-death watchdog covers the case where
    stdin EOF never arrives (the Windows ``cmd``-shim grandchild).
    """
    # watchdog is stdlib-only and light; import here so a direct-fallback path
    # (which never calls pump) doesn't pay for it.
    from .watchdog import install_parent_death_watchdog, reap_descendants_on_exit

    done = threading.Event()  # set when the host->client direction ends

    def _client_to_host() -> None:
        try:
            for line in sys.stdin.buffer:
                sock.sendall(line if line.endswith(b"\n") else line + b"\n")
        except (OSError, ValueError):
            pass
        finally:
            # Half-close: tell the host we're done sending; it drains + closes,
            # which ends the host->client reader below. Do NOT signal done here,
            # so the reader can still deliver the host's final responses.
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass

    def _host_to_client() -> None:
        out = sys.stdout.buffer
        try:
            while True:
                data = sock.recv(65536)
                if not data:
                    break
                out.write(data)
                out.flush()
        except OSError:
            pass
        finally:
            done.set()

    def _stop() -> None:
        done.set()
        _close(sock)  # unblock a reader parked in recv()

    reap_descendants_on_exit()
    install_parent_death_watchdog(_stop)

    threading.Thread(target=_client_to_host, name="agent-mcp-fwd-in",
                     daemon=True).start()
    threading.Thread(target=_host_to_client, name="agent-mcp-fwd-out",
                     daemon=True).start()
    done.wait()
    _close(sock)
