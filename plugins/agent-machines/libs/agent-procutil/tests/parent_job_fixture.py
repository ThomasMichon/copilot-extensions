"""Disposable native Windows fixture; no installed runtime or shell admission."""

from __future__ import annotations

from ctypes import wintypes
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import _windows_birth
from agent_procutil._windows_caller_owned import _api, spawn
from agent_procutil._windows_parent_job import transfer_lifetime_to_parent


TARGET = """
import sys
sys.path.insert(0, sys.argv[1])
import json, subprocess, time
from agent_procutil import no_window_kwargs
from agent_procutil._daemon_instance_identity import _windows_birth
child = subprocess.Popen(
    [sys.executable, '-I', '-S', '-B', '-c', 'import time;time.sleep(30)'],
    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    **no_window_kwargs(),
)
print(json.dumps({'pid': child.pid, 'birth': _windows_birth(int(child._handle))}), flush=True)
time.sleep(30)
"""


def main() -> None:
    mode, report_name = sys.argv[1:3]
    report = Path(report_name)
    kernel = _api()
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    current = kernel.GetCurrentProcess()
    if mode.startswith("worker"):
        parent_pid, birth = int(sys.argv[3]), sys.argv[4]
        try:
            receipt = transfer_lifetime_to_parent(parent_pid=parent_pid, parent_birth=birth)
        except ProcessLookupError:
            if mode != "worker-refuse":
                raise
            report.write_text("refused", encoding="ascii")
            return
        process, job = spawn(
            [
                sys.executable, "-I", "-S", "-B", "-c", TARGET,
                str(Path(__file__).resolve().parents[1] / "src"),
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        try:
            descendant = json.loads(process.stdout.readline(1024).decode("utf-8"))
            data = {
                "transfer": asdict(receipt),
                "target": {"pid": process.pid, "birth": _windows_birth(process._handle)},
                "descendant": descendant,
            }
            pending = report.with_suffix(".pending")
            pending.write_text(json.dumps(data), encoding="utf-8")
            os.replace(pending, report)
            time.sleep(20)
            raise RuntimeError("parent failed to retire the disposable fixture")
        finally:
            job.terminate_and_wait(2)
            process.wait(2)
            process.stdout.close()
            process.close()
        return
    birth = _windows_birth(current)
    if mode == "refuse":
        birth = "windows:1"
    source = str(Path(__file__).resolve().parents[1] / "src")
    bootstrap = "import sys;sys.path.insert(0,sys.argv.pop(1));" + (
        "import runpy;runpy.run_path(sys.argv.pop(1),run_name='__main__')"
    )
    worker = subprocess.Popen(
        [
            getattr(sys, "_base_executable", sys.executable),
            "-I", "-S", "-B", "-c", bootstrap, source, __file__,
            "worker-refuse" if mode == "refuse" else "worker", report_name,
            str(os.getpid()), birth,
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 8
        while not report.exists():
            if worker.poll() is not None:
                raise RuntimeError(f"helper startup failed: {worker.stderr.read().decode('utf-8')}")
            if time.monotonic() >= deadline:
                raise TimeoutError("fixture did not admit its parent")
            time.sleep(0.01)
        if mode == "refuse":
            assert worker.wait(3) == 0
            return
        data = json.loads(report.read_text(encoding="utf-8"))
        print("ready", flush=True)
        if sys.stdin.readline().strip() != "retire":
            raise RuntimeError("missing fixture retirement command")
        if mode == "exit":
            os._exit(0)
        if mode != "close":
            raise ValueError("invalid fixture mode")
        if not kernel.CloseHandle(data["transfer"]["parent_handle"]):
            raise OSError("fixture could not close its own transferred Job")
        worker.wait(3)
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait(3)
        worker.stderr.close()


if __name__ == "__main__":
    main()
