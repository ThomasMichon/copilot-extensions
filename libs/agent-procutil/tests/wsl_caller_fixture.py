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


def main() -> None:
    config = json.loads(Path(sys.argv[1]).read_text())
    sys.path.insert(0, config["source"])
    from agent_procutil.wsl import run_windows_owned

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
