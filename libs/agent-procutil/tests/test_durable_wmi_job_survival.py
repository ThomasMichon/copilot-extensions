"""Real Windows proof for the #12117 WMI durable daemon launch.

A launcher runs inside a kill-on-close Job that forbids breakaway, the shape
that used to make durable launches fail with ``WinError 5``. It starts one
durable daemon through :func:`agent_procutil.spawn_daemon` and one ordinary
bounded child. Closing the Job must end the launcher and the bounded child,
while the daemon keeps the same PID and creation time outside the caller's Jobs.

Only processes started by this test are terminated: each cleanup handle is
verified against the creation time the launcher captured through its own
process handle, so a reused PID is never touched. Windows-only.
"""

from __future__ import annotations

import json
import ctypes
from collections.abc import Callable
from ctypes import wintypes
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows Job semantics")

_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102

_LAUNCHER = """
import json, os, subprocess, sys, time
from pathlib import Path
import agent_procutil
from agent_procutil._daemon_instance_identity import _windows_birth
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
    result = {
        "daemon": daemon.pid, "bounded": bounded.pid,
        "births": {
            "daemon": _windows_birth(daemon._handle),
            "bounded": _windows_birth(int(bounded._handle)),
        },
    }
except agent_procutil.DaemonSpawnError as error:
    result = {"daemon_spawn_error": str(error)}
pending = Path(spec["result"] + ".tmp")
pending.write_text(json.dumps(result), encoding="utf-8")
os.replace(pending, spec["result"])
time.sleep(120)
"""


def _kernel32() -> Any:
    kernel32 = cast(Any, ctypes).WinDLL("kernel32", use_last_error=True)
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


def _open_verified(kernel32: Any, pid: int, birth: str) -> int | None:
    """Open ``pid`` only if the handle names the process the launcher recorded."""
    from agent_procutil._daemon_instance_identity import _windows_birth

    handle = kernel32.OpenProcess(
        _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE, False, pid,
    )
    if not handle:
        return None
    try:
        matches = _windows_birth(handle) == birth
    except OSError:
        matches = False
    if not matches:
        kernel32.CloseHandle(handle)
        return None
    return cast(int, handle)


def _release(kernel32: Any, handle: int) -> None:
    """Terminate (if still running) and close a verified fixture handle."""
    if kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0:
        kernel32.TerminateProcess(handle, 1)
        kernel32.WaitForSingleObject(handle, 5000)
    kernel32.CloseHandle(handle)


def test_cleanup_never_terminates_a_process_with_a_different_identity() -> None:
    kernel32 = _kernel32()
    bystander = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL, creationflags=0x08000000,
    )
    try:
        assert _open_verified(kernel32, bystander.pid, "windows:0") is None
        assert bystander.poll() is None, "a mismatched identity must never be terminated"
    finally:
        bystander.kill()
        bystander.wait(timeout=10)


def test_destroyed_native_scope_does_not_block_admission() -> None:
    from agent_procutil import _job_scopes

    kernel = pu._kernel32()
    pu._configure_job_api(kernel)
    name = _job_scopes.new_name()
    job = kernel.CreateJobObjectW(None, name)
    assert job
    assert kernel.CloseHandle(job)
    process = kernel.OpenProcess(0x1000, False, os.getpid())
    assert process
    environment = {_job_scopes.ENVIRONMENT_KEY: name}
    try:
        with _job_scopes.query_scopes(environment) as verify:
            verify(process)
        assert environment == {_job_scopes.ENVIRONMENT_KEY: name}
    finally:
        assert kernel.CloseHandle(process)


@pytest.mark.parametrize("expired_scope", [False, True])
def test_durable_daemon_survives_closing_a_strict_kill_on_close_caller_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expired_scope: bool,
) -> None:
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
    if expired_scope:
        from agent_procutil import _job_scopes

        env = _job_scopes.child_environment(env, append=_job_scopes.new_name())
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
        births = result["births"]
        for name in ("daemon", "bounded"):
            handle = _open_verified(kernel32, result[name], births[name])
            assert handle, f"{name} exited or its PID no longer names the launched process"
            handles[name] = handle
        in_job = wintypes.BOOL()
        assert kernel32.IsProcessInJob(handles["bounded"], job.handle, ctypes.byref(in_job))
        assert in_job.value, "the bounded child must stay in its caller's Job"
        assert kernel32.IsProcessInJob(handles["daemon"], job.handle, ctypes.byref(in_job))
        assert not in_job.value, "the durable daemon must not be in its caller's Job"

        job.close()
        job = None
        assert launcher.wait(timeout=15) is not None
        assert kernel32.WaitForSingleObject(handles["bounded"], 15000) == _WAIT_OBJECT_0
        time.sleep(1)
        assert kernel32.WaitForSingleObject(handles["daemon"], 0) == _WAIT_TIMEOUT
        assert birth_identity(result["daemon"]) == births["daemon"]
    finally:
        for handle in handles.values():
            _release(kernel32, handle)
        if launcher.poll() is None:
            launcher.kill()
            try:
                launcher.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        if job is not None:
            job.close()


