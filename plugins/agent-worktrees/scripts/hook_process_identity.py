"""Standalone OS birth tokens for hook ownership checks, without runtime imports.

The OS implementation matches agent_worktrees.locks; unavailable tokens never
establish ownership. This module must remain importable with isolated Python.
"""

from __future__ import annotations

import platform
from pathlib import Path


def process_start_time(pid: int) -> str | None:
    """A stable, per-process **start-time identity token** for ``pid``, or None.

    The token only needs to be *stable for the life of the process* and *differ*
    when the pid is later reused -- it is compared for equality, never
    interpreted as a wall-clock. Returns None when the process is gone or its
    start-time can't be read; callers must not establish ownership from None.

    * Windows -- the process creation ``FILETIME`` (100 ns ticks since 1601) via
      ``GetProcessTimes``.
    * macOS -- microsecond birth time from ``proc_pidinfo(PROC_PIDTBSDINFO)``.
    * Linux -- field 22 (``starttime``, clock ticks since boot) of
      ``/proc/<pid>/stat``.
    """
    if pid <= 0:
        return None
    if platform.system() == "Windows":
        return _start_time_windows(pid)
    if platform.system() == "Darwin":
        return _start_time_darwin(pid)
    return _start_time_posix(pid)


def _start_time_darwin(pid: int) -> str | None:
    import ctypes

    # Public proc_bsdinfo ABI from XNU bsd/sys/proc_info.h.
    class BsdInfo(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_uint32) for name in (
                "flags", "status", "exit_status", "pid", "parent_pid",
                "uid", "gid", "real_uid", "real_gid", "saved_uid", "saved_gid", "reserved",
            )
        ] + [
            ("command", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
            ("files", ctypes.c_uint32), ("group", ctypes.c_uint32),
            ("job_count", ctypes.c_uint32), ("tty_device", ctypes.c_uint32),
            ("tty_group", ctypes.c_uint32), ("nice", ctypes.c_int32),
            ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64),
        ]

    try:
        query = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pidinfo
        query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        query.restype = ctypes.c_int
        info = BsdInfo()
        size = ctypes.sizeof(info)
        if query(pid, 3, 0, ctypes.byref(info), size) != size:
            return None
    except (OSError, AttributeError):
        return None
    if info.pid != pid or not info.start_seconds or info.start_microseconds >= 1_000_000:
        return None
    return f"{info.start_seconds}:{info.start_microseconds}"


def process_is_alive(pid: int, birth: str) -> bool:
    """Require the original birth and native live state, not a retained dead PID."""
    if not isinstance(birth, str) or not birth:
        return False
    if platform.system() == "Windows":
        return _start_time_windows(pid, require_live=True) == birth
    return process_start_time(pid) == birth


def _windows_kernel():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    k32.GetProcessTimes.restype = wintypes.BOOL
    k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    k32.WaitForSingleObject.restype = wintypes.DWORD
    return k32


def _windows_handle_birth(k32, handle) -> str:
    import ctypes
    from ctypes import wintypes

    creation, exit_, kernel, user = (wintypes.FILETIME() for _ in range(4))
    if not k32.GetProcessTimes(
        handle, ctypes.byref(creation), ctypes.byref(exit_),
        ctypes.byref(kernel), ctypes.byref(user),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)


def _start_time_windows(pid: int, *, require_live: bool = False) -> str | None:
    k32 = _windows_kernel()
    access = 0x1000 | (0x100000 if require_live else 0)
    handle = k32.OpenProcess(access, False, pid)
    if not handle:
        return None
    try:
        birth = _windows_handle_birth(k32, handle)
        if require_live and k32.WaitForSingleObject(handle, 0) != 0x102:
            return None
        return birth
    except OSError:
        return None
    finally:
        k32.CloseHandle(handle)


def open_windows_caller_lifetime():
    """Pin the original caller across only the selected, known venv redirector.

    CPython's redirector waits for the real interpreter after its own caller
    exits. Match its actual image to the selected venv before crossing that
    one link; retain both native objects and reject reused-parent birth order.
    This observation never assigns a Job or attests descendant settlement.
    """
    import ctypes
    import os
    import sys
    from ctypes import wintypes

    k32 = _windows_kernel()
    k32.GetCurrentProcess.restype = wintypes.HANDLE
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    handles = []

    def close():
        errors = []
        while handles:
            if not k32.CloseHandle(handles.pop()):
                errors.append(ctypes.WinError(ctypes.get_last_error()))
        if errors:
            raise errors[0]

    def pin(pid):
        if pid <= 1 or pid == os.getpid():
            raise ProcessLookupError("original caller identity unavailable")
        handle = k32.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        handles.append(handle)
        return handle

    def image(handle):
        value, size = ctypes.create_unicode_buffer(32768), wintypes.DWORD(32768)
        if not k32.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        return value.value

    def live():
        return bool(handles) and all(k32.WaitForSingleObject(handle, 0) == 0x102 for handle in handles)

    try:
        parent_pid = os.getppid()
        parent = pin(parent_pid)
        parent_birth = int(_windows_handle_birth(k32, parent))
        current = k32.GetCurrentProcess()
        if parent_birth > int(_windows_handle_birth(k32, current)) or not live():
            raise ProcessLookupError("original parent identity changed")
        selected = Path(sys.executable)
        if os.path.samefile(image(parent), selected) and not os.path.samefile(image(current), selected):
            if not (selected.parent.parent / "pyvenv.cfg").is_file() or not os.path.samefile(image(current), sys._base_executable):
                raise ProcessLookupError("selected venv interpreter identity changed")

            class ProcessBasicInformation(ctypes.Structure):
                _fields_ = [
                    ("reserved", ctypes.c_void_p), ("peb", ctypes.c_void_p),
                    ("reserved2", ctypes.c_void_p * 2), ("pid", ctypes.c_size_t),
                    ("parent_pid", ctypes.c_size_t),
                ]
            query = ctypes.WinDLL("ntdll").NtQueryInformationProcess
            query.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p,
                              wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
            query.restype = wintypes.LONG
            info, size = ProcessBasicInformation(), wintypes.ULONG()
            if query(parent, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(size)) < 0 or info.pid != parent_pid:
                raise ProcessLookupError("venv redirector caller identity unavailable")
            caller = pin(info.parent_pid)
            if int(_windows_handle_birth(k32, caller)) > parent_birth or not live():
                raise ProcessLookupError("venv redirector caller identity changed")
        return live, close
    except BaseException:
        close()
        raise


def _start_time_posix(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="ignore")
    except OSError:
        return None
    # comm (field 2) may contain spaces/parens -- split on the LAST ')'.
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    rest = stat[rparen + 1:].split()
    # After comm, `rest` holds fields from state (field 3) onward, so starttime
    # (field 22) is rest[19]: state=0, ppid=1, ... starttime=19.
    if len(rest) < 20:
        return None
    token = rest[19]
    return token if token.isdigit() else None
