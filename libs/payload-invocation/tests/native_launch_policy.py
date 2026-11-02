"""Exercise generated first-use launchers without pytest's process adapters."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import threading
import traceback
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job  # noqa: E402

user = ctypes.WinDLL("user32", use_last_error=True)
kernel = ctypes.WinDLL("kernel32", use_last_error=True)
kernel.GetConsoleWindow.restype = wintypes.HWND
user.GetForegroundWindow.restype = wintypes.HWND
user.IsWindowVisible.argtypes = [wintypes.HWND]
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
stop = threading.Event()
ready = threading.Event()
new_visible: set[int] = set()
focus_changes: list[int] = []
errors: list[str] = []


def observe() -> None:
    baseline = None
    previous = None
    try:
        while not stop.is_set():
            visible: set[int] = set()

            @callback_type
            def inspect(handle, _param):
                if user.IsWindowVisible(handle):
                    visible.add(int(handle))
                return True

            if not user.EnumWindows(inspect, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            foreground = user.GetForegroundWindow()
            if baseline is None:
                assert foreground, "interactive desktop observation unavailable"
                baseline = visible
            else:
                new_visible.update(visible - baseline)
                if foreground != previous:
                    focus_changes.append(1)
            previous = foreground
            ready.set()
            stop.wait(0.005)
    except Exception as error:
        errors.append(str(error))
        ready.set()


def invoke(command: list[str], environment: dict[str, str], timeout: float):
    process, job = spawn_sync_in_kill_on_close_job(
        command,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        require_job=True,
        allow_breakaway=False,
        **no_window_kwargs(),
    )
    assert job is not None
    timed_out = False
    try:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            job.terminate_and_wait(5)
            stdout, stderr = process.communicate(timeout=5)
        return process.returncode, stdout, stderr, timed_out
    finally:
        if job.handle:
            job.terminate_and_wait(5)


def main() -> int:
    request = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    workspace = Path(request["workspace"])
    report = {
        "cycles": 0,
        "timeout_job_empty": False,
        "failure": "",
        "child_policies": [],
    }
    observer = threading.Thread(target=observe)
    observer.start()
    try:
        assert not kernel.GetConsoleWindow(), "native driver must have no console"
        assert ready.wait(5) and not errors, errors
        code, stdout, stderr, timed_out = invoke(
            [
                request["host"],
                "-NoLogo",
                "-NoProfile",
                "-Command",
                "Get-ExecutionPolicy",
            ],
            dict(os.environ),
            10,
        )
        assert not timed_out and code == 0, (stdout, stderr)
        report["inherited_policy"] = stdout.strip()
        for cycle in range(3):
            home = workspace / f"home-{cycle}"
            home.mkdir()
            environment = {
                **os.environ,
                "HOME": str(home),
                "USERPROFILE": str(home),
                "LOCALAPPDATA": str(home / "AppData/Local"),
                "APPDATA": str(home / "AppData/Roaming"),
                "LAUNCH_POLICY_PYTHON": request["python"],
                "LAUNCH_POLICY_STALL": "1" if cycle == 2 else "0",
            }
            code, stdout, stderr, timed_out = invoke(
                [os.environ["COMSPEC"], "/d", "/c", request["launcher"], "--help"],
                environment,
                10,
            )
            assert not stdout, stdout
            assert "recorded installer output" in stderr, stderr
            assert "recorded installer diagnostic" in stderr, stderr
            record = json.loads(
                (home / "installer-action.json").read_text(encoding="utf-8-sig")
            )
            assert record["action"] == request["action"], record
            report["child_policies"].append(record["policy"])
            if cycle == 2:
                assert timed_out and (home / "grandchild.pid").is_file(), (code, stderr)
                report["timeout_job_empty"] = True
            else:
                assert not timed_out and code == 37, (code, stderr)
                report["cycles"] += 1
    except BaseException:
        report["failure"] = traceback.format_exc()
    finally:
        stop.set()
        observer.join(5)
        report.update(
            new_visible_windows=len(new_visible),
            foreground_transitions=len(focus_changes),
            observer_errors=errors,
        )
        Path(request["report"]).write_text(json.dumps(report), encoding="utf-8")
    return int(
        bool(
            report["failure"]
            or new_visible
            or focus_changes
            or errors
            or observer.is_alive()
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
