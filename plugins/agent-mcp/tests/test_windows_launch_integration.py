"""Live Windows regression for the headless-launch fixes in this PR.

Per ``docs/patterns/windows-background-process-launch.md`` § Review and
validation: a mocked ``creationflags`` assertion proves wiring, not
behavior. This exercises the real ``CommandInjector`` auth-mint spawn (the
most representative of the four sites this PR touched -- a real console
child with real, caller-influenced args) from the real pytest parent across
several live cycles, observing actual Win32 process/foreground state rather
than any captured kwargs.

Windows-only; skipped everywhere else. Deliberately excluded from the fast
required lane (it needs a real Windows host and takes real wall-clock time
for repeated cycles) -- run it explicitly on a Windows box before/alongside
publishing a launch-path change.
"""

from __future__ import annotations

import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

from agent_mcp.auth import build_injector
from agent_mcp.config import parse_config


def _cfg(auth):
    doc = {"server": {"type": "stdio", "command": "npx"}, "auth": auth}
    return parse_config(doc)


class _ProcessEntry(ctypes.Structure if sys.platform == "win32" else object):
    if sys.platform == "win32":
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]


def _process_snapshot(kernel32) -> dict[int, str]:
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return {}
    found: dict[int, str] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            found[int(entry.th32ProcessID)] = entry.szExeFile
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _foreground_state(user32) -> tuple[int, str]:
    hwnd = int(user32.GetForegroundWindow())
    length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, title, length + 1)
    return hwnd, title.value


async def test_command_injector_mint_spawn_never_surfaces_a_window():
    # Two real cycles: a single-run flake can't be ruled out, two can.
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    cfg = _cfg({
        "kind": "command",
        "command": [sys.executable, "-c", "print('token=abc')"],
        "target_env": "API_KEY",
    })
    inj = build_injector(cfg)

    baseline_processes = _process_snapshot(kernel32)
    baseline_foreground = _foreground_state(user32)
    new_consoles: set[int] = set()
    suspicious_titles: list[str] = []
    results: list[dict] = []

    def run_cycles() -> None:
        import asyncio

        async def _cycles():
            for _ in range(2):
                inj._cached = None  # force a fresh spawn each cycle
                results.append(await inj.child_env())

        asyncio.run(_cycles())

    probe = threading.Thread(target=run_cycles)
    probe.start()
    while probe.is_alive():
        new_consoles.update(
            pid
            for pid, name in _process_snapshot(kernel32).items()
            if pid not in baseline_processes and name.lower() == "openconsole.exe"
        )
        state = _foreground_state(user32)
        if state != baseline_foreground and (
            "python" in state[1].lower() or "cmd.exe" in state[1].lower()
        ):
            suspicious_titles.append(state[1])
        time.sleep(0.003)
    probe.join()

    assert results == [{"API_KEY": "abc"}, {"API_KEY": "abc"}]
    assert new_consoles == set()
    assert suspicious_titles == []


async def test_command_injector_timeout_reaps_the_child():
    # Force the timeout path and prove the spawned tree fully exits -- a
    # headless child is easy to leak silently since there's no window to
    # notice still running.
    cfg = _cfg({
        "kind": "command",
        "command": [sys.executable, "-c", "import time; time.sleep(30)"],
        "target_env": "API_KEY",
    })
    inj = build_injector(cfg)
    inj._timeout = 0.5

    from agent_mcp.auth import injectors as injectors_module

    live_procs: list = []
    orig_term = injectors_module._terminate_proc

    async def spy(proc):
        if proc is not None:
            live_procs.append(proc)
        await orig_term(proc)

    injectors_module._terminate_proc = spy
    try:
        assert await inj.child_env() == {}  # timed out -> no token
    finally:
        injectors_module._terminate_proc = orig_term

    assert live_procs, "expected the timed-out child to be captured for reaping"
    proc = live_procs[0]
    # Give the OS a moment to finish tearing the process down, then confirm
    # it is actually gone (not merely signaled).
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    deadline = time.monotonic() + 5.0
    exited = False
    while time.monotonic() < deadline:
        snapshot = _process_snapshot(kernel32)
        if proc.pid not in snapshot:
            exited = True
            break
        time.sleep(0.05)
    assert exited, f"pid {proc.pid} is still present in the process snapshot"
