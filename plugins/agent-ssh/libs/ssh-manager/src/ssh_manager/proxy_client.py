"""Windowless binary stdio client for the owning SSH proxy broker."""

from __future__ import annotations

import os
import re
import socket
import sys
import threading
from typing import BinaryIO


def _windows_stream(identifier: int, mode: str) -> BinaryIO:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel.GetStdHandle.restype = wintypes.HANDLE
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    handle = kernel.GetStdHandle(identifier)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise OSError("SSH proxy has no inherited standard stream")
    current = kernel.GetCurrentProcess()
    duplicate = wintypes.HANDLE()
    if not kernel.DuplicateHandle(
        current, handle, current, ctypes.byref(duplicate), 0, False, 2,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    flags = os.O_BINARY | (os.O_RDONLY if mode == "rb" else os.O_WRONLY)
    descriptor = msvcrt.open_osfhandle(duplicate.value, flags)
    return os.fdopen(descriptor, mode, buffering=0)


def _write_all(stream: BinaryIO, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = stream.write(view)
        if written is None or written <= 0:
            raise BrokenPipeError("SSH proxy output closed")
        view = view[written:]
    stream.flush()


def relay(
    port: int, capability: str, source: BinaryIO, destination: BinaryIO,
) -> None:
    with socket.create_connection(("127.0.0.1", port), timeout=10) as connection:
        connection.settimeout(None)
        connection.sendall(capability.encode("ascii"))
        errors: list[OSError] = []

        def send_input() -> None:
            try:
                read = getattr(source, "read1", source.read)
                while data := read(65536):
                    connection.sendall(data)
                connection.shutdown(socket.SHUT_WR)
            except OSError as exc:
                errors.append(exc)
                try:
                    connection.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        sender = threading.Thread(target=send_input, daemon=True)
        sender.start()
        while data := connection.recv(65536):
            _write_all(destination, data)
        if errors:
            raise errors[0]


def main() -> int:
    if os.name == "nt":
        # pythonw intentionally has no sys.stdin/out; OpenSSH still supplies OS pipes.
        source = _windows_stream(-10, "rb")
        destination = _windows_stream(-11, "wb")
        errors = _windows_stream(-12, "wb")
    else:
        source, destination, errors = sys.stdin.buffer, sys.stdout.buffer, sys.stderr.buffer
    try:
        if len(sys.argv) != 3 or not re.fullmatch(r"[0-9a-f]{64}", sys.argv[2]):
            raise ValueError("Expected a broker port and connection capability")
        port = int(sys.argv[1])
        if not 1 <= port <= 65535:
            raise ValueError("Invalid SSH proxy broker port")
        relay(port, sys.argv[2], source, destination)
        return 0
    except (OSError, ValueError) as exc:
        _write_all(errors, f"SSH proxy: {exc}\n".encode("utf-8", errors="replace"))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
