"""Opt-in native WSL/Windows ownership proof, outside the ordinary CI matrix."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("AGENT_PROCUTIL_WSL_TEST_DISTRO"),
    reason="explicit native Windows/WSL qualification only",
)

_TARGET = """
import ctypes,json,os,pathlib,subprocess,sys,time
leaf=subprocess.Popen(
    [sys.executable,'-c','import time;time.sleep(120)'],
    creationflags=0x08000000,
)
ready=pathlib.Path(sys.argv[1])
stage=ready.with_suffix('.staging')
stage.write_text(json.dumps({
    'pid':os.getpid(),'leaf':leaf.pid,'owner':os.getppid(),
    'console':ctypes.windll.kernel32.GetConsoleWindow(),
}))
stage.replace(ready)
time.sleep(120)
"""


def _linux_path(path: Path, distro: str) -> str:
    result = subprocess.run(
        ["wsl.exe", "-d", distro, "--exec", "wslpath", "-u", str(path)],
        capture_output=True, text=True, timeout=5, check=True, **no_window_kwargs(),
    )
    return result.stdout.strip()


def _open_owned(pid: int):
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x100000 | 0x1000 | 0x0001, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


def _exercise_case(tmp_path, case):
    distro = os.environ["AGENT_PROCUTIL_WSL_TEST_DISTRO"]
    source = Path(
        os.environ.get("AGENT_PROCUTIL_WSL_TEST_PACKAGE_ROOT")
        or Path(__file__).resolve().parents[1] / "src"
    )
    fixture = Path(__file__).with_name("wsl_caller_fixture.py")
    python = Path(sys._base_executable)
    pythonw = python.with_name("pythonw.exe")
    assert pythonw.is_file()
    ready, gate, config = (tmp_path / name for name in ("ready.json", "interrupt", "request.json"))
    config.write_text(json.dumps({
        "case": case, "source": _linux_path(source, distro),
        "pythonw": _linux_path(pythonw, distro), "gate": _linux_path(gate, distro),
        "argv": [str(python), "-c", _TARGET, str(ready)],
    }))
    process, outer_job = spawn_sync_in_kill_on_close_job(
        ["wsl.exe", "-d", distro, "--exec", "/usr/bin/python3", "-B",
         _linux_path(fixture, distro), _linux_path(config, distro)],
        require_job=True, allow_breakaway=False,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    handles = []
    try:
        deadline = time.monotonic() + 12
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(.02)
        assert ready.exists(), "Windows fixture did not establish its owned identities"
        identity = json.loads(ready.read_text())
        assert identity["console"] == 0
        handles = [_open_owned(identity[name]) for name in ("pid", "leaf", "owner")]
        assert all(kernel.WaitForSingleObject(handle, 0) == 258 for kernel, handle in handles)
        if case not in {"timeout", "stdin-stall"}:
            gate.write_text("interrupt")
        stdout, stderr = process.communicate(timeout=12)
        if case != "caller-death":
            assert process.returncode == 0, (stdout, stderr)
            assert json.loads(stdout)["outcome"] == case
        else:
            assert process.returncode != 0
        deadline = time.monotonic() + 5
        while (
            any(kernel.WaitForSingleObject(handle, 0) == 258 for kernel, handle in handles)
            and time.monotonic() < deadline
        ):
            time.sleep(.02)
        # This assertion precedes cleanup of the test harness's outer Job.
        assert all(kernel.WaitForSingleObject(handle, 0) == 0 for kernel, handle in handles)
    finally:
        gate.write_text("interrupt")
        for kernel, handle in handles:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                kernel.TerminateProcess(handle, 1)
                kernel.WaitForSingleObject(handle, 3000)
            kernel.CloseHandle(handle)
        if outer_job is not None:
            outer_job.terminate_and_wait(3)
        process.wait(timeout=3)
        process.stdout.close()
        process.stderr.close()


@pytest.mark.parametrize("case", ["timeout", "stdin-stall", "sigint", "caller-death"])
def test_real_windows_tree_is_gone_before_outer_job_cleanup(tmp_path, case):
    from test_spawn_daemon_windows_integration import _configure_win32, _WindowObserver

    with _WindowObserver(*_configure_win32()) as observer:
        for cycle in range(2):
            directory = tmp_path / str(cycle)
            directory.mkdir()
            _exercise_case(directory, case)
    assert observer.surfaced == {}, f"visible console windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
