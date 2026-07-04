"""Small OS utilities for the Session Host layer (packaged, not the spike copy)."""

from __future__ import annotations

import os
import sys

_STILL_ACTIVE = 259


def pid_alive(pid: int | None) -> bool:
    """Cross-platform: is ``pid`` a currently-running process?"""
    if not pid:
        return False
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            code = wintypes.DWORD()
            k.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            if not k.GetExitCodeProcess(h, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            k.CloseHandle(h)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def kill_pid(pid: int | None) -> None:
    """Best-effort tree-kill of a process by pid (cross-platform, idempotent).

    Used by the frontend to reap a Session Host process (and, via the host's
    kill-on-close job / process group, its child) once the host's session has
    reached its own stop or is being force-reaped under the sprawl bound.
    Swallows "already gone" and permission errors -- reaping is best-effort.
    """
    if not pid:
        return
    if sys.platform == "win32":
        import subprocess

        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        pass
