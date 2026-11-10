"""Fresh, complete native PSMux server identities, without executing PSMux."""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes

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


def _processes() -> list[tuple[int, str]]:
    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
            ("pid", wintypes.DWORD), ("heap", ctypes.c_void_p),
            ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
            ("flags", wintypes.DWORD), ("image", wintypes.WCHAR * 260),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
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
                if ctypes.get_last_error() != _ERROR_NO_MORE_FILES:
                    raise MuxObservationUnavailable("Native mux process enumeration is incomplete")
                return result
    finally:
        kernel.CloseHandle(handle)


def servers() -> dict[int, str]:
    result = {}
    for pid, _image in _processes():
        before = locks.process_start_time(pid)
        command = windows_process_census.command_line(pid)
        if before is None or command is None or locks.process_start_time(pid) != before:
            raise MuxObservationUnavailable("Native mux process identity is unavailable")
        match = _COMMAND.match(command)
        mode = match[2].strip('"') if match else None
        if mode == "server" and not match[1]:
            result[pid] = before
        elif mode not in _CLIENTS:
            # Unknown/global-option forms might be servers; never omit them.
            raise MuxObservationUnavailable("Native mux process mode is unconfirmed")
    return result
