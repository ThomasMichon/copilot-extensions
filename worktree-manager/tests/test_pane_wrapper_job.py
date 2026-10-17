"""Native pane ownership without a launch-time C# compiler."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

WRAPPER = Path(__file__).resolve().parents[1] / "bin" / "pane-wrapper.ps1"

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows pane Job Object")

QUERY_JOB = """
import ctypes, json, sys
from ctypes import wintypes
class BasicLimits(ctypes.Structure):
    _fields_ = [
        ('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64),
        ('flags', wintypes.DWORD), ('min_working_set', ctypes.c_size_t),
        ('max_working_set', ctypes.c_size_t), ('processes', wintypes.DWORD),
        ('affinity', ctypes.c_size_t), ('priority', wintypes.DWORD),
        ('scheduling', wintypes.DWORD),
    ]
class ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ('basic', BasicLimits), ('io', ctypes.c_uint64 * 6),
        ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
        ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t),
    ]
kernel = ctypes.WinDLL('kernel32', use_last_error=True)
kernel.QueryInformationJobObject.argtypes = [
    wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
]
kernel.QueryInformationJobObject.restype = wintypes.BOOL
kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel.OpenProcess.restype = wintypes.HANDLE
kernel.GetCurrentProcess.restype = wintypes.HANDLE
kernel.DuplicateHandle.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
    ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
]
kernel.DuplicateHandle.restype = wintypes.BOOL
kernel.CloseHandle.argtypes = [wintypes.HANDLE]
kernel.CloseHandle.restype = wintypes.BOOL
owner = kernel.OpenProcess(0x40, False, int(sys.argv[1]))
if not owner:
    raise ctypes.WinError(ctypes.get_last_error())
job = wintypes.HANDLE()
try:
    if not kernel.DuplicateHandle(
        owner, int(sys.argv[2]), kernel.GetCurrentProcess(), ctypes.byref(job),
        0, False, 2,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
finally:
    kernel.CloseHandle(owner)
limits = ExtendedLimits()
size = ctypes.sizeof(limits)
try:
    if not kernel.QueryInformationJobObject(job, 9, ctypes.byref(limits), size, None):
        raise ctypes.WinError(ctypes.get_last_error())
finally:
    kernel.CloseHandle(job)
assert limits.basic.flags == 0x2000, hex(limits.basic.flags)
assert limits.basic.processes == 0
assert limits.process_memory == limits.job_memory == 0
"""


def _pwsh() -> str:
    executable = shutil.which("pwsh")
    if not executable:
        pytest.skip("PowerShell 7 is required")
    return executable


def _quote(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def test_native_job_binding_does_not_compile_and_uses_kernel_abi(tmp_path):
    source = WRAPPER.read_text("utf-8")
    function = source.split("function Set-AwPaneKillOnCloseJob {", 1)[1].split(
        "\nSet-AwPaneKillOnCloseJob", 1
    )[0]
    child = tmp_path / "query.py"
    child.write_text(
        QUERY_JOB + "\nprint(json.dumps({'size': size, 'flags': limits.basic.flags}))\n",
        encoding="utf-8",
    )
    script = tmp_path / "bind.ps1"
    script.write_text(
        "function Add-Type { throw 'The pane hot path must not compile C#.' }\n"
        "function Set-AwPaneKillOnCloseJob {" + function + "\n"
        "Set-AwPaneKillOnCloseJob\n"
        "if (-not $script:AwPaneJobHandle) { throw 'Pane job was not installed.' }\n"
        f"& {_quote(sys.executable)} {_quote(child)} $PID $script:AwPaneJobHandle\n"
        "exit $LASTEXITCODE\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("WORKTREE_NO_PANE_JOB", None)
    result = subprocess.run(
        [_pwsh(), "-NoProfile", "-NoLogo", "-File", str(script)],
        env=env, capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "size": 144 if ctypes.sizeof(ctypes.c_void_p) == 8 else 112,
        "flags": 0x2000,
    }


def test_actual_wrapper_exit_reaps_its_native_child(tmp_path):
    from ctypes import wintypes

    child = tmp_path / "owned child.py"
    ready = tmp_path / "ready.json"
    child.write_text(
        "import json, os, pathlib, sys, time\n"
        "ready = pathlib.Path(sys.argv[1])\n"
        "temporary = ready.with_suffix('.tmp')\n"
        "temporary.write_text(json.dumps({'pid': os.getpid()}))\n"
        "temporary.replace(ready)\n"
        "time.sleep(60)\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("WORKTREE_NO_PANE_JOB", None)
    env["WORKTREE_PANE_MIN_RUNTIME"] = "0"
    env["WORKTREE_PANE_WAIT_TIMEOUT"] = "0"
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    process = subprocess.Popen(
        [_pwsh(), "-NoProfile", "-NoLogo", "-File", str(WRAPPER),
         sys.executable, str(child), str(ready)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        **no_window_kwargs(),
    )
    handle = None
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        if not ready.exists():
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
            pytest.fail(f"Owned native child did not start: {stdout!r}; {stderr!r}")
        pid = json.loads(ready.read_text())["pid"]
        handle = kernel.OpenProcess(0x100001, False, pid)
        assert handle, ctypes.WinError(ctypes.get_last_error())
        assert kernel.WaitForSingleObject(handle, 0) == 258
        process.kill()
        process.communicate(timeout=5)
        assert kernel.WaitForSingleObject(handle, 5000) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=5)
        if handle:
            if kernel.WaitForSingleObject(handle, 0) == 258:
                assert kernel.TerminateProcess(handle, 1)
                assert kernel.WaitForSingleObject(handle, 5000) == 0
            assert kernel.CloseHandle(handle)
