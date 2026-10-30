"""Read-only original-hook process metadata; importable without the runtime."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sys
from pathlib import Path
from uuid import UUID


MAX_ANCESTORS = 16
_EPOCH_FIELDS = ("platform", "environment_id", "boot_id")


def _text(path: str, limit: int = 4096) -> str:
    with open(path, encoding="ascii") as stream:
        value = stream.read(limit + 1)
    if len(value) > limit:
        raise ValueError("Native source metadata exceeds its budget")
    return value.strip()


def epoch() -> dict[str, str]:
    """Read the native execution epoch, refusing unsupported environments."""
    if sys.platform == "win32":
        return _windows_epoch()
    if not sys.platform.startswith("linux"):
        raise ValueError("Native lifecycle source is unqualified on this platform")
    if any(Path(marker).exists() for marker in ("/.dockerenv", "/run/.containerenv")):
        raise ValueError("Container lifecycle sources are unqualified")
    cgroup = _text("/proc/1/cgroup")
    if any(tag in cgroup for tag in ("/docker", "/kubepods", "containerd", "/lxc", "libpod")):
        raise ValueError("Container lifecycle sources are unqualified")
    platform = "wsl" if "microsoft" in _text("/proc/sys/kernel/osrelease").lower() else "linux"
    boot = str(UUID(_text("/proc/sys/kernel/random/boot_id")))
    environment = _text("/etc/machine-id")
    if len(environment) != 32 or any(c not in "0123456789abcdef" for c in environment):
        raise ValueError("Invalid native environment identity")
    if platform == "wsl":
        boot += ":" + process(1)["start_time"]
    return {"platform": platform, "environment_id": environment, "boot_id": boot}


def process(pid: int) -> dict:
    """Read one PID/birth/parent observation, never its command line or environment."""
    if sys.platform == "win32":
        return _windows_process(pid)
    fields = _text(f"/proc/{pid}/stat").rsplit(")", 1)[1].split()
    birth = fields[19]
    parent = int(fields[1])
    if not birth.isdecimal() or len(birth) > 32 or parent < 0:
        raise ValueError("Invalid native process identity")
    return {"pid": pid, "start_time": birth, "parent_pid": parent}


def capture() -> dict:
    """Capture the original hook ancestry before crossing any resident boundary."""
    try:
        observed_epoch = epoch()
        ancestors = []
        child = process(os.getpid())
        pid = child["parent_pid"]
        seen = set()
        while pid > 0 and pid not in seen and len(ancestors) < MAX_ANCESTORS:
            seen.add(pid)
            try:
                current = process(pid)
            except OSError:
                # Windows ancestors can cross into a protected service account.
                # Only the verified prefix is retained; it must still contain
                # the exact session's open-hold owner to qualify at consumption.
                if sys.platform == "win32" and ancestors:
                    break
                raise
            fresh_child = process(child["pid"])
            if (
                fresh_child["start_time"] != child["start_time"]
                or fresh_child["parent_pid"] != pid
                or process(pid)["start_time"] != current["start_time"]
                or (
                    sys.platform == "win32"
                    and int(current["start_time"]) > int(child["start_time"])
                )
            ):
                raise ValueError("Original hook ancestry changed during capture")
            ancestors.append({"pid": pid, "start_time": current["start_time"]})
            child = current
            pid = current["parent_pid"]
        if not ancestors:
            raise ValueError("Original hook ancestry is unavailable")
        return {"version": 1, "qualified": True, **observed_epoch, "ancestors": ancestors}
    except (OSError, ValueError, IndexError, AttributeError) as exc:
        logging.getLogger(__name__).warning("Native lifecycle source unavailable: %s", exc)
        return {"version": 1, "qualified": False, "reason": "native-source-unavailable"}


def identity(session_id: str, pid: int, start_time: str, observed_epoch: dict) -> str:
    """Bind an opaque source to one native UUID and physical execution epoch."""
    if (
        str(UUID(session_id)) != session_id or type(pid) is not int or pid <= 0
        or not isinstance(start_time, str) or not start_time.isdecimal()
        or not start_time.isascii() or len(start_time) > 32
    ):
        raise ValueError("Invalid native lifecycle process identity")
    value = [observed_epoch[name] for name in _EPOCH_FIELDS]
    value.extend((session_id, pid, start_time))
    digest = hashlib.sha256(json.dumps(value, separators=(",", ":")).encode("ascii")).hexdigest()
    return "native-source-v1:" + digest


def candidates(session_id: str, witness: object) -> list[tuple[str, int, str]]:
    """Derive only captured original sources, never the resident's ancestry."""
    if (
        not isinstance(witness, dict) or type(witness.get("version")) is not int
        or witness["version"] != 1 or witness.get("qualified") is not True
        or {name: witness.get(name) for name in _EPOCH_FIELDS} != epoch()
    ):
        raise ValueError("Original native source epoch is unavailable or changed")
    ancestors = witness.get("ancestors")
    if not isinstance(ancestors, list) or not 1 <= len(ancestors) <= MAX_ANCESTORS:
        raise ValueError("Invalid original native source ancestry")
    result = []
    for item in ancestors:
        if not isinstance(item, dict) or set(item) != {"pid", "start_time"}:
            raise ValueError("Invalid original native source process")
        token = identity(session_id, item["pid"], item["start_time"], witness)
        result.append((token, item["pid"], item["start_time"]))
    if len(set(result)) != len(result):
        raise ValueError("Repeated original native source process")
    return result


