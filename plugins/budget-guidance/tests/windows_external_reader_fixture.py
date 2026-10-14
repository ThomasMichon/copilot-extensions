"""Finite native fixture for the real external-reader Windows launch path."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _record(path: Path) -> None:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetConsoleWindow.restype = wintypes.HWND
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    window = kernel.GetConsoleWindow()
    creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    assert kernel.GetProcessTimes(
        kernel.GetCurrentProcess(), ctypes.byref(creation), ctypes.byref(exit_time),
        ctypes.byref(kernel_time), ctypes.byref(user_time),
    )
    record = {
        "pid": os.getpid(), "birth": (creation.dwHighDateTime << 32) | creation.dwLowDateTime,
        "console": int(window or 0), "visible": bool(window and user.IsWindowVisible(window)),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    role, root_text, console_python = sys.argv[1:4]
    root = Path(root_text)
    if role == "descendant":
        _record(root / "descendant.json")
        time.sleep(15)
        return
    if role == "reader":
        from agent_procutil import no_window_kwargs

        _record(root / "reader.json")
        subprocess.Popen(
            [console_python, __file__, "descendant", str(root), console_python],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        time.sleep(0.6)
        if (root / "timeout").exists():
            time.sleep(15)
        print(json.dumps({
            "schema": "copilot-extensions.budget-reading", "version": 1,
            "source": "native-fixture",
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "freshness_seconds": 60, "availability": "available",
            "consumption": 7,
        }))
        return

    from decimal import Decimal
    from budget_guidance.external_reader import CommandReader, acquire_config
    from budget_guidance.models import parse_config

    results = []
    for cycle in range(3):
        current = root / str(cycle)
        current.mkdir()
        if cycle == 2:
            (current / "timeout").touch()
        config = parse_config({
            "schema": "copilot-extensions.budget-guidance-config", "version": Decimal(1),
            "adapters": [{
                "type": "external-command", "id": "native", "authority": Decimal(1),
                "source": "native-fixture",
            }],
        })
        started_at = time.monotonic()
        acquired = acquire_config(config, {"native": CommandReader(
            (console_python, __file__, "reader", str(current), console_python),
            current, timeout_seconds=1.5,
        )})
        reading = acquired.adapters[0].reading
        results.append({
            "availability": reading.availability, "error": reading.error,
            "consumption": str(reading.consumption) if reading.consumption is not None else None,
            "elapsed_seconds": time.monotonic() - started_at,
        })
    (root / "result.json").write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main()
