"""Delegate a plugin's heavy *core* over a tokened, discovered IPC seam.

A service-bearing Copilot CLI plugin ships a light **host adapter** and reaches a
heavy **core** -- an in-process default, a remote host engine, or a container --
as *just another transport target*. This module is the read-side of that seam: a
generic client transport that

1. **discovers** the core's endpoint via ``endpoint-rendezvous`` (the same
   override -> rendezvous-file -> legacy ladder every migrated service uses),
2. picks the endpoint's transport that is dialable **in this process's context**
   (``unix`` socket / Windows named ``pipe`` / loopback ``tcp``),
3. ships the caller's ``request`` dict as **newline-framed JSON** -- wire-identical
   to the framing agent-vault's daemon already speaks -- with an optional
   **bearer token** attached (token-bind), and returns the decoded response, or
4. returns **``None`` to fall through** when no core is wired or reachable.

That ``None`` fall-through is the whole point of the seam's safety property:
absent a wired core, the plugin's built-in transports and user-mode path still
handle the request, so **no plugin ever requires a core, a tunnel, or a broker**
to work on its own host (``visions/plugin-services`` §standalone-reachability).
It is an opt-in boundary crossing layered on top of the transport ladder
(``docs/patterns/service-transport.md`` rung 4).

The seam is deliberately **protocol-agnostic**: it frames and ships whatever
``request`` dict the caller hands it and returns whatever dict comes back. It
carries no knowledge of any one plugin's actions, so agent-vault and agent-index
reuse the same primitive.

Pure standard library; no runtime dependencies beyond ``endpoint-rendezvous``
(itself stdlib-only, and vendored alongside this module in a consuming package).
"""

from __future__ import annotations

import ctypes
import json
import socket
import sys
from collections.abc import Callable
from pathlib import Path

from endpoint_rendezvous import (
    Endpoint,
    EndpointUnavailable,
    connect_probe,
    default_runtime_dir,
    resolve,
)

IS_WINDOWS = sys.platform == "win32"

DEFAULT_TIMEOUT = 5.0
_CONNECT_TIMEOUT = 5.0

# The request key carrying the bearer token. A core that enforces token-bind
# validates it; a core that does not simply ignores the extra key (unknown keys
# are ignored by the JSON-line daemons this seam targets), so attaching a token
# is always wire-safe.
TOKEN_KEY = "_token"  # noqa: S105 -- a JSON request key name, not a secret

# Provenance tag stamped onto a delegated response so a caller can tell a
# core-delegated reply from a built-in-transport reply.
TRANSPORT_TAG = "core-delegation"


def default_accept(transport: str) -> bool:
    """Whether this process can dial ``transport`` in its current context.

    A Unix socket is dialable only off Windows (where ``AF_UNIX`` server support
    is absent from the async stacks these daemons use); a named pipe only on
    Windows; loopback TCP anywhere. A core that advertises several transports
    (via the rendezvous ``alt`` list) is resolved to the one that passes here.
    """
    if transport == "tcp":
        return True
    if transport == "unix":
        return not IS_WINDOWS and hasattr(socket, "AF_UNIX")
    if transport == "pipe":
        return IS_WINDOWS
    return False


def _frame(payload: dict) -> bytes:
    """Encode a request as a single newline-terminated JSON line."""
    return (json.dumps(payload) + "\n").encode()


def _decode(buf: bytes) -> dict | None:
    """Decode a newline-framed JSON response; ``None`` if empty/malformed."""
    if not buf:
        return None
    try:
        result = json.loads(buf.decode().strip())
    except (ValueError, UnicodeDecodeError):
        return None
    return result if isinstance(result, dict) else None


def _send_socket(
    family: int, address, payload: dict, timeout: float | None
) -> dict | None:
    """Send a framed request over an ``AF_UNIX``/``AF_INET`` stream socket."""
    try:
        s = socket.socket(family, socket.SOCK_STREAM)
    except OSError:
        return None
    try:
        s.settimeout(_CONNECT_TIMEOUT)
        s.connect(address)
        s.settimeout(timeout)
        s.sendall(_frame(payload))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
        return _decode(buf)
    except OSError:
        return None
    finally:
        try:
            s.close()
        except OSError:
            pass


def _send_unix(address: str, payload: dict, timeout: float | None) -> dict | None:
    if not hasattr(socket, "AF_UNIX"):
        return None
    return _send_socket(socket.AF_UNIX, address, payload, timeout)


def _send_tcp(host: str, port: int, payload: dict, timeout: float | None) -> dict | None:
    return _send_socket(socket.AF_INET, (host, port), payload, timeout)


