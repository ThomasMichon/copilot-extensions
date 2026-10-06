"""Live Windows regression for agent-mcp's headless-launch primitives.

Per ``docs/patterns/windows-background-process-launch.md`` § Review and
validation: a mocked ``creationflags`` assertion proves wiring, not
behavior, and a genuine check needs a real console child launched from a
real windowless parent. This launches a real ``pythonw.exe`` parent process
that itself runs the ``CommandInjector`` auth-mint spawn (a real console
child with real, caller-influenced args) across two cycles, observing
actual Win32 window/foreground state from the pytest side -- not just
captured kwargs, and not from a console-attached thread inside pytest's own
process, which cannot distinguish "flag applied" from "flag absent" when
the parent already owns a console.

Windows-only; skipped everywhere else. Deliberately excluded from the fast
required lane (it needs a real Windows host and takes real wall-clock time
for repeated cycles) -- run it explicitly on a Windows box alongside a
Windows launch-path change.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

_SURFACED_PROCESS_NAMES = {
    "python.exe", "pythonw.exe", "powershell.exe", "conhost.exe",
    "openconsole.exe", "windowsterminal.exe", "cmd.exe",
}

# The child script run by the real `pythonw.exe` parent below: constructs the
# same `CommandInjector` auth-mint config this PR changed and runs it for two
# real cycles, exactly as `agent_mcp.auth.injectors` does in production.
_CHILD_SCRIPT = """
import asyncio, json, sys
sys.path.insert(0, sys.argv[2])
from agent_mcp.auth import build_injector
from agent_mcp.config import parse_config

cfg = parse_config({
    "server": {"type": "stdio", "command": "npx"},
    "auth": {
        "kind": "command",
        "command": [sys.executable.replace("pythonw.exe", "python.exe"),
                    "-c", "print('token=abc')"],
        "target_env": "API_KEY",
    },
})
inj = build_injector(cfg)

async def cycles():
    out = []
    for _ in range(2):
        inj._cached = None
        out.append(await inj.child_env())
    return out

results = asyncio.run(cycles())
with open(sys.argv[1], "w", encoding="utf-8") as fh:
    json.dump(results, fh)
"""


def _configure_win32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    return user32, kernel32, callback_type


def _visible_windows(user32, callback_type):
    windows = set()

    @callback_type
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            windows.add(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return windows


def _process_name(user32, kernel32, hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)
    if not handle:
        return ""
    try:
        length = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(length.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            return Path(name.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


def test_command_injector_mint_spawn_never_surfaces_a_window(tmp_path):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        pytest.skip("pythonw.exe is unavailable on this interpreter")

    repo_src = str(Path(__file__).resolve().parents[1] / "src")
    child = tmp_path / "windowless_child.py"
    child.write_text(_CHILD_SCRIPT, encoding="utf-8")
    output_path = tmp_path / "result.json"

    user32, kernel32, callback_type = _configure_win32()
    baseline = _visible_windows(user32, callback_type)
    foreground = user32.GetForegroundWindow()
    surfaced: set[int] = set()
    focus_changes: set[int] = set()

    with subprocess.Popen(
        [str(pythonw), "-I", str(child), str(output_path), repo_src],
    ) as proc:
        deadline = time.monotonic() + 25
        while proc.poll() is None and time.monotonic() < deadline:
            surfaced.update(
                hwnd for hwnd in _visible_windows(user32, callback_type) - baseline
                if _process_name(user32, kernel32, hwnd) in _SURFACED_PROCESS_NAMES
            )
            current = user32.GetForegroundWindow()
            if (
                current != foreground and current not in baseline
                and _process_name(user32, kernel32, current) in _SURFACED_PROCESS_NAMES
            ):
                focus_changes.add(current)
            time.sleep(0.02)
        if proc.poll() is None:
            proc.kill()
            pytest.fail("windowless child spawn exceeded its deadline")
        returncode = proc.returncode

    assert returncode == 0
    results = json.loads(output_path.read_text(encoding="utf-8"))
    assert results == [{"API_KEY": "abc"}, {"API_KEY": "abc"}]
    assert surfaced == set()
    assert focus_changes == set()


async def test_command_injector_timeout_reaps_the_direct_child():
    # Force the timeout path and prove the spawned child process itself
    # fully exits, not just gets signaled. `CommandInjector._run_command`'s
    # timeout path reaps only the direct child (`_terminate_proc`, not the
    # tree-aware `_terminate_tree` other injectors use for a known
    # multi-process wrapper) -- this test's scope matches that: it verifies
    # the one process it spawns, not a descendant tree the auth-mint path
    # does not claim to manage.
    from agent_mcp.auth import build_injector
    from agent_mcp.auth import injectors as injectors_module
    from agent_mcp.config import parse_config

    cfg = parse_config({
        "server": {"type": "stdio", "command": "npx"},
        "auth": {
            "kind": "command",
            "command": [sys.executable, "-c", "import time; time.sleep(30)"],
            "target_env": "API_KEY",
        },
    })
    inj = build_injector(cfg)
    inj._timeout = 0.5

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

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    still_active = 259  # STILL_ACTIVE

    def has_exited() -> bool:
        handle = kernel32.OpenProcess(0x0400, False, proc.pid)  # PROCESS_QUERY_INFORMATION
        if not handle:
            return True  # can't even open it -> already gone
        try:
            code = wintypes.DWORD()
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value != still_active
        finally:
            kernel32.CloseHandle(handle)

    deadline = time.monotonic() + 5.0
    exited = False
    while time.monotonic() < deadline:
        if has_exited():
            exited = True
            break
        time.sleep(0.05)
    assert exited, f"pid {proc.pid} is still running (not STILL_ACTIVE-cleared)"
