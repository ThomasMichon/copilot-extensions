"""Native WSL fixture: abrupt caller death must not depend on Python finally."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

_SETUP_OWNER = """
import ctypes,json,pathlib,sys
from ctypes import wintypes
sys.path.insert(0,sys.argv[1])
from agent_procutil import wsl
read_request=wsl._read_request
def record_then_read():
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    kernel.GetProcessTimes.restype=wintypes.BOOL
    times=[wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(kernel.GetCurrentProcess(),*[ctypes.byref(t) for t in times]):
        raise ctypes.WinError(ctypes.get_last_error())
    ready=pathlib.Path(sys.argv[2])
    staging=ready.with_suffix('.staging')
    staging.write_text(json.dumps({
        'pid':wsl.os.getpid(),'created':(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime,
    }))
    staging.replace(ready)
    return read_request()
wsl._read_request=record_then_read
wsl._windows_main()
"""


def _publish(path: Path, value: dict) -> None:
    staging = path.with_suffix(".staging")
    staging.write_text(json.dumps(value))
    staging.replace(path)


def keep_unrelated_fork_alive(gate: Path) -> None:
    ready = gate.with_name("fork-ready.json")
    stop = gate.with_name("fork-stop")
    if os.fork() == 0:
        try:
            # Keep this unrelated actor outside wsl.exe's departing caller session.
            os.setsid()
            null = os.open(os.devnull, os.O_RDWR)
            for descriptor in (0, 1, 2):
                os.dup2(null, descriptor)
            if null > 2:
                os.close(null)
            fields = Path("/proc/self/stat").read_text().rpartition(")")[2].split()
            _publish(ready, {"pid": os.getpid(), "birth": int(fields[19])})
            deadline = time.monotonic() + 30
            while not stop.exists() and time.monotonic() < deadline:
                time.sleep(.01)
        finally:
            os._exit(0)
    deadline = time.monotonic() + 5
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(.01)
    if not ready.exists():
        raise RuntimeError("Unrelated fork child did not establish its native identity")


def setup_interrupt(config: dict) -> None:
    from agent_procutil import wsl

    real_popen = subprocess.Popen
    gate = Path(config["gate"])
    original_owner_command = wsl._owner_command

    def command(python, package):
        original = original_owner_command(python, package)
        return [*original[:5], _SETUP_OWNER, package, config["ready"]]

    def launch(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        if args[0][0] != config["pythonw"]:
            return process
        stream = process.stdin

        def fileno():
            deadline = time.monotonic() + 12
            while not gate.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            if not gate.exists():
                raise RuntimeError("Setup interruption gate did not open")
            signal.raise_signal(signal.SIGINT)

        process.stdin = SimpleNamespace(fileno=fileno, close=stream.close)
        return process

    wsl._owner_command = command
    wsl.subprocess.Popen = launch
    retained = None
    try:
        wsl.run_windows_owned(
            config["argv"], windows_python=config["pythonw"], timeout=30,
        )
    except KeyboardInterrupt:
        retained = sys.exc_info()
    if retained is None or retained[2] is None:
        raise RuntimeError("Setup interruption was not retained by the live caller")
    _publish(gate.with_name("caught.json"), {"retained": True})
    deadline = time.monotonic() + 12
    while not gate.with_name("stop").exists() and time.monotonic() < deadline:
        time.sleep(.01)


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text())
    sys.path.insert(0, config["source"])
    from agent_procutil.wsl import run_windows_owned

    if config["case"] == "setup-sigint":
        setup_interrupt(config)
        return

    caller = os.fork() if config["case"] == "caller-death-with-fork" else 0
    if caller:
        _, status = os.waitpid(caller, 0)
        gate = Path(config["gate"])
        _publish(gate.with_name("caller-exited.json"), {
            "returncode": os.waitstatus_to_exitcode(status),
        })
        deadline = time.monotonic() + 30
        while not gate.with_name("fork-stop").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        return

    def interrupt() -> None:
        gate = Path(config["gate"])
        while not gate.exists():
            time.sleep(.01)
        if config["case"] == "caller-death-with-fork":
            keep_unrelated_fork_alive(gate)
        os.kill(os.getpid(), signal.SIGINT if config["case"] == "sigint" else signal.SIGKILL)

    threading.Thread(target=interrupt, daemon=True).start()
    try:
        result = run_windows_owned(
            config["argv"], windows_python=config["pythonw"],
            timeout=4 if config["case"] in {"timeout", "stdin-stall"} else 30,
            input_text="x" * 524288 if config["case"] == "stdin-stall" else None,
        )
    except subprocess.TimeoutExpired:
        print(json.dumps({"outcome": config["case"]}), flush=True)
    except KeyboardInterrupt:
        print(json.dumps({"outcome": "sigint"}), flush=True)
    else:
        raise RuntimeError(f"Fixture command unexpectedly completed: {result.returncode}")


if __name__ == "__main__":
    main()
