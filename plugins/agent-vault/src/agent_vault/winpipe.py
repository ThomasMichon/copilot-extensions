"""Windows named-pipe transport for agent-vault (standard library only).

The vision's transport ladder (``docs/patterns/service-transport.md``) puts a
**named pipe** at rung 2 -- the idiomatic Windows-local endpoint, immune by
construction to the loopback port lottery and the Windows/WSL shared-``127.0.0.1``
collision. This module provides both halves with no third-party dependency:

* :func:`pipe_send` -- a synchronous client dialer (``CreateFileW`` +
  ``WaitNamedPipe`` retry via ``ctypes``) that speaks the same newline-framed
  JSON as the TCP/UDS transports.
* :func:`start_pipe_server` -- an ``asyncio`` proactor-loop pipe listener that
  bridges each accepted pipe instance into the daemon's existing
  ``handle_client(reader, writer)`` coroutine.

Everything here is a no-op / unavailable off Windows (guarded on
``sys.platform``), so importing the module is always safe.
"""

from __future__ import annotations

import asyncio
import ctypes
import json
import sys
from collections.abc import Callable

IS_WINDOWS = sys.platform == "win32"

DEFAULT_PIPE_PATH = r"\\.\pipe\agent-vault"

# Win32 constants
_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_PIPE_BUSY = 231
_INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


def _kernel32():
    """Return kernel32 with the handful of signatures this module uses set.

    Setting ``restype``/``argtypes`` matters on 64-bit Windows: a HANDLE is a
    pointer, and the default ``c_int`` restype would *truncate* it and corrupt
    the comparison against ``INVALID_HANDLE_VALUE``.
    """
    k32 = ctypes.windll.kernel32
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


def pipe_send(pipe_path: str, request: dict, timeout: float | None = 5.0) -> dict | None:
    """Send a newline-framed JSON request over a Windows named pipe; parse reply.

    Returns the decoded response dict, or ``None`` on any failure (unreachable
    pipe, timeout, protocol error) so callers can fall through to another
    transport. Off Windows this always returns ``None``.
    """
    if not IS_WINDOWS:
        return None
    import time

    k32 = _kernel32()
    deadline = time.monotonic() + (timeout if timeout is not None else 5.0)
    handle = None
    try:
        while True:
            handle = k32.CreateFileW(
                pipe_path, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None
            )
            if handle and handle != _INVALID_HANDLE_VALUE:
                break
            err = k32.GetLastError()
            if err == _ERROR_PIPE_BUSY and time.monotonic() < deadline:
                k32.WaitNamedPipeW(pipe_path, 200)
                continue
            return None

        data = (json.dumps(request) + "\n").encode()
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
        if not out:
            return None
        return json.loads(out.decode().strip())
    except Exception:
        return None
    finally:
        if handle and handle != _INVALID_HANDLE_VALUE:
            k32.CloseHandle(handle)


async def start_pipe_server(
    pipe_path: str,
    connected_cb: Callable[[asyncio.StreamReader, asyncio.StreamWriter], object],
) -> list:
    """Start an asyncio named-pipe server, bridging each client to ``connected_cb``.

    ``connected_cb(reader, writer)`` mirrors the ``asyncio.start_server`` callback
    contract (it may return a coroutine, which is scheduled). Requires a proactor
    event loop (the Windows default). Returns the list of pipe-server objects
    (each has ``.close()``); raises off Windows or if the loop lacks pipe support.
    """
    if not IS_WINDOWS:
        raise RuntimeError("named pipes are Windows-only")
    loop = asyncio.get_event_loop()
    serve = getattr(loop, "start_serving_pipe", None)
    if serve is None:
        raise RuntimeError("event loop has no named-pipe support (need a proactor loop)")

    # Retain references to in-flight handler tasks so they aren't GC'd mid-run
    # (the server closures keep this set alive for the server's lifetime).
    pending: set = set()

    def factory() -> asyncio.Protocol:
        reader = asyncio.StreamReader()

        def _on_connect(r: asyncio.StreamReader, w: asyncio.StreamWriter) -> None:
            result = connected_cb(r, w)
            if asyncio.iscoroutine(result):
                task = asyncio.ensure_future(result)
                pending.add(task)
                task.add_done_callback(pending.discard)

        return asyncio.StreamReaderProtocol(reader, _on_connect)

    return await serve(factory, pipe_path)
