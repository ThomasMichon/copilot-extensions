"""Positive desktop witness for the explicitly admitted disposable broker lane."""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path


class DesktopWitness:
    def __init__(self, root: Path) -> None:
        from windows_broker_protocol_acceptance import admit

        admit(root, True)
        self.root = root
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.user.GetForegroundWindow.restype = wintypes.HWND
        self.user.IsWindowVisible.argtypes = [wintypes.HWND]
        self.user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self.user.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        self.callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        self.user.EnumWindows.argtypes = [self.callback, wintypes.LPARAM]
        self.stop_event = threading.Event()
        self.windows: set[int] = set()
        self.focus_changes: set[int] = set()
        self.error: Exception | None = None
        self.thread = None
        self.samples = 0

    def visible(self) -> set[int]:
        windows: set[int] = set()

        @self.callback
        def collect(window, _context):
            rectangle = wintypes.RECT()
            if self.user.IsWindowVisible(window) and self.user.GetWindowRect(
                window, ctypes.byref(rectangle),
            ) and rectangle.right > rectangle.left and rectangle.bottom > rectangle.top:
                windows.add(int(window))
            return True

        if not self.user.EnumWindows(collect, 0):
            raise ctypes.WinError(ctypes.get_last_error())
        return windows

    def qualify(self, pu) -> dict:
        """Observe this exact canary's window and foreground, then retire it."""
        baseline = self.visible()
        marker = self.root / "desktop-canary.json"
        program = (
            "import ctypes,json,time;from ctypes import wintypes;from pathlib import Path;"
            "u=ctypes.WinDLL('user32',use_last_error=True);"
            "u.CreateWindowExW.argtypes=[wintypes.DWORD,wintypes.LPCWSTR,wintypes.LPCWSTR,"
            "wintypes.DWORD,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,"
            "wintypes.HWND,wintypes.HMENU,wintypes.HINSTANCE,ctypes.c_void_p];"
            "u.CreateWindowExW.restype=wintypes.HWND;"
            "u.SetForegroundWindow.argtypes=[wintypes.HWND];"
            "w=u.CreateWindowExW(0,'STATIC','Owned broker desktop control',"
            "0x10000000,40,40,300,120,None,None,None,None);"
            "assert w;u.SetForegroundWindow(w);"
            f"Path({str(marker)!r}).write_text(json.dumps({{'window':int(w)}}));"
            "time.sleep(15)"
        )
        process, job = pu.spawn_sync_in_kill_on_close_job(
            [str(Path(sys.base_prefix) / "pythonw.exe"), "-I", "-c", program],
            require_job=True, allow_breakaway=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            # headless-guard: allow explicitly admitted, owned positive GUI canary.
            creationflags=0x08000000,
        )
        if job is None:
            process.kill()
            process.wait(timeout=5)
            raise RuntimeError("desktop canary native custody unavailable")
        window = None
        try:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                if marker.is_file():
                    window = json.loads(marker.read_text(encoding="utf-8"))["window"]
                    owner = wintypes.DWORD()
                    self.user.GetWindowThreadProcessId(window, ctypes.byref(owner))
                    if (
                        window not in baseline and window in self.visible()
                        and owner.value == process.pid
                        and self.user.GetForegroundWindow() == window
                    ):
                        break
                if process.poll() is not None:
                    raise RuntimeError("desktop canary exited before positive observation")
                time.sleep(0.01)
            else:
                raise RuntimeError("desktop window/foreground observer is unqualified")
        finally:
            job.terminate_and_wait(5)
            job.close()
            process.wait(timeout=5)
        deadline = time.monotonic() + 5
        while window in self.visible() or self.user.GetForegroundWindow() == window:
            if time.monotonic() >= deadline:
                raise RuntimeError("desktop canary retirement is unconfirmed")
            time.sleep(0.01)
        return {"owned_window_observed": True, "foreground_observed": True, "retired": True}

    def start(self) -> None:
        self.baseline = self.visible()
        self.foreground = int(self.user.GetForegroundWindow() or 0)
        if not self.foreground:
            raise RuntimeError("desktop foreground baseline unavailable")

        def sample():
            try:
                while not self.stop_event.is_set():
                    self.windows.update(self.visible() - self.baseline)
                    foreground = int(self.user.GetForegroundWindow() or 0)
                    if foreground != self.foreground:
                        self.focus_changes.add(foreground)
                    self.samples += 1
                    self.stop_event.wait(0.005)
            except Exception as error:
                self.error = error

        self.thread = threading.Thread(target=sample, name="broker-desktop-witness", daemon=True)
        self.thread.start()

    def finish(self) -> dict:
        self.stop_event.set()
        if self.thread is None:
            raise RuntimeError("desktop observation never started")
        self.thread.join(timeout=3)
        if self.thread.is_alive() or self.error is not None or not self.samples:
            raise RuntimeError("desktop observation did not complete")
        result = {
            "new_visible_windows": len(self.windows),
            "foreground_changes": len(self.focus_changes),
            "samples": self.samples,
        }
        if self.windows or self.focus_changes:
            raise RuntimeError("broker lifecycle changed the visible desktop: " + json.dumps(result))
        return result
