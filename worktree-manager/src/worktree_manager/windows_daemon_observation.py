"""Read-only native process observations for Windows daemon diagnostics."""

from __future__ import annotations

from contextlib import contextmanager
import ctypes as c
from ctypes import wintypes as w
from functools import lru_cache
import os

from zdd.private_protocol import PrivateIPCError


class _ProcessEntry(c.Structure):
    _fields_ = [
        ("size", w.DWORD), ("usage", w.DWORD), ("pid", w.DWORD),
        ("heap", c.c_size_t), ("module", w.DWORD), ("threads", w.DWORD),
        ("parent", w.DWORD), ("priority", w.LONG), ("flags", w.DWORD),
        ("name", w.WCHAR * 260),
    ]


class _UnicodeString(c.Structure):
    _fields_ = [("length", c.c_uint16), ("maximum", c.c_uint16), ("buffer", c.c_void_p)]


@lru_cache(maxsize=1)
def _native():
    if os.name != "nt":
        raise OSError("Native Windows process observation is unavailable")
    from zdd.private_windows_native import Native

    try:
        return Native()
    except PrivateIPCError as error:
        raise OSError("Native process observation is unavailable") from error


def _close(native, handle) -> None:
    try:
        native.close(handle)
    except PrivateIPCError as error:
        raise OSError("Native process observation could not close its handle") from error


@contextmanager
def _process(pid: int):
    native = _native()
    handle = native.k.OpenProcess(0x1000, False, pid)
    if not handle:
        raise OSError("Native process observation could not open the process")
    try:
        yield native, handle
    except PrivateIPCError as error:
        raise OSError("Native process identity could not be verified") from error
    finally:
        _close(native, handle)


def current_user_sid() -> str:
    try:
        return _native().current_sid()
    except PrivateIPCError as error:
        raise OSError("Native current-user identity could not be verified") from error


def process_user_sid(pid: int) -> str:
    with _process(pid) as (native, handle):
        return native.process_sid(handle)


def process_start_time(pid: int) -> str:
    with _process(pid) as (native, handle):
        return native.identity(handle, pid).start_time


def _decode_command_line(buffer) -> str:
    if len(buffer) < c.sizeof(_UnicodeString):
        raise OSError("Native command-line result is truncated")
    value = _UnicodeString.from_buffer(buffer)
    start = c.addressof(buffer)
    if (
        value.length % 2 or value.length > value.maximum
        or value.buffer is None or value.buffer < start + c.sizeof(_UnicodeString)
        or value.buffer > start + len(buffer) - value.length
    ):
        raise OSError("Native command-line result has invalid bounds")
    try:
        return c.string_at(value.buffer, value.length).decode("utf-16-le")
    except UnicodeError as error:
        raise OSError("Native command-line result is not UTF-16") from error


def process_command_line(pid: int) -> str:
    with _process(pid) as (_native_api, handle):
        query = c.WinDLL("ntdll").NtQueryInformationProcess
        query.argtypes = [c.c_void_p, c.c_uint32, c.c_void_p, c.c_uint32, c.POINTER(c.c_uint32)]
        query.restype = c.c_int32
        size = c.c_uint32()
        # ProcessCommandLineInformation returns caller-sized UNICODE_STRING data.
        status = query(handle, 60, None, 0, c.byref(size))
        if status not in (-1073741820, -1073741789) or not (
            c.sizeof(_UnicodeString) <= size.value <= 131072
        ):
            raise OSError("Native command-line sizing failed")
        buffer = c.create_string_buffer(size.value)
        if query(handle, 60, buffer, len(buffer), c.byref(size)) != 0:
            raise OSError("Native command-line observation failed")
        return _decode_command_line(buffer)


def process_ids() -> set[int]:
    kernel = _native().k
    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    kernel.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(_ProcessEntry)]
    kernel.Process32FirstW.restype = w.BOOL
    kernel.Process32NextW.argtypes = [w.HANDLE, c.POINTER(_ProcessEntry)]
    kernel.Process32NextW.restype = w.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == c.c_void_p(-1).value:
        raise OSError("Native process census failed")
    try:
        entry = _ProcessEntry()
        entry.size = c.sizeof(entry)
        if not kernel.Process32FirstW(snapshot, c.byref(entry)):
            raise OSError("Native process census could not read its first entry")
        result = {int(entry.pid)}
        while kernel.Process32NextW(snapshot, c.byref(entry)):
            result.add(int(entry.pid))
        if c.get_last_error() != 18:
            raise OSError("Native process census did not complete")
        return result
    finally:
        _close(_native(), snapshot)


def mux_daemon_pids() -> set[int]:
    from .mux_daemon_cutover import _is_mux_daemon_cmdline

    result = set()
    for pid in process_ids():
        try:
            command = process_command_line(pid)
        except OSError:
            continue
        if _is_mux_daemon_cmdline(command):
            result.add(pid)
    return result
