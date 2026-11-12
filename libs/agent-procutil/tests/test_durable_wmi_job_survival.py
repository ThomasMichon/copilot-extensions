"""Real Windows proof for the #12117 WMI durable daemon launch.

A launcher runs inside a kill-on-close Job that forbids breakaway, the shape
that used to make durable launches fail with ``WinError 5``. It starts one
durable daemon through :func:`agent_procutil.spawn_daemon` and one ordinary
bounded child. Closing the Job must end the launcher and the bounded child,
while the daemon keeps the same PID and creation time and belongs to no Job.

Only processes started by this test are terminated. Windows-only.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job semantics")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102

_LAUNCHER = """
import json, subprocess, sys, time
from pathlib import Path
import agent_procutil
from agent_procutil.daemon_instances import DaemonInstanceSpec, DaemonLeasePolicy
spec = json.loads(sys.argv[1])
root = Path(spec["root"])
sleeper = [spec["python"], "-c", "import time; time.sleep(120)"]
try:
    daemon = agent_procutil.spawn_daemon(
        sleeper, cwd=str(root), log_path=str(root / "daemon.log"),
        instance=DaemonInstanceSpec(
            "wmi-durable-probe", root, Path(agent_procutil.__file__).resolve(),
            purpose="unattended-test", registry_root=root / "instances",
            lease=DaemonLeasePolicy(30, 120),
        ),
    )
    bounded = subprocess.Popen(sleeper, stdin=subprocess.DEVNULL, creationflags=0x08000000)
    result = {"daemon": daemon.pid, "bounded": bounded.pid}
except agent_procutil.DaemonSpawnError as error:
    result = {"daemon_spawn_error": str(error)}
Path(spec["result"]).write_text(json.dumps(result), encoding="utf-8")
time.sleep(120)
"""


def _kernel32():
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    return kernel32


def _open(kernel32, pid: int):
    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE, False, pid,
    )
    assert handle, f"process {pid} exited before it could be observed"
    return handle


def test_durable_daemon_survives_closing_a_strict_kill_on_close_caller_job(tmp_path, monkeypatch):
    from agent_procutil._daemon_instance_identity import birth_identity

    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert not pu.contained_test_mode()
    kernel32 = _kernel32()
    root = tmp_path / "probe"
    root.mkdir()
    launcher_script = root / "launcher.py"
    launcher_script.write_text(_LAUNCHER, encoding="utf-8")
    result_path = root / "result.json"
    python = getattr(sys, "_base_executable", "") or sys.executable
    spec = {"root": str(root), "result": str(result_path), "python": python}
    env = os.environ.copy()
    env.pop("__PYVENV_LAUNCHER__", None)
    src = str(Path(pu.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")

    launcher, job = pu.spawn_sync_in_kill_on_close_job(
        [pu.windowless_python(sys.executable), str(launcher_script), json.dumps(spec)],
        env=env, require_job=True, allow_breakaway=False,
    )
    handles: dict[str, int] = {}
    try:
        assert job is not None
        deadline = time.monotonic() + 60
        while not result_path.exists() and time.monotonic() < deadline:
            assert launcher.poll() is None, "the launcher exited before recording a result"
            time.sleep(0.05)
        result = json.loads(result_path.read_text(encoding="utf-8"))
        assert "daemon_spawn_error" not in result, result.get("daemon_spawn_error")
        handles = {name: _open(kernel32, result[name]) for name in ("daemon", "bounded")}
        birth = birth_identity(result["daemon"])
        in_job = wintypes.BOOL()
        assert kernel32.IsProcessInJob(handles["bounded"], job.handle, ctypes.byref(in_job))
        assert in_job.value, "the bounded child must stay in its caller's Job"
        # The broker requires a job-free child at creation; afterwards the
        # leased daemon may own a Job for its own children, never the caller's.
        assert kernel32.IsProcessInJob(handles["daemon"], job.handle, ctypes.byref(in_job))
        assert not in_job.value, "the durable daemon must not be in its caller's Job"

        job.close()
        job = None
        assert launcher.wait(timeout=15) is not None
        assert kernel32.WaitForSingleObject(handles["bounded"], 15000) == _WAIT_OBJECT_0
        time.sleep(1)
        assert kernel32.WaitForSingleObject(handles["daemon"], 0) == _WAIT_TIMEOUT
        assert birth_identity(result["daemon"]) == birth
    finally:
        for handle in handles.values():
            if kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 5000)
            kernel32.CloseHandle(handle)
        if launcher.poll() is None:
            launcher.kill()
            try:
                launcher.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if job is not None:
            job.close()
