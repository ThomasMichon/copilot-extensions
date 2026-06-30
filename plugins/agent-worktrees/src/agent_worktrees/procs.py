"""Process discovery + termination by working directory (dependency-free).

When a worktree is reaped, processes that were spawned inside it (a stray
``gh``, an ``agent-worktrees status-updater``, a leftover shell) may outlive
the session with their **current working directory** still rooted in the
worktree.  On Windows an open cwd handle keeps a directory locked, so
``shutil.rmtree`` fails and the worktree is left behind as an empty shell.

This module finds and terminates those orphans.  It is intentionally
**dependency-free** (pure ``ctypes`` on Windows, ``/proc`` on POSIX, matching
the style already used in :mod:`agent_worktrees.sessions`) and degrades
gracefully -- any failure to enumerate or read a process is swallowed so a
cleanup is never crashed by this best-effort sweep.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

__all__ = ["processes_with_cwd_under", "terminate_processes_under"]


def _norm(p: str) -> str:
    """Normalize a path for prefix comparison (resolve, strip trailing sep)."""
    try:
        return os.path.normcase(os.path.normpath(os.path.abspath(p)))
    except (OSError, ValueError):
        return os.path.normcase(p.rstrip("\\/"))


def _is_under(cwd: str, root: str) -> bool:
    """True when ``cwd`` is ``root`` or a descendant of it."""
    if not cwd:
        return False
    c, r = _norm(cwd), _norm(root)
    return c == r or c.startswith(r + os.sep)


# ---------------------------------------------------------------------------
# POSIX
# ---------------------------------------------------------------------------

def _iter_processes_posix():
    """Yield ``(pid, cwd, name)`` for readable processes via ``/proc``."""
    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cwd = os.readlink(entry / "cwd")
        except OSError:
            continue
        name = ""
        try:
            name = (entry / "comm").read_text(errors="ignore").strip()
        except OSError:
            pass
        yield pid, cwd, name


def _terminate_posix(pid: int) -> bool:
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Windows -- read each process's cwd from its PEB (64-bit layout)
# ---------------------------------------------------------------------------

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_PROCESS_VM_READ = 0x0010
_PROCESS_TERMINATE = 0x0001
# Offsets into the 64-bit PEB / RTL_USER_PROCESS_PARAMETERS.
_PEB_PROCESS_PARAMETERS_OFFSET = 0x20
_RTL_CURRENT_DIRECTORY_OFFSET = 0x38  # UNICODE_STRING DosPath
_UNICODE_STRING_BUFFER_OFFSET = 8     # PWSTR Buffer within UNICODE_STRING (x64)


def _win_kernel32():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    k32.OpenProcess.restype = wintypes.HANDLE
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.ReadProcessMemory.argtypes = [
        wintypes.HANDLE, wintypes.LPCVOID, wintypes.LPVOID,
        ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t),
    ]
    k32.ReadProcessMemory.restype = wintypes.BOOL
    k32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    k32.TerminateProcess.restype = wintypes.BOOL
    k32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD,
        wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    k32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    return k32


def _win_enum_pids():
    import ctypes
    from ctypes import wintypes

    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    psapi.EnumProcesses.argtypes = [
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    psapi.EnumProcesses.restype = wintypes.BOOL

    count = 4096
    while True:
        arr = (wintypes.DWORD * count)()
        needed = wintypes.DWORD()
        if not psapi.EnumProcesses(arr, ctypes.sizeof(arr), ctypes.byref(needed)):
            return []
        got = needed.value // ctypes.sizeof(wintypes.DWORD)
        if got < count:
            return [arr[i] for i in range(got) if arr[i]]
        count *= 2  # buffer was full -- grow and retry


def _win_read_cwd(k32, pid: int) -> tuple[str, str]:
    """Return ``(cwd, exe_name)`` for ``pid`` (either may be ``""``)."""
    import ctypes
    from ctypes import wintypes

    handle = k32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_VM_READ, False, pid)
    if not handle:
        return "", ""
    try:
        exe_name = ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            exe_name = Path(buf.value).name.lower()

        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)

        class _PBI(ctypes.Structure):
            _fields_ = [
                ("Reserved1", ctypes.c_void_p),       # ExitStatus (+ pad)
                ("PebBaseAddress", ctypes.c_void_p),
                ("Reserved2", ctypes.c_void_p * 2),
                ("UniqueProcessId", ctypes.c_void_p),
                ("Reserved3", ctypes.c_void_p),
            ]

        ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        ]
        ntdll.NtQueryInformationProcess.restype = ctypes.c_long

        pbi = _PBI()
        status = ntdll.NtQueryInformationProcess(
            handle, 0, ctypes.byref(pbi), ctypes.sizeof(pbi), None)
        if status != 0 or not pbi.PebBaseAddress:
            return "", exe_name

        def _rpm(addr: int, length: int) -> bytes | None:
            out = (ctypes.c_char * length)()
            read = ctypes.c_size_t(0)
            ok = k32.ReadProcessMemory(
                handle, ctypes.c_void_p(addr), out, length, ctypes.byref(read))
            if not ok or read.value != length:
                return None
            return out.raw

        pp_raw = _rpm(pbi.PebBaseAddress + _PEB_PROCESS_PARAMETERS_OFFSET, 8)
        if not pp_raw:
            return "", exe_name
        params = int.from_bytes(pp_raw, "little")
        if not params:
            return "", exe_name

        us_raw = _rpm(params + _RTL_CURRENT_DIRECTORY_OFFSET, 16)
        if not us_raw:
            return "", exe_name
        length = int.from_bytes(us_raw[0:2], "little")
        ptr = int.from_bytes(
            us_raw[_UNICODE_STRING_BUFFER_OFFSET:_UNICODE_STRING_BUFFER_OFFSET + 8],
            "little")
        if not length or not ptr:
            return "", exe_name
        cwd_raw = _rpm(ptr, length)
        if not cwd_raw:
            return "", exe_name
        return cwd_raw.decode("utf-16-le", "ignore").rstrip("\\/"), exe_name
    finally:
        k32.CloseHandle(handle)


def _terminate_windows(k32, pid: int) -> bool:
    handle = k32.OpenProcess(_PROCESS_TERMINATE, False, pid)
    if not handle:
        return False
    try:
        return bool(k32.TerminateProcess(handle, 1))
    finally:
        k32.CloseHandle(handle)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def processes_with_cwd_under(
    root: str, *, exclude_pids: set[int] | None = None,
) -> list[dict]:
    """Find processes whose cwd is at or under ``root``.

    Returns a list of ``{"pid": int, "name": str}`` dicts.  Best-effort: any
    process that can't be opened or read is silently skipped, and the current
    process (plus ``exclude_pids``) is never reported.
    """
    excluded = {os.getpid()}
    if exclude_pids:
        excluded |= exclude_pids
    hits: list[dict] = []

    if platform.system() == "Windows":
        try:
            k32 = _win_kernel32()
        except OSError:
            return hits
        for pid in _win_enum_pids():
            if pid in excluded:
                continue
            try:
                cwd, name = _win_read_cwd(k32, pid)
            except OSError:
                continue
            if _is_under(cwd, root):
                hits.append({"pid": pid, "name": name})
    else:
        for pid, cwd, name in _iter_processes_posix():
            if pid in excluded:
                continue
            if _is_under(cwd, root):
                hits.append({"pid": pid, "name": name})
    return hits


def terminate_processes_under(
    root: str, *, exclude_pids: set[int] | None = None,
) -> list[dict]:
    """Terminate every process whose cwd is at or under ``root``.

    Returns the list of ``{"pid", "name", "killed": bool}`` that were targeted.
    Used by worktree cleanup to release directory locks before ``rmtree`` so a
    reaped worktree directory can actually be removed.
    """
    targets = processes_with_cwd_under(root, exclude_pids=exclude_pids)
    if not targets:
        return []

    if platform.system() == "Windows":
        try:
            k32 = _win_kernel32()
        except OSError:
            return [{**t, "killed": False} for t in targets]
        killer = lambda pid: _terminate_windows(k32, pid)  # noqa: E731
    else:
        killer = _terminate_posix

    results: list[dict] = []
    for t in targets:
        ok = False
        try:
            ok = killer(t["pid"])
        except OSError:
            ok = False
        results.append({**t, "killed": ok})
    return results
