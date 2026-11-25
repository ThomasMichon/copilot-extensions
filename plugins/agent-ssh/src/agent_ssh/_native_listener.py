"""Bounded observations of a Windows IPv4 loopback listener."""

from __future__ import annotations

import ctypes
import socket
import struct
import sys
import time

_MAX_TABLE = 16 * 1024 * 1024


def listener_pid(port: int) -> int | None:
    if sys.platform != "win32":
        raise OSError("Native listener attribution requires Windows")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Select an exact TCP port")
    from ctypes import wintypes as w

    query = ctypes.WinDLL("iphlpapi", use_last_error=True).GetExtendedTcpTable
    query.argtypes = [w.LPVOID, ctypes.POINTER(w.DWORD), w.BOOL, w.ULONG, w.ULONG, w.ULONG]
    query.restype = w.DWORD
    size = w.DWORD()
    result = query(None, ctypes.byref(size), False, 2, 5, 0)
    if result != 122:
        raise OSError(result, "Native TCP listener sizing failed")
    for _ in range(3):
        if not 4 <= size.value <= _MAX_TABLE:
            raise OSError("Native TCP listener table exceeds its bound")
        data = ctypes.create_string_buffer(size.value)
        capacity = size.value
        result = query(data, ctypes.byref(size), False, 2, 5, 0)
        if result == 122:
            continue
        if result:
            raise OSError(result, "Native TCP listener observation failed")
        if not 4 <= size.value <= capacity:
            raise OSError("Native TCP listener table is incomplete")
        return _table_pid(data.raw[:size.value], port)
    raise OSError("Native TCP listener table changed during observation")


def _table_pid(raw: bytes, port: int) -> int | None:
    if len(raw) < 4:
        raise OSError("Native TCP listener table is incomplete")
    count = struct.unpack_from("<I", raw)[0]
    if count > (len(raw) - 4) // 24:
        raise OSError("Native TCP listener table is incomplete")
    owners: list[int] = []
    for index in range(count):
        state, address, local_port, _, _, pid = struct.unpack_from("<6I", raw, 4 + index * 24)
        if state == 2 and address in (0, 0x0100007F) and socket.ntohs(local_port & 0xFFFF) == port:
            # A wildcard listener is a conflict, not a private loopback endpoint.
            if address == 0 or pid == 0:
                return None
            owners.append(pid)
    return owners[0] if len(owners) == 1 else None


def ssh_banner(port: int) -> bool:
    deadline = time.monotonic() + 1.0
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1.0) as connection:
            raw = bytearray()
            while len(raw) < 256 and b"\n" not in raw:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                connection.settimeout(remaining)
                chunk = connection.recv(256 - len(raw))
                if not chunk:
                    return False
                raw.extend(chunk)
            line = bytes(raw).split(b"\n", 1)[0].rstrip(b"\r")
            return b"\n" in raw and line.startswith(b"SSH-2.0-") and len(line) > 8
    except OSError:
        return False
