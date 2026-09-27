"""Local-only native OpenSSH probe with synthetic console-subsystem proxy."""

from __future__ import annotations

import asyncio
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import time


def console_window() -> int:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    return int(kernel.GetConsoleWindow() or 0)


def terminal_windows() -> dict[int, bool]:
    user = ctypes.WinDLL("user32", use_last_error=True)
    dwm = ctypes.WinDLL("dwmapi", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    dwm.DwmGetWindowAttribute.argtypes = [
        wintypes.HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    foreground = user.GetForegroundWindow()
    windows: dict[int, bool] = {}

    @callback_type
    def collect(window, _context):
        name = ctypes.create_unicode_buffer(256)
        user.GetClassNameW(window, name, len(name))
        if name.value not in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"):
            return True
        rectangle = wintypes.RECT()
        user.GetWindowRect(window, ctypes.byref(rectangle))
        cloaked = wintypes.DWORD()
        status = dwm.DwmGetWindowAttribute(
            window, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked),
        )
        if (
            user.IsWindowVisible(window)
            and rectangle.right > rectangle.left
            and rectangle.bottom > rectangle.top
            and status == 0
            and cloaked.value == 0
        ):
            windows[int(window)] = window == foreground
        return True

    user.EnumWindows(collect, 0)
    return windows


def serve_proxy(marker: Path) -> None:
    samples = []
    for delay in (0.0, 0.2, 0.4):
        time.sleep(delay)
        samples.append(console_window())
    marker.write_text(json.dumps(samples), encoding="utf-8")


async def drive(directory: Path) -> dict:
    from ssh_manager import SSHConfig
    from ssh_manager.process import run_process_cleanup, terminate_ssh_process_tree
    from ssh_manager.proxy import create_ssh_subprocess

    ssh = Path(os.environ["SSH_MANAGER_TEST_SSH"])
    empty_config = directory / "empty.config"
    empty_config.write_text("", encoding="utf-8")
    baseline = set(terminal_windows())
    observed: dict[int, bool] = {}
    stop = asyncio.Event()

    async def monitor():
        while not stop.is_set():
            for window, foreground in terminal_windows().items():
                if window not in baseline:
                    observed[window] = observed.get(window, False) or foreground
            await asyncio.sleep(0.01)

    watcher = asyncio.create_task(monitor())
    cycles = []
    try:
        for cycle in range(2):
            marker = directory / f"proxy-{cycle}.json"
            proxy_args = [
                sys._base_executable, str(Path(__file__).resolve()), "--proxy", str(marker),
            ]
            proxy_command = (
                shlex.join([
                    Path(proxy_args[0]).as_posix(), Path(proxy_args[1]).as_posix(),
                    "--proxy", marker.as_posix(),
                ])
                if os.environ["SSH_MANAGER_TEST_SSH_KIND"] == "git"
                else subprocess.list2cmdline(proxy_args)
            )
            config = SSHConfig(
                host_alias="local-proxy-test.invalid",
                user="probe",
                config_file=str(empty_config),
                proxy_command=proxy_command,
            )
            process = await create_ssh_subprocess(
                str(ssh), "-F", str(empty_config), "-T",
                "-o", "BatchMode=yes", "-o", "IdentityFile=none",
                "-o", "IdentityAgent=none", config.ssh_target,
                config=config,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _output, error = await asyncio.wait_for(process.communicate(), timeout=8)
            finally:
                await terminate_ssh_process_tree(process)
                await run_process_cleanup(process)
            if not marker.is_file():
                raise RuntimeError(
                    "The synthetic native SSH proxy did not execute: "
                    + error.decode(errors="replace")
                )
            cycles.append({
                "proxy_console_handles": json.loads(marker.read_text(encoding="utf-8")),
                "ssh_returncode": process.returncode,
            })
    finally:
        stop.set()
        await watcher
    return {
        "parent_console_handle": console_window(),
        "cycles": cycles,
        "new_visible_terminal_windows": len(observed),
        "new_terminal_took_foreground": any(observed.values()),
    }


if __name__ == "__main__":
    if sys.argv[1] == "--proxy":
        serve_proxy(Path(sys.argv[2]))
    else:
        print(json.dumps(asyncio.run(drive(Path(sys.argv[1])))))
