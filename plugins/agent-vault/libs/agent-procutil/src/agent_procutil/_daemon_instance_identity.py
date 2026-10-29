"""Native birth observations and identity-pinned stop, without process scans."""

from __future__ import annotations

import ctypes
import os
import select
import signal
import sys
from pathlib import Path


def path_identity(path: Path) -> tuple[str, str]:
    """Use the native volume/file ID, independent of Python's stat ABI version."""
    if os.name != "nt":
        info = path.stat()
        return str(info.st_dev), str(info.st_ino)
    from ctypes import wintypes

    class FileId(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_uint64), ("id", ctypes.c_ubyte * 16)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(path), 0, 7, None, 3, 0x02000000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileId()
        if not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return str(info.volume), str(int.from_bytes(bytes(info.id), "little"))
    finally:
        kernel.CloseHandle(handle)


def delete_owned_file(path: Path, expected: tuple[str, str]) -> None:
    if os.name != "nt":
        if path_identity(path) != expected:
            raise OSError("daemon artifact identity changed")
        path.unlink()
        return
    from ctypes import wintypes

    class FileId(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_uint64), ("id", ctypes.c_ubyte * 16)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # Deny name replacement while checking and disposing the actual file.
    handle = kernel.CreateFileW(str(path), 0x10080, 3, None, 3, 0x00200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = FileId()
        if not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        actual = str(info.volume), str(int.from_bytes(bytes(info.id), "little"))
        if actual != expected:
            raise OSError("daemon artifact identity changed")
        disposition = wintypes.BOOL(True)
        if not kernel.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def _windows_handle(pid: int):
    from .daemon_broker import WindowsDaemonProcess

    return WindowsDaemonProcess(pid, ["daemon-instance"])


def _windows_birth(handle) -> str:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, *[ctypes.POINTER(wintypes.FILETIME)] * 4,
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    fields = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in fields)):
        raise ctypes.WinError(ctypes.get_last_error())
    created = fields[0]
    return f"windows:{(created.dwHighDateTime << 32) | created.dwLowDateTime}"


def birth_identity(pid: int, *, include_exited: bool = False) -> str:
    if type(pid) is not int or pid <= 0:
        raise ValueError("invalid daemon pid")
    if os.name == "nt":
        try:
            process = _windows_handle(pid)
        except OSError as error:
            if getattr(error, "winerror", None) in (87, 1168):
                raise ProcessLookupError(pid) from None
            raise
        try:
            if not include_exited and process.poll() is not None:
                raise ProcessLookupError(pid)
            return _windows_birth(process._handle)
        finally:
            process.close()
    if sys.platform.startswith("linux"):
        try:
            data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="surrogateescape")
            fields = data[data.rindex(")") + 2:].split()
            if not include_exited and fields[0] == "Z":
                raise ProcessLookupError(pid)
            ticks = int(fields[19])
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            namespace = os.stat(f"/proc/{pid}/ns/pid").st_ino
            return f"linux:{boot}:{namespace}:{ticks}"
        except FileNotFoundError:
            raise ProcessLookupError(pid) from None
    if sys.platform == "darwin":
        from ._darwin_caller_owned import _BSDInfo

        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        query = library.proc_pidinfo
        query.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
        ]
        query.restype = ctypes.c_int
        info = _BSDInfo()
        size = query(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info))
        if size != ctypes.sizeof(info) or info.pid != pid:
            raise OSError("daemon birth identity unavailable")
        if not include_exited and info.status == 5:
            raise ProcessLookupError(pid)
        return f"darwin:{info.start_sec}:{info.start_usec}"
    raise OSError("native daemon birth identity unsupported")


def created_process_identity(process) -> tuple[int, str] | None:
    """Capture only a pinned Windows handle or an actual unreaped direct child."""
    pid = getattr(process, "pid", None)
    if type(pid) is not int or pid <= 0:
        return None
    try:
        if os.name == "nt":
            handle = getattr(process, "_handle", None)
            if not isinstance(handle, int) or not handle:
                return None
            from ctypes import wintypes
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.GetProcessId.argtypes = [wintypes.HANDLE]
            kernel.GetProcessId.restype = wintypes.DWORD
            if kernel.GetProcessId(handle) != pid:
                return None
            return pid, _windows_birth(handle)
        if hasattr(os, "waitid"):
            os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            return pid, birth_identity(pid, include_exited=True)
    except (OSError, RuntimeError, ValueError):
        return None
    return None


def identity_state(pid: int, birth: str) -> str:
    try:
        return "matching" if birth_identity(pid) == birth else "reused"
    except ProcessLookupError:
        return "dead"
    except (OSError, ValueError):
        return "unknown"


def stop_identity(pid: int, birth: str, timeout: float = 5, *, graceful: bool = False) -> bool:
    """Observe the pinned target's exit; never signal an unproved numeric PID."""
    if pid == os.getpid():
        raise RuntimeError("external cleanup cannot stop its own caller")
    if os.name == "nt":
        process = _windows_handle(pid)
        try:
            if process.poll() is not None:
                return True
            if _windows_birth(process._handle) != birth:
                return False
            process.terminate()
            process.wait(timeout=timeout)
            return True
        finally:
            process.close()
    if sys.platform.startswith("linux"):
        from ._linux_pidfd import open_pidfd, send_pidfd_signal

        descriptor = open_pidfd(pid)
        try:
            if birth_identity(pid) != birth:
                return False
            send_pidfd_signal(descriptor, signal.SIGTERM if graceful else signal.SIGKILL)
            observer = select.poll()
            observer.register(descriptor, select.POLLIN)
            return bool(observer.poll(math_milliseconds(timeout)))
        finally:
            os.close(descriptor)
    # A Darwin birth recheck does not pin a numeric PID across kill(2).
    # Only a daemon's own expiry monitor may retire itself on that platform.
    return False


def math_milliseconds(seconds: float) -> int:
    return max(0, int(seconds * 1000))


def retire_self() -> None:
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = ctypes.c_void_p
        kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        kernel.TerminateProcess(kernel.GetCurrentProcess(), 1)
    else:
        # A resident owning its original group can retire that whole group
        # without a PID-reuse race: the caller itself pins the group identity.
        if os.getpgrp() == os.getpid():
            os.killpg(os.getpgrp(), signal.SIGKILL)
        else:
            os.kill(os.getpid(), signal.SIGKILL)
    # The native call must not silently leave an expired instance executing.
    os._exit(1)