# -- Windows named pipe -----------------------------------------------------

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_PIPE_BUSY = 231
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _kernel32():
    """kernel32 with the pointer-correct signatures this module needs.

    Setting ``restype``/``argtypes`` matters on 64-bit Windows: a HANDLE is a
    pointer, and the default ``c_int`` restype would truncate it.
    """
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    k32.CreateFileW.restype = ctypes.c_void_p
    k32.CreateFileW.argtypes = [
        ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
        ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p,
    ]
    k32.WaitNamedPipeW.restype = ctypes.c_int
    k32.WaitNamedPipeW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    k32.WriteFile.restype = ctypes.c_int
    k32.WriteFile.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]
    k32.ReadFile.restype = ctypes.c_int
    k32.ReadFile.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p,
    ]
    k32.CloseHandle.restype = ctypes.c_int
    k32.CloseHandle.argtypes = [ctypes.c_void_p]
    return k32


def _send_pipe(pipe_path: str, payload: dict, timeout: float | None) -> dict | None:
    """Send a framed request over a Windows named pipe; ``None`` on any failure."""
    if not IS_WINDOWS:
        return None
    import time

    k32 = _kernel32()
    deadline = time.monotonic() + (timeout if timeout is not None else DEFAULT_TIMEOUT)
    handle = None
    try:
        while True:
            handle = k32.CreateFileW(
                pipe_path, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None
            )
            if handle and handle != _INVALID_HANDLE_VALUE:
                break
            if k32.GetLastError() == _ERROR_PIPE_BUSY and time.monotonic() < deadline:
                k32.WaitNamedPipeW(pipe_path, 200)
                continue
            return None

        data = _frame(payload)
        written = ctypes.c_uint32(0)
        if not k32.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            return None

        buf = ctypes.create_string_buffer(4096)
        out = b""
        while b"\n" not in out:
            read = ctypes.c_uint32(0)
            if not k32.ReadFile(handle, buf, 4096, ctypes.byref(read), None) or read.value == 0:
                break
            out += buf.raw[: read.value]
        return _decode(out)
    except OSError:
        return None
    finally:
        if handle and handle != _INVALID_HANDLE_VALUE:
            k32.CloseHandle(handle)


def _dial(endpoint: Endpoint, payload: dict, timeout: float | None) -> dict | None:
    """Ship ``payload`` over the resolved endpoint's transport."""
    if endpoint.transport == "unix":
        return _send_unix(endpoint.address, payload, timeout)
    if endpoint.transport == "tcp":
        try:
            host, port = endpoint.tcp_host_port
        except ValueError:
            return None
        return _send_tcp(host, port, payload, timeout)
    if endpoint.transport == "pipe":
        return _send_pipe(endpoint.address, payload, timeout)
    return None


def delegate(
    app: str,
    request: dict,
    *,
    timeout: float | None = DEFAULT_TIMEOUT,
    token: str | None = None,
    override: str | Endpoint | None = None,
    legacy: str | Endpoint | None = None,
    runtime_dir: Path | str | None = None,
    accept: Callable[[str], bool] | None = None,
    probe: Callable[[Endpoint], bool] | None = connect_probe,
    tag: bool = True,
) -> dict | None:
    """Delegate ``request`` to ``app``'s wired core; return its response or ``None``.

    Discovery uses the ``endpoint-rendezvous`` ladder rooted at ``runtime_dir``
    (default ``~/.<app>/run``): an explicit ``override`` (or a
    ``"<transport>:<address>"`` spec / :class:`Endpoint`) wins, else the core's
    rendezvous file if present and not stale, else ``legacy`` if given. When
    nothing resolves -- **no core is wired** -- this returns ``None`` so the
    caller falls through to its built-in transports / user-mode path.

    The resolved endpoint is narrowed to a transport dialable here via
    ``accept`` (default :func:`default_accept`); if none is, ``None`` is
    returned. A ``token`` (when supplied) is attached under :data:`TOKEN_KEY`
    before framing. The request is shipped as newline-framed JSON; the decoded
    response dict is returned (stamped with a ``_transport`` provenance tag when
    ``tag`` is set), or ``None`` on any send/parse failure.
    """
    rdir = Path(runtime_dir) if runtime_dir is not None else default_runtime_dir(app)
    try:
        endpoint = resolve(rdir, override=override, legacy=legacy, probe=probe)
    except EndpointUnavailable:
        return None

    usable = endpoint.usable(accept or default_accept)
    if usable is None:
        return None

    payload = dict(request)
    if token:
        payload.setdefault(TOKEN_KEY, token)

    try:
        result = _dial(usable, payload, timeout)
    except Exception:
        return None
    if result is not None and tag:
        result.setdefault("_transport", TRANSPORT_TAG)
    return result