def _windows_epoch() -> dict[str, str]:
    """Machine/boot identity, without guessing boot time from the wall clock."""
    import ctypes
    from ctypes import wintypes
    import winreg

    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control") as key:
        try:
            winreg.QueryValueEx(key, "ContainerType")
        except FileNotFoundError:
            pass
        else:
            raise ValueError("Container lifecycle sources are unqualified")
    with winreg.OpenKey(
        winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
        0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
    ) as key:
        machine, kind = winreg.QueryValueEx(key, "MachineGuid")
    if kind != winreg.REG_SZ or not isinstance(machine, str):
        raise ValueError("Invalid native environment identity")
    environment = UUID(machine).hex

    class BootEnvironment(ctypes.Structure):
        _fields_ = [
            ("identifier", ctypes.c_ubyte * 16),
            ("firmware", wintypes.ULONG),
            ("flags", ctypes.c_ulonglong),
        ]

    query = ctypes.WinDLL("ntdll", use_last_error=True).NtQuerySystemInformation
    query.argtypes = [
        ctypes.c_int, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ]
    query.restype = ctypes.c_long
    value = BootEnvironment()
    written = wintypes.ULONG()
    # SystemBootEnvironmentInformation; unavailable or changed ABI is refused.
    status = query(90, ctypes.byref(value), ctypes.sizeof(value), ctypes.byref(written))
    if status != 0 or written.value != ctypes.sizeof(value):
        raise OSError("Native Windows boot identity is unavailable")
    boot = UUID(bytes_le=bytes(value.identifier))
    if not boot.int:
        raise ValueError("Invalid native Windows boot identity")
    return {"platform": "windows", "environment_id": environment, "boot_id": str(boot)}


def _windows_process(pid: int) -> dict:
    # This file is also published alone beside the hook client. It cannot
    # import runtime helpers; keep the metadata-only native query self-contained.
    import ctypes
    from ctypes import wintypes

    class BasicInformation(ctypes.Structure):
        _fields_ = [
            ("exit_status", ctypes.c_long), ("peb", ctypes.c_void_p),
            ("affinity", ctypes.c_size_t), ("priority", ctypes.c_long),
            ("pid", ctypes.c_size_t), ("parent_pid", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME),
    ] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    query = ctypes.WinDLL("ntdll", use_last_error=True).NtQueryInformationProcess
    query.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
        wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
    ]
    query.restype = ctypes.c_long
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info, written = BasicInformation(), wintypes.ULONG()
        if (
            query(handle, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(written)) != 0
            or written.value != ctypes.sizeof(info) or info.pid != pid
            or info.exit_status != 259  # STILL_ACTIVE
        ):
            raise OSError("Native Windows process identity is unavailable")
        birth, ended, system, user = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle, ctypes.byref(birth), ctypes.byref(ended),
            ctypes.byref(system), ctypes.byref(user),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return {
            "pid": pid,
            "start_time": str((birth.dwHighDateTime << 32) | birth.dwLowDateTime),
            "parent_pid": info.parent_pid,
        }
    finally:
        kernel.CloseHandle(handle)
