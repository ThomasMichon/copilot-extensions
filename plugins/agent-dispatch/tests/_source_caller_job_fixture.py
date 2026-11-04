"""One finite installer caller with nested Jobs and an admitted bounded control."""

from __future__ import annotations

from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys

from agent_procutil import spawn_sync_in_kill_on_close_job
from agent_procutil._daemon_instance_identity import birth_identity
from agent_procutil.daemon_instances import DaemonInstanceSpec, spawn_instance


def main() -> int:
    request = json.loads(sys.argv[1])
    root = Path(request["root"])
    bounded = spawn_instance(
        [sys.executable, "-I", "-B", "-c", "import time; time.sleep(300)"],
        instance=DaemonInstanceSpec(
            "dispatch-caller-bounded-control", root, Path(__file__).resolve(),
            purpose="unattended-test", lifecycle="finite-worker",
        ),
        cwd=root, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    pending = {
        "caller_pid": os.getpid(),
        "bounded_pid": bounded.pid, "bounded_birth": birth_identity(bounded.pid),
        "bounded_instance": asdict(bounded.instance_receipt),
    }
    pending["bounded_instance"]["registry_root"] = str(
        pending["bounded_instance"]["registry_root"],
    )
    Path(request["pending"]).write_text(json.dumps(pending), encoding="utf-8")
    installer = None
    job = None
    environment = dict(os.environ)
    environment.pop("__PYVENV_LAUNCHER__", None)
    try:
        installer, job = spawn_sync_in_kill_on_close_job(
            request["command"], cwd=root, env=environment,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, require_job=True, allow_breakaway=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        stdout, stderr = installer.communicate(timeout=300)
        Path(request["stdout"]).write_text(stdout, encoding="utf-8")
        Path(request["stderr"]).write_text(stderr, encoding="utf-8")
        pending["installer_exit"] = installer.returncode
    finally:
        if installer is not None and installer.poll() is None:
            installer.kill()
            installer.wait(timeout=5)
        if job is not None:
            job.close()
            pending["inner_job_closed"] = True
        Path(request["result"]).write_text(json.dumps(pending), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
