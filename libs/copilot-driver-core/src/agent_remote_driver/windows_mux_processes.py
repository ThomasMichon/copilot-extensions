"""Fresh, complete native PSMux server identities, without executing PSMux."""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from typing import Any

from . import mux_process_identity as locks, mux_process_identity as windows_process_census
from .mux_observation_types import ObservationUnavailable as MuxObservationUnavailable

_IMAGES = {"psmux.exe", "pmux.exe", "tmux.exe"}
_COMMAND = re.compile(
    r'^(?:"[^"]+"|[^\s"]+)\s+(?:("-L"|-L)\s+(?:"[^"]+"|[^\s"]+)\s+)?("[^"]+"|[^\s"]+)(?:\s|$)',
)
_CLIENTS = {
    "attach-session", "attach", "at", "a", "new-session", "new",
    "set-option", "set", "display-message", "display", "has-session", "has",
}
_TH32CS_SNAPPROCESS = 2
_ERROR_NO_MORE_FILES = 18
# Windows-only ctypes members are absent from non-Windows type stubs.
_native: Any = ctypes


def _processes() -> list[tuple[int, str]]:
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("image", wintypes.WCHAR * 260),
        ]

    kernel = _native.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    for name in ("Process32FirstW", "Process32NextW"):
        function = getattr(kernel, name)
        function.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
        function.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateToolhelp32Snapshot(_TH32CS_SNAPPROCESS, 0)
    if not handle or handle == ctypes.c_void_p(-1).value:
        raise MuxObservationUnavailable("Native mux process enumeration is unavailable")
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        if not kernel.Process32FirstW(handle, ctypes.byref(entry)):
            raise MuxObservationUnavailable("Native mux process enumeration is incomplete")
        result = []
        while True:
            if entry.image.casefold() in _IMAGES:
                result.append((int(entry.pid), entry.image.casefold()))
                if len(result) > 256:
                    raise MuxObservationUnavailable("Native mux process budget exceeded")
            if not kernel.Process32NextW(handle, ctypes.byref(entry)):
                if _native.get_last_error() != _ERROR_NO_MORE_FILES:
                    raise MuxObservationUnavailable("Native mux process enumeration is incomplete")
                return result
    finally:
        kernel.CloseHandle(handle)


def servers() -> dict[int, str]:
    """Return live mux servers plus opaque processes that may be servers.

    A process this token may not open (typically an elevated server observed by
    a medium-integrity monitor) cannot be classified, so it is returned with its
    snapshot start time; callers' registration coverage must then prove it.
    Verifiably exited processes are skipped rather than blanking observation.
    """
    result = {}
    snapshot: dict[int, str] | None = None
    for pid, _image in _processes():
        before = locks.process_start_time(pid)
        command = windows_process_census.command_line(pid) if before is not None else None
        if before is None or command is None:
            field = "start time" if before is None else "command line"
            state, error = locks.access(pid)
            if state == "gone":
                continue
            if state == "denied":
                if snapshot is None:
                    snapshot = locks.system_start_times()
                if snapshot.get(pid) is not None:
                    result[pid] = snapshot[pid]
                    continue
            raise MuxObservationUnavailable(
                f"Native mux process identity is unavailable: pid {pid} {field} "
                f"is unreadable ({state}, Win32 error {error})",
            )
        after = locks.process_start_time(pid)
        if after != before:
            if after is None and locks.access(pid)[0] == "gone":
                continue
            raise MuxObservationUnavailable(
                f"Native mux process identity is unavailable: pid {pid} start time changed",
            )
        match = _COMMAND.match(command)
        mode = match[2].strip('"') if match else None
        if match and mode == "server" and not match[1]:
            result[pid] = before
        elif mode not in _CLIENTS:
            # Unknown/global-option forms might be servers; never omit them.
            raise MuxObservationUnavailable(f"Native mux process mode is unconfirmed: pid {pid}")
    return result
