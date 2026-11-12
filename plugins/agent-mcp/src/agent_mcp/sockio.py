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
import time
from pathlib import Path

from .home import default_home_dir as default_home_dir

_TCP_HOST = "127.0.0.1"


class HostUnreachableError(OSError):
    """A serve handle is advertised but no live host answers the connect.

    Distinct from a live host that *refuses* an attach: unreachable means the
    resident host is gone (crashed, leaving a stale handle behind), so the caller
    should reclaim the handle and respawn a fresh host rather than degrade to the
    in-process path forever. A plain :class:`OSError` from the attach
    *handshake* keeps the old fall-back-to-direct behavior (a live host declined
    this particular attach).
    """


def default_socket_path() -> Path:
    """Current publication namespace, independent of pre-feature live listeners."""
    configured = os.environ.get("AGENT_MCP_SERVE_SOCKET")
    return Path(configured) if configured else default_home_dir() / "current" / "serve.sock"


def legacy_socket_path() -> Path:
    return Path(os.environ.get("AGENT_MCP_SERVE_SOCKET") or default_home_dir() / "serve.sock")


def legacy_serving_hint(current: Path) -> bool:
    """Observe separate legacy namespace intent, never claim its ownership."""
    legacy = Path(absolute_handle(legacy_socket_path()))
    if legacy == current:
        return False
    for path in (legacy, _endpoint_path(legacy)):
        try:
            path.lstat()
            return True
        except FileNotFoundError:
            pass
        except OSError:
            return True
    return False


def absolute_handle(raw: str | Path) -> str:
    """``raw`` as an absolute path that still names the fixed serve handle.

    On POSIX the handle is a symlink that cutover atomically repoints at the
    promoted generation's socket, so it is made absolute lexically, never
    dereferenced: a dereferenced path would pin the caller (and anything it
    spawns) to one generation. Resolution is still attempted so a symlink
    loop raises here (``RuntimeError``/``OSError``) for the caller's fallback.
    """
    path = os.path.abspath(raw)
    Path(path).resolve()
    return path


