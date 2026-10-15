"""Finite native fixture for the real external-reader Windows launch path."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import shutil
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
        "parent": os.getppid(),
        "recorded_monotonic": time.monotonic(),
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(record), encoding="utf-8")
    temporary.replace(path)


def main() -> None:
    role, root_text, console_python = sys.argv[1:4]
    root = Path(root_text)
    if role == "cue-host":
        _cue_cycles(root, console_python)
        return
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
    import budget_guidance.external_reader as reader_module
    from budget_guidance.external_reader import CommandReader, acquire_config
    from budget_guidance.models import parse_config

    results = []
    spawn = reader_module.spawn_sync_in_kill_on_close_job
    read_output = reader_module._read_output
    observations = {}

    def observed_spawn(*args, **kwargs):
        observations["spawn_started"] = time.monotonic()
        process, job = spawn(*args, **kwargs)
        observations["spawn_finished"] = time.monotonic()
        poll = process.poll

        def observed_poll():
            code = poll()
            observations["last_poll_time"] = time.monotonic()
            observations["last_poll_code"] = code
            return code

        process.poll = observed_poll
        return process, job

    def observed_output(*args):
        observations["output_started"] = time.monotonic()
        read_output(*args)
        observations["output_finished"] = time.monotonic()

    reader_module.spawn_sync_in_kill_on_close_job = observed_spawn
    reader_module._read_output = observed_output
    for cycle in range(3):
        observations = {}
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
            "acquisition_started": started_at,
            "observations": dict(observations),
            "process_records": {
                role: json.loads((current / f"{role}.json").read_text())
                if (current / f"{role}.json").exists() else None
                for role in ("reader", "descendant")
            },
        })
    (root / "result.json").write_text(json.dumps(results), encoding="utf-8")


def _cue_cycles(root: Path, console_python: str) -> None:
    from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

    shell = shutil.which("pwsh") or shutil.which("powershell.exe")
    assert shell, "native session-cue witness requires PowerShell"
    plugin = Path(__file__).resolve().parents[1]
    results = []
    for cycle in range(3):
        current = root / str(cycle)
        scripts = current / "payload" / "scripts"
        scripts.mkdir(parents=True)
        home = current / "home"
        home.mkdir()
        shutil.copy2(plugin / "scripts" / "write-session-guidance.ps1", scripts)
        actual_writer = plugin / "scripts" / "write_session_guidance.py"
        # Pin the actual console child before delegating to the unchanged writer.
        # The timeout fixture replaces only its workload, not wrapper flags/limits.
        (scripts / "write_session_guidance.py").write_text(
            "import sys,time,runpy\n"
            f"sys.path.insert(0, {str(Path(__file__).parent)!r})\n"
            "from windows_external_reader_fixture import _record\n"
            f"from pathlib import Path\n_record(Path({str(current / 'descendant.json')!r}))\n"
            + ("time.sleep(20)\n" if cycle == 2 else
               f"time.sleep(0.6)\nrunpy.run_path({str(actual_writer)!r}, run_name='__main__')\n"),
            encoding="utf-8",
        )
        path_literal = str(current / "reader.json").replace("'", "''")
        shim = current / "observe.ps1"
        shim.write_text(
            "$p = [Diagnostics.Process]::GetCurrentProcess()\n"
            "$record = @{pid=$PID; birth=$p.StartTime.ToFileTimeUtc(); "
            "visible=($p.MainWindowHandle -ne [IntPtr]::Zero)} | ConvertTo-Json -Compress\n"
            f"[IO.File]::WriteAllText('{path_literal}.tmp', $record)\n"
            f"[IO.File]::Move('{path_literal}.tmp', '{path_literal}')\n"
            "& (Join-Path $env:COPILOT_PLUGIN_ROOT 'scripts/write-session-guidance.ps1')\n",
            encoding="utf-8",
        )
        capture = datetime.now(timezone.utc)
        config = current / "budget.json"
        config.write_text(json.dumps({
            "schema": "copilot-extensions.budget-guidance-config", "version": 1,
            "adapters": [{
                "type": "static", "id": "native", "authority": 1,
                "reading": {
                    "schema": "copilot-extensions.budget-reading", "version": 1,
                    "source": "native-session-cue", "captured_at": capture.isoformat(),
                    "freshness_seconds": 60, "availability": "available",
                    "allowance": 100, "consumption": 7 + cycle,
                    "reset_at": (capture + timedelta(days=2)).isoformat(),
                },
            }],
        }), encoding="utf-8")
        env = {
            **os.environ, "HOME": str(home), "USERPROFILE": str(home),
            "COPILOT_PLUGIN_ROOT": str(scripts.parent), "BUDGET_GUIDANCE_CONFIG": str(config),
            "PATH": str(Path(console_python).parent) + os.pathsep + os.environ.get("PATH", ""),
        }
        process, job = spawn_sync_in_kill_on_close_job(
            [shell, "-NoProfile", "-File", str(shim)], cwd=current, env=env,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            require_job=True, allow_breakaway=False, **no_window_kwargs(),
        )
        assert job is not None
        started_at = time.monotonic()
        try:
            stdout, stderr = process.communicate(json.dumps({
                "sessionId": "native-cue", "cwd": str(current),
                "source": "startup" if cycle == 0 else "resume",
            }).encode(), timeout=12)
            target = home / ".copilot/session-state/native-cue/instructions/budget-guidance/session-guidance.instructions.md"
            data = json.loads(target.read_text().split("\n", 1)[1]) if target.exists() else None
            results.append({
                "availability": data["availability"] if data else None,
                "consumption": data["fields"]["consumption"]["selected"]["value"] if data else None,
                "stdout": stdout.decode(), "stderr": stderr.decode(),
                "returncode": process.returncode,
                "elapsed_seconds": time.monotonic() - started_at,
            })
        finally:
            job.terminate_and_wait(5)
            process.wait(timeout=5)
    (root / "result.json").write_text(json.dumps(results), encoding="utf-8")


if __name__ == "__main__":
    main()