def test_tracked_cleanup_scope_refuses_a_child_outside_the_immediate_caller_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil import _job_scopes, daemon_broker as broker

    original_process = broker.WindowsDaemonProcess
    name = _job_scopes.new_name()
    children: list[broker.WindowsDaemonProcess] = []
    jobs: list[pu.JobHandle] = []
    marker = tmp_path / "must-not-start"

    def process(pid: int, args: list[str]) -> broker.WindowsDaemonProcess:
        child = original_process(pid, args)
        if children:
            job = pu._assign_suspended_to_kill_on_close_job(
                child.pid, allow_breakaway=False, name=name,
            )
            assert job is not None
            jobs.append(job)
            assert broker._outside_caller_job(child), "the immediate-Job check alone would admit"
        children.append(child)
        return child

    monkeypatch.setattr(broker, "WindowsDaemonProcess", process)
    python = getattr(sys, "_base_executable", "") or sys.executable
    try:
        with pytest.raises(pu.DaemonSpawnError, match="child-adoption") as caught:
            broker.spawn_brokered_daemon(
                [python, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).touch()",
                 str(marker)],
                cwd=str(tmp_path), env=dict(os.environ),
                creationflags=pu._CREATE_NO_WINDOW | pu._CREATE_BREAKAWAY_FROM_JOB,
                log_path=str(tmp_path / "refused.log"), stderr_log_path=None,
                scope_environment={
                    _job_scopes.ENVIRONMENT_KEY: ";".join((_job_scopes.new_name(), name)),
                },
            )
        assert caught.value.admission_sent is False
        assert caught.value.cleanup_confirmed is True
        assert "launcher-owned cleanup Job" in str(caught.value.__cause__)
        assert len(children) == 2 and children[-1].returncode is not None
        assert not marker.exists()
    finally:
        for job in jobs:
            job.terminate_and_wait(5)


def test_wmi_launch_transfers_a_pipe_with_output_eof_and_caller_descriptors(tmp_path: Path) -> None:
    """The handle-transfer path ``spawn_daemon(stdio_fds=...)`` uses for session hosts."""
    import msvcrt
    import threading

    from agent_procutil import daemon_broker as broker

    python = getattr(sys, "_base_executable", "") or sys.executable
    code = (
        "import sys; sys.stdout.write('out-line\\n'); sys.stdout.flush(); "
        "sys.stderr.write('err-line\\n'); sys.stderr.flush()"
    )
    env = {k: v for k, v in os.environ.items() if k != "COPILOT_EXTENSIONS_TEST_CONTAINED"}
    read_fd, write_fd = os.pipe()
    process = None
    chunks: list[bytes] = []

    def drain() -> None:
        # Raw reads hold no buffered-reader lock, so a failing test never
        # blocks on the reader during cleanup.
        while chunk := os.read(read_fd, 4096):
            chunks.append(chunk)

    reader = threading.Thread(target=drain, daemon=True)
    try:
        with os.fdopen(write_fd, "wb") as output, open(os.devnull, "rb") as null:
            get_osfhandle: Callable[[int], int] = getattr(msvcrt, "get_osfhandle")
            handles = (
                get_osfhandle(null.fileno()), get_osfhandle(output.fileno()),
                get_osfhandle(output.fileno()),
            )
            process = broker.spawn_brokered_daemon(
                [python, "-c", code], cwd=str(tmp_path), env=env,
                creationflags=pu._CREATE_NO_WINDOW | pu._CREATE_BREAKAWAY_FROM_JOB,
                log_path=str(tmp_path / "launch.log"), stderr_log_path=None,
                stdio_handles=handles,
            )
            # The caller's descriptors stay open and usable after the transfer.
            os.fstat(output.fileno())
            os.fstat(null.fileno())
            output.write(b"")
        reader.start()
        reader.join(30)
        assert not reader.is_alive(), "the pipe never reached EOF after the daemon exited"
        assert b"".join(chunks).replace(b"\r\n", b"\n") == b"out-line\nerr-line\n"
        assert process.wait(timeout=10) == 0
    finally:
        # Stop the owned daemon first: its exit closes the write end the reader waits on.
        if process is not None:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)
            process.close()
        if reader.is_alive():
            reader.join(10)
        # Close the read end only once nothing can still be blocked reading it;
        # otherwise the daemon reader thread is left to end with the test process.
        if not reader.is_alive():
            os.close(read_fd)
