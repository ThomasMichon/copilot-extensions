"""Opt-in native Git/process/pipe/caller-death proof with unchanged 15s/5s bounds."""
from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from contextlib import ExitStack
from ctypes import wintypes
from pathlib import Path

import pytest
from agent_procutil import direct_python, spawn_sync_in_kill_on_close_job

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows atomic Job proof"),
    pytest.mark.skipif(os.environ.get("AGENT_WORKTREES_NATIVE_RECOVERY") != "1",
                       reason="opt in with AGENT_WORKTREES_NATIVE_RECOVERY=1"),
]


def wait_for(predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(0.01)
    raise AssertionError("native observation fixture did not reach its bounded marker")


def held_native_handle(pid, stack):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.OpenProcess(0x00100000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    stack.callback(kernel.CloseHandle, handle)
    assert kernel.WaitForSingleObject(handle, 0) in (0, 0x102)
    return lambda: kernel.WaitForSingleObject(handle, 0) == 0


@pytest.mark.parametrize("mode,kill_caller", [("stall", False), ("held-pipe", False), ("stall", True), ("before-resume", True), ("cancel", False)])
def test_native_git_alias_descendants_pipes_and_caller_death(tmp_path, mode, kill_caller):
    python, extra_env = direct_python(sys.executable)
    plain_python = python[4:] if python.startswith(chr(92) * 2 + "?" + chr(92)) else python
    helper = Path(__file__).with_name("native_git_observation_driver.py")
    with (tmp_path / "driver.log").open("w", encoding="utf-8") as log:
        process, job = spawn_sync_in_kill_on_close_job(
            [python, str(helper), str(tmp_path), "observe", mode, plain_python],
            require_job=True, allow_breakaway=False, env={**os.environ, **extra_env},
            stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert job is not None
        try:
            with ExitStack() as stack:
                names = ("suspended.json",) if mode == "before-resume" else (
                    "spawned.json", "alias.json", "leaf.json",
                )
                def ready():
                    assert process.poll() is None, (tmp_path / "driver.log").read_text()
                    return all((tmp_path / name).exists() for name in names)
                wait_for(ready)
                identities = [json.loads((tmp_path / name).read_text())["pid"] for name in names]
                settled = [held_native_handle(pid, stack) for pid in identities]
                assert not settled[-1](), "pipe-holding descendant was not live at admission"
                if mode == "cancel":
                    (tmp_path / "cancel-release").touch()
                if mode == "held-pipe":
                    (tmp_path / "alias-release").touch()
                    wait_for(settled[0])
                    assert not settled[-1]() and process.poll() is None
                if kill_caller:
                    # Kill only the exact caller handle; the outer fixture Job remains open.
                    process.terminate()
                process.wait(timeout=16)
                assert all(wait_for(done, timeout=5) for done in settled)
                if not kill_caller:
                    assert process.returncode == 0, (tmp_path / "driver.log").read_text()
                    result = json.loads((tmp_path / "result.json").read_text())
                    assert result["elapsed"] <= 15 and result["cleanup_reserve"] >= 5
                    assert result["same_deadline"] and result["started"]
                    assert result["cancelled"] is (mode == "cancel")
                    assert result["empty_job"] and result["native_handle_closed"] and result["readers_joined"]
                else:
                    assert not (tmp_path / "result.json").exists()
                    assert job.handle is not None, "outer Job caused caller-death cleanup"
        finally:
            job.terminate_and_wait(5)
            process.wait(timeout=5)
            assert job.handle is None


def test_native_observation_preserves_cwd_environment_output_and_legacy_binary(tmp_path):
    from agent_worktrees import push_timeout as pt

    python, _ = direct_python(sys.executable)
    env = dict(os.environ)
    env.pop("__PYVENV_LAUNCHER__", None)
    env["OBSERVATION_FIXTURE"] = "present"
    code = ("import json,os,sys; print(json.dumps([os.getcwd(),os.environ['OBSERVATION_FIXTURE']])); "
            "print('diagnostic',file=sys.stderr); sys.exit(7)")
    result = pt.run_bounded([python, "-c", code], cwd=tmp_path, env=env, timeout=15,
                            observation_deadline=time.monotonic() + 15)
    assert result.returncode == 7 and result.stderr == "diagnostic\n"
    assert json.loads(result.stdout) == [str(tmp_path), "present"]
    binary = pt.run_bounded(
        [python, "-c", "import sys; sys.stdout.buffer.write(sys.stdin.buffer.read())"],
        cwd=tmp_path, env=env, timeout=15, binary=True, input=bytes([0, 255, 13, 10]),
    )
    assert binary.returncode == 0 and binary.stdout == bytes([0, 255, 13, 10]) and binary.stderr == b""