def daemon_env_overrides(home: Path) -> dict[str, str]:
    """Environment for a serve host spawned with the absolute ``home`` as cwd.

    The host runs from ``home``, not from its spawner's directory, so every
    ``AGENT_MCP_*`` path it reads must already be absolute: a relative
    ``AGENT_MCP_HOME`` (or ``AGENT_MCP_SERVE_SOCKET``) would otherwise be
    re-resolved against ``home`` and name a nested directory.
    """
    env = {"AGENT_MCP_HOME": str(home)}
    raw = os.environ.get("AGENT_MCP_SERVE_SOCKET")
    if raw:
        env["AGENT_MCP_SERVE_SOCKET"] = absolute_handle(default_socket_path())
    return env


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
    yields a path -- but the subsequent connect raises
    :class:`HostUnreachableError`, and the caller then reclaims the handle (via
    :func:`discard_stale_handle`) and respawns a fresh host, rather than
    being wedged on the in-process path by the orphaned file.
    """
    if os.environ.get("AGENT_MCP_NO_SERVE"):
        return None
    raw = explicit
    path = Path(raw) if raw else default_socket_path()
    if _read_endpoint(path) is not None:
        return path
    return path if _is_socket_file(path) else None


def discard_stale_handle(explicit: str | None = None) -> None:
    """Remove a dead host's on-disk handle (AF_UNIX socket + endpoint sidecar).

    Called after a connect to an *advertised* handle proves the host is gone
    (:class:`HostUnreachableError`): a crashed host leaves its socket/sidecar
    behind, which keeps :func:`serve_socket_if_available` reporting it as
    available and wedges respawn -- so the forwarder would degrade to the
    in-process path forever instead of resurrecting the resident host.
    Removing the orphaned handle lets a fresh host be spawned, which re-creates
    it. Best-effort; the winning host re-creates its handle on bind (and holds the
    single-instance lease), so this never disturbs a live host.
    """
    raw = explicit
    path = Path(raw) if raw else default_socket_path()
    for p in (path, _endpoint_path(path)):
        try:
            Path(p).unlink()
        except OSError:
            pass


def _connect(path: str | Path, *, timeout: float | None = None) -> tuple[socket.socket, str | None]:
    """Open a blocking client connection; return ``(sock, token)``.

    Dials loopback TCP when an endpoint sidecar is present (token from it), else
    an AF_UNIX socket. Raises :class:`HostUnreachableError` when no live host
    answers the connect (a crashed host that left a stale handle), so the caller
    can reclaim the handle and respawn rather than degrade to the in-process path.
    """
    ep = _read_endpoint(path)
    if ep is not None:
        try:
            options = {} if timeout is None else {"timeout": timeout}
            sock = socket.create_connection((_TCP_HOST, ep["port"]), **options)
        except OSError as exc:
            raise HostUnreachableError(
                f"serve host unreachable at {_TCP_HOST}:{ep['port']}: {exc}") from exc
        return sock, ep.get("token")
    if not hasattr(socket, "AF_UNIX"):
        raise HostUnreachableError(f"no serve endpoint for {path}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if timeout is not None:
            sock.settimeout(timeout)
        sock.connect(str(path))
    except OSError as exc:
        _close(sock)
        raise HostUnreachableError(
            f"serve host unreachable at unix:{path}: {exc}") from exc
    return sock, None


def _read_ack_line(sock: socket.socket, *, deadline: float | None = None) -> bytes:
    """Read one newline-terminated line directly off the socket.

    Reads a byte at a time so it consumes **exactly** up to (and including) the
    first newline and no further -- a buffered reader (``socket.makefile``) could
    read ahead past the ack and swallow an early host notification that
    :func:`pump` must still deliver. Only used once, for the small attach ack.
    """
    buf = bytearray()
    while True:
        if deadline is not None:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("serve handshake deadline expired")
            sock.settimeout(remaining)
        b = sock.recv(1)
        if not b:
            break
        buf += b
        if len(buf) > 65536:
            raise OSError("serve handshake exceeds its byte budget")
        if b == b"\n":
            break
    return bytes(buf)


def attach(path: str | Path, bridge: str | Path, *, expected_generation: str | None = None,
           expected_root: str | None = None) -> socket.socket:
    """Attach a full MCP session on the host; return the raw connected socket.

    Sends the ``attach`` op and verifies the ack; thereafter the caller speaks
    line-delimited MCP JSON-RPC directly over the socket while the host runs that
    session's upstream + decorator pipeline. Raises :class:`HostUnreachableError`
    (an ``OSError`` subclass) if no live host answers the connect -- the caller
    reclaims the stale handle and respawns; raises a plain ``OSError`` if a *live*
    host refuses the attach handshake -- the caller falls back to a direct
    in-process bridge without respawning.
    """
    from .execution_context import request_fields
    from .request_context import capture_context, supports_context

    fields = request_fields(path, bridge)
    req: dict = {
        "op": "attach", "bridge": str(bridge),
        "configuration_context": capture_context(str(bridge)),
    }
    sock, token = _connect(path)
    req.update(fields)
    if expected_generation is not None:
        req["generation"] = expected_generation
    if expected_root is not None:
        req["runtime_root"] = expected_root
    if token is not None:
        req["token"] = token
    try:
        hello: dict = {"op": "ping"}
        if token is not None:
            hello["token"] = token
        sock.sendall((json.dumps(hello) + "\n").encode())
        line = _read_ack_line(sock)
        try:
            capable = supports_context(json.loads(line))
        except (ValueError, TypeError):
            capable = False
        if not capable:
            raise OSError("resident does not support caller configuration context; use the direct bridge")
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


def pump(sock: socket.socket, *, session_owner=None) -> None:
    """Bidirectionally pump line-delimited JSON-RPC between stdio and ``sock``.

    stdin -> socket and socket -> stdout run on two daemon threads (blocking I/O,
    no event loop). The **host->client** direction is authoritative for session
    end: on stdin EOF the client half-closes the socket (so the host drains and
    closes), and the pump keeps forwarding the host's final replies until the
    host closes the connection. A parent-death watchdog covers the case where
    stdin EOF never arrives (the Windows ``cmd``-shim grandchild). An opt-in
    adopted conduit instead supplies its explicitly pinned session owner;
    intermediate ancestors are not its lifetime boundary.
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
    try:
        if session_owner is None:
            install_parent_death_watchdog(_stop)
        else:
            session_owner.start(_stop)
        threading.Thread(target=_client_to_host, name="agent-mcp-fwd-in",
                         daemon=True).start()
        threading.Thread(target=_host_to_client, name="agent-mcp-fwd-out",
                         daemon=True).start()
        done.wait()
    finally:
        _close(sock)
        if session_owner is not None:
            session_owner.close()
