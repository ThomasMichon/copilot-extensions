"""Opt-in no-breakaway Job boundary; default legacy semantics are unchanged."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from unittest.mock import Mock

import pytest
from test_agent_procutil import _FakeKernel32

import agent_procutil as pu


@pytest.mark.parametrize("allow", [True, False])
def test_job_limit_flags_follow_explicit_breakaway_policy(monkeypatch, allow):
    kernel = _FakeKernel32()
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: kernel)
    job = pu._assign_suspended_to_kill_on_close_job(123, allow_breakaway=allow)
    assert job is not None
    assert kernel.limit_flags == (
        pu._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | (pu._JOB_OBJECT_LIMIT_BREAKAWAY_OK if allow else 0)
    )
    assert not kernel.limit_flags & 0x1000  # JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK
    job.close()


def test_conflicting_creation_flag_rejected_before_spawn(monkeypatch):
    spawn = Mock()
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="conflicts"):
        pu.spawn_sync_in_kill_on_close_job(
            ["fixture"], require_job=True, allow_breakaway=False,
            creationflags=pu._CREATE_BREAKAWAY_FROM_JOB,
        )
    spawn.assert_not_called()


@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_breakaway_policy_strict_boolean(monkeypatch, value):
    spawn = Mock()
    monkeypatch.setattr(subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="boolean"):
        pu.spawn_sync_in_kill_on_close_job(["fixture"], allow_breakaway=value)
    spawn.assert_not_called()


def test_failed_strict_assignment_never_resumes_and_confirms_exit(monkeypatch):
    process = Mock(pid=123)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    assign = Mock(return_value=None)
    resume = Mock()
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", assign)
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)
    with pytest.raises(RuntimeError, match="before child resume"):
        pu.spawn_sync_in_kill_on_close_job(
            ["fixture"], require_job=True, allow_breakaway=False,
        )
    assign.assert_called_once_with(123, allow_breakaway=False)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    resume.assert_not_called()


def test_no_breakaway_cannot_degrade_to_unowned_resume(monkeypatch):
    process = Mock(pid=123)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", Mock(return_value=None))
    resume = Mock()
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)
    with pytest.raises(RuntimeError, match="before child resume"):
        pu.spawn_sync_in_kill_on_close_job(["fixture"], allow_breakaway=False)
    resume.assert_not_called()
    process.wait.assert_called_once_with(timeout=5)


@pytest.mark.skipif(os.name != "nt", reason="real Windows suspended failure proof")
def test_real_assignment_failure_runs_no_child_code(tmp_path, monkeypatch):
    receipt = tmp_path / "must-not-run"
    actual_popen = subprocess.Popen
    created = []

    def capture(argv, **kwargs):
        process = actual_popen(argv, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", capture)
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", Mock(return_value=None))
    with pytest.raises(RuntimeError, match="before child resume"):
        pu.spawn_sync_in_kill_on_close_job(
            [pu.windowless_python(sys.executable), "-c",
             "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')",
             str(receipt)],
            require_job=True, allow_breakaway=False,
            env={**os.environ, **pu.windowless_python_env(sys.executable)},
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **pu.no_window_kwargs(),
        )
    assert len(created) == 1
    assert created[0].poll() is not None
    assert not receipt.exists()


@pytest.mark.skipif(os.name != "nt", reason="real Windows no-breakaway Job proof")
def test_real_job_contains_rejected_or_accepted_breakaway_child(tmp_path):
    receipt = tmp_path / "job-result.json"
    script = """
import json,os,subprocess,sys,time
from pathlib import Path
ordinary=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
try:
    escaped=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],
        creationflags=0x01000000,stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
except OSError as error:
    result={'denied':True,'winerror':error.winerror}
else:
    result={'denied':False,'breakaway':escaped.pid}
result.update(parent=os.getpid(),ordinary=ordinary.pid)
Path(sys.argv[1]).write_text(json.dumps(result))
time.sleep(120)
"""
    process, job = pu.spawn_sync_in_kill_on_close_job(
        [pu.windowless_python(sys.executable), "-c", script, str(receipt)],
        require_job=True, allow_breakaway=False,
        env={**os.environ, **pu.windowless_python_env(sys.executable)},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    handles = []
    kernel = pu._kernel32()
    pu._configure_job_teardown_api(kernel)
    kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    kernel.OpenProcess.restype = ctypes.c_void_p
    kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.WaitForSingleObject.restype = ctypes.c_uint32
    kernel.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    kernel.TerminateProcess.restype = ctypes.c_int
    try:
        assert job is not None
        until = time.monotonic() + 15
        while not receipt.exists() and time.monotonic() < until:
            assert process.poll() is None
            time.sleep(0.05)
        assert receipt.exists()
        result = json.loads(receipt.read_text())
        assert result["parent"] == process.pid
        if result["denied"]:
            assert result["winerror"] == 5
        children = [result["ordinary"]]
        if not result["denied"]:
            children.append(result["breakaway"])
        for pid in children:
            handle = kernel.OpenProcess(0x00100000 | 0x0001, False, pid)
            assert handle, "owned child exited before containment witness"
            handles.append(handle)
        info = pu._JobBasicAccountingInformation()
        returned = ctypes.c_uint32()
        assert kernel.QueryInformationJobObject(
            job.handle, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned),
        )
        assert info.ActiveProcesses >= 1 + len(children)
        job.terminate_and_wait(15.0)
        assert job.handle is None
        process.wait(timeout=15)
        for handle in handles:
            assert kernel.WaitForSingleObject(handle, 3000) == 0, "owned child escaped strict Job"
    finally:
        if job is not None and job.handle is not None:
            job.terminate_and_wait(15.0)
        process.wait(timeout=15)
        for handle in handles:
            try:
                if kernel.WaitForSingleObject(handle, 0) != 0:
                    assert kernel.TerminateProcess(handle, 1)
                    assert kernel.WaitForSingleObject(handle, 3000) == 0
            finally:
                assert kernel.CloseHandle(handle)
