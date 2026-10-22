"""Tests for the shared headless/detached process-spawn kwargs."""
from __future__ import annotations

import asyncio
import ctypes
import os
from pathlib import Path
import subprocess
import sys
import uuid
from unittest.mock import AsyncMock, Mock

import pytest

import agent_procutil as pu


def test_required_sync_job_failure_never_resumes_child(monkeypatch):
    process = Mock(pid=123)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", lambda pid: None)
    resume = Mock(return_value=True)
    monkeypatch.setattr(pu, "_resume_suspended_process", resume)
    with pytest.raises(RuntimeError, match="before child resume"):
        pu.spawn_sync_in_kill_on_close_job(["fixture"], require_job=True)
    process.kill.assert_called_once_with()
    process.wait.assert_called_once_with(timeout=5)
    resume.assert_not_called()


def test_contained_test_mode_reads_explicit_runner_marker(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert not pu.contained_test_mode()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.contained_test_mode()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "true")
    assert not pu.contained_test_mode()


def test_no_window_kwargs_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    kw = pu.no_window_kwargs()
    assert kw == {"creationflags": pu._CREATE_NO_WINDOW}


def test_no_window_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.no_window_kwargs() == {}


def test_no_window_flags(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.no_window_flags() == pu._CREATE_NO_WINDOW
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.no_window_flags() == 0


def test_windowless_python_prefers_pythonw_on_windows(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(pythonw)


def test_windowless_python_prefers_base_home_pythonw_from_pyvenv_cfg(monkeypatch, tmp_path):
    venv_root = tmp_path / "venv"
    scripts = venv_root / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_text("")
    (scripts / "pythonw.exe").write_text("")
    base = tmp_path / "base-python"
    base.mkdir()
    base_pythonw = base / "pythonw.exe"
    base_pythonw.write_text("")
    (venv_root / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(base_pythonw)
    assert pu.windowless_python_env(python) == {"__PYVENV_LAUNCHER__": str(python)}


def test_windowless_python_falls_back_to_local_pythonw_when_base_home_lacks_it(
    monkeypatch, tmp_path
):
    venv_root = tmp_path / "venv"
    scripts = venv_root / "Scripts"
    scripts.mkdir(parents=True)
    python = scripts / "python.exe"
    python.write_text("")
    local_pythonw = scripts / "pythonw.exe"
    local_pythonw.write_text("")
    base = tmp_path / "base-python"
    base.mkdir()
    (venv_root / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(local_pythonw)
    assert pu.windowless_python_env(python) == {}


def test_windowless_python_falls_back_without_pythonw(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(python)


def test_windowless_python_noop_off_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.windowless_python("/usr/bin/python3") == "/usr/bin/python3"
    assert pu.windowless_python_env("/usr/bin/python3") == {}


def test_detached_kwargs_windows_plain(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = pu.detached_kwargs()["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert not (flags & pu._CREATE_BREAKAWAY_FROM_JOB)


def test_detached_kwargs_windows_breakaway(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = pu.detached_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert flags & pu._CREATE_BREAKAWAY_FROM_JOB


def test_detached_kwargs_windows_contained_suppresses_breakaway(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    flags = pu.detached_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert not (flags & pu._CREATE_BREAKAWAY_FROM_JOB)


def test_detached_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert pu.detached_kwargs() == {"start_new_session": True}
    assert pu.detached_kwargs(breakaway=True) == {"start_new_session": True}


def test_detached_kwargs_posix_contained_suppresses_new_session(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.detached_kwargs() == {}
    assert pu.detached_kwargs(breakaway=True) == {}


def test_windowless_daemon_kwargs_windows_preserves_no_window_host(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = pu.windowless_daemon_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._CREATE_NO_WINDOW
    assert flags & pu._CREATE_BREAKAWAY_FROM_JOB
    assert not (flags & pu._DETACHED_PROCESS)
    assert not (flags & pu._CREATE_NEW_PROCESS_GROUP)


def test_windowless_daemon_kwargs_windows_contained_suppresses_breakaway(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.windowless_daemon_kwargs(breakaway=True) == {
        "creationflags": pu._CREATE_NO_WINDOW
    }


def test_windowless_daemon_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert pu.windowless_daemon_kwargs(breakaway=True) == {
        "start_new_session": True
    }


def test_windowless_daemon_kwargs_posix_contained(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.windowless_daemon_kwargs(breakaway=True) == {}


class _FakeWinFunc:
    def __init__(self, name, func):
        self.name = name
        self.func = func
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        return self.func(*args)


class _FakeKernel32:
    def __init__(self):
        self.calls = []
        self.fail: str | None = None
        self.limit_flags: int | None = None
        self.CreateJobObjectW = _FakeWinFunc(
            "CreateJobObjectW", self._create_job_object
        )
        self.SetInformationJobObject = _FakeWinFunc(
            "SetInformationJobObject", self._set_information_job_object
        )
        self.OpenProcess = _FakeWinFunc("OpenProcess", self._open_process)
        self.AssignProcessToJobObject = _FakeWinFunc(
            "AssignProcessToJobObject", self._assign_process_to_job_object
        )
        self.CloseHandle = _FakeWinFunc("CloseHandle", self._close_handle)

    def _create_job_object(self, attrs, name):
        self.calls.append(("CreateJobObjectW", attrs, name))
        return 101

    def _set_information_job_object(self, job, info_class, limit_info, size):
        self.calls.append(("SetInformationJobObject", job, info_class, size))
        self.limit_flags = limit_info._obj.BasicLimitInformation.LimitFlags
        return 0 if self.fail == "SetInformationJobObject" else 1

    def _open_process(self, access, inherit, pid):
        self.calls.append(("OpenProcess", access, inherit, pid))
        return 0 if self.fail == "OpenProcess" else 202

    def _assign_process_to_job_object(self, job, process):
        self.calls.append(("AssignProcessToJobObject", job, process))
        return 0 if self.fail == "AssignProcessToJobObject" else 1

    def _close_handle(self, handle):
        self.calls.append(("CloseHandle", handle))
        return 1


class _FakeNtdll:
    def __init__(self, calls, *, resume_status=0):
        self.calls = calls
        self.resume_status = resume_status
        self.NtResumeProcess = _FakeWinFunc("NtResumeProcess", self._resume_process)

    def _resume_process(self, process):
        self.calls.append(("NtResumeProcess", process))
        return self.resume_status


class _FakeProcess:
    pid = 12345

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def test_assign_suspended_to_kill_on_close_job_noop_off_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu._assign_suspended_to_kill_on_close_job(12345) is None


@pytest.mark.parametrize("allow_breakaway", [True, False])
def test_assign_suspended_to_kill_on_close_job_assigns_pid_to_job(monkeypatch, allow_breakaway):
    fake = _FakeKernel32()
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)

    job = pu._assign_suspended_to_kill_on_close_job(12345, allow_breakaway=allow_breakaway)

    assert job is not None
    assert fake.calls[:4] == [
        ("CreateJobObjectW", None, None),
        (
            "SetInformationJobObject",
            101,
            pu._JobObjectExtendedLimitInformation,
            pu.ctypes.sizeof(pu._build_job_extended_limit_info()()),
        ),
        ("OpenProcess", pu._PROCESS_SET_QUOTA | pu._PROCESS_TERMINATE, False, 12345),
        ("AssignProcessToJobObject", 101, 202),
    ]
    assert fake.limit_flags == (
        pu._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | (pu._JOB_OBJECT_LIMIT_BREAKAWAY_OK if allow_breakaway else 0)
    )
    assert fake.calls[4:] == [("CloseHandle", 202)]

    job.close()
    job.close()
    assert fake.calls[5:] == [("CloseHandle", 101)]


@pytest.mark.parametrize(
    ("failure", "closed_handles"),
    [
        ("SetInformationJobObject", [101]),
        ("OpenProcess", [101]),
        ("AssignProcessToJobObject", [202, 101]),
    ],
)
def test_assign_suspended_to_kill_on_close_job_failures_return_none_and_close_handles(
    monkeypatch, failure, closed_handles
):
    fake = _FakeKernel32()
    fake.fail = failure
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu.ctypes, "get_last_error", lambda: 5, raising=False)  # Windows-only in ctypes

    assert pu._assign_suspended_to_kill_on_close_job(12345) is None
    assert [call[1] for call in fake.calls if call[0] == "CloseHandle"] == closed_handles


def test_spawn_in_kill_on_close_job_noop_off_windows(monkeypatch):
    process = _FakeProcess()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", spawn)

    result, job = asyncio.run(pu.spawn_in_kill_on_close_job("python", creationflags=7))

    assert result is process
    assert job is None
    assert spawn.await_args.args == ("python",)
    assert spawn.await_args.kwargs["creationflags"] == 7


def test_spawn_in_kill_on_close_job_assigns_before_resuming(monkeypatch):
    fake = _FakeKernel32()
    ntdll = _FakeNtdll(fake.calls)
    process = _FakeProcess()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu, "_ntdll", lambda: ntdll)
    monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", spawn)

    result, job = asyncio.run(
        pu.spawn_in_kill_on_close_job("python", creationflags=pu._CREATE_NO_WINDOW)
    )

    assert result is process
    assert job is not None
    assert spawn.await_args.kwargs["creationflags"] == (
        pu._CREATE_NO_WINDOW | pu._CREATE_SUSPENDED
    )
    operations = [call[0] for call in fake.calls]
    assert operations.index("AssignProcessToJobObject") < operations.index("NtResumeProcess")
    assert not process.killed
    job.close()


def test_spawn_in_kill_on_close_job_resumes_when_assignment_fails(monkeypatch):
    fake = _FakeKernel32()
    fake.fail = "AssignProcessToJobObject"
    ntdll = _FakeNtdll(fake.calls)
    process = _FakeProcess()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu, "_ntdll", lambda: ntdll)
    monkeypatch.setattr(pu.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", spawn)

    result, job = asyncio.run(pu.spawn_in_kill_on_close_job("python"))

    assert result is process
    assert job is None
    assert any(call[0] == "AssignProcessToJobObject" for call in fake.calls)
    assert any(call[0] == "NtResumeProcess" for call in fake.calls)
    assert not process.killed


def test_spawn_in_kill_on_close_job_kills_and_closes_job_when_resume_fails(monkeypatch):
    fake = _FakeKernel32()
    ntdll = _FakeNtdll(fake.calls, resume_status=-1)
    process = _FakeProcess()
    spawn = AsyncMock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu, "_ntdll", lambda: ntdll)
    monkeypatch.setattr(pu.asyncio, "create_subprocess_exec", spawn)

    with pytest.raises(RuntimeError, match="failed to resume suspended process"):
        asyncio.run(pu.spawn_in_kill_on_close_job("python"))

    assert process.killed
    assert ("AssignProcessToJobObject", 101, 202) in fake.calls
    assert ("NtResumeProcess", 202) in fake.calls
    assert [call[1] for call in fake.calls if call[0] == "CloseHandle"].count(101) == 1


def test_spawn_sync_in_kill_on_close_job_noop_off_windows(monkeypatch):
    process = _FakeProcess()
    spawn = Mock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setattr(pu.subprocess, "Popen", spawn)

    result, job = pu.spawn_sync_in_kill_on_close_job(["python"], creationflags=7)

    assert result is process
    assert job is None
    assert spawn.call_args.args == (["python"],)
    assert spawn.call_args.kwargs["creationflags"] == 7


def test_spawn_sync_in_kill_on_close_job_assigns_before_resuming(monkeypatch):
    fake = _FakeKernel32()
    ntdll = _FakeNtdll(fake.calls)
    process = _FakeProcess()
    spawn = Mock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu, "_ntdll", lambda: ntdll)
    monkeypatch.setattr(pu.subprocess, "Popen", spawn)

    result, job = pu.spawn_sync_in_kill_on_close_job(
        ["python"], creationflags=pu._CREATE_NO_WINDOW
    )

    assert result is process
    assert job is not None
    assert spawn.call_args.kwargs["creationflags"] == (
        pu._CREATE_NO_WINDOW | pu._CREATE_SUSPENDED
    )
    operations = [call[0] for call in fake.calls]
    assert operations.index("AssignProcessToJobObject") < operations.index("NtResumeProcess")
    assert not process.killed
    job.close()

def test_strict_sync_job_disallows_breakaway(monkeypatch):
    process = _FakeProcess()
    job = Mock()
    assign = Mock(return_value=job)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu.subprocess, "Popen", Mock(return_value=process))
    monkeypatch.setattr(pu, "_assign_suspended_to_kill_on_close_job", assign)
    monkeypatch.setattr(pu, "_resume_suspended_process", lambda pid: True)
    result, owned = pu.spawn_sync_in_kill_on_close_job(
        ["fixture"], require_job=True, allow_breakaway=False,
    )
    assert result is process and owned is job
    assign.assert_called_once_with(process.pid, allow_breakaway=False)


def test_spawn_sync_in_kill_on_close_job_resumes_when_assignment_fails(monkeypatch):
    fake = _FakeKernel32()
    fake.fail = "AssignProcessToJobObject"
    ntdll = _FakeNtdll(fake.calls)
    process = _FakeProcess()
    spawn = Mock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu, "_ntdll", lambda: ntdll)
    monkeypatch.setattr(pu.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(pu.subprocess, "Popen", spawn)

    result, job = pu.spawn_sync_in_kill_on_close_job(["python"])

    assert result is process
    assert job is None
    assert any(call[0] == "AssignProcessToJobObject" for call in fake.calls)
    assert any(call[0] == "NtResumeProcess" for call in fake.calls)
    assert not process.killed


def test_spawn_sync_in_kill_on_close_job_kills_and_closes_job_when_resume_fails(monkeypatch):
    fake = _FakeKernel32()
    ntdll = _FakeNtdll(fake.calls, resume_status=-1)
    process = _FakeProcess()
    spawn = Mock(return_value=process)
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu, "_ntdll", lambda: ntdll)
    monkeypatch.setattr(pu.subprocess, "Popen", spawn)

    with pytest.raises(RuntimeError, match="failed to resume suspended process"):
        pu.spawn_sync_in_kill_on_close_job(["python"])

    assert process.killed
    assert ("AssignProcessToJobObject", 101, 202) in fake.calls
    assert ("NtResumeProcess", 202) in fake.calls
    assert [call[1] for call in fake.calls if call[0] == "CloseHandle"].count(101) == 1


def _wait_or_terminate_process_handle(handle: int, *, timeout_ms: int) -> None:
    wait_result = pu.ctypes.windll.kernel32.WaitForSingleObject(handle, timeout_ms)
    if wait_result == 0:
        return
    pu.ctypes.windll.kernel32.TerminateProcess(handle, 1)
    pu.ctypes.windll.kernel32.WaitForSingleObject(handle, 5000)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_job_kills_grandchild_when_owner_is_hard_killed():
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    child_code = r"""
import asyncio
import sys
import time

from agent_procutil import spawn_in_kill_on_close_job

async def main():
    grandchild, job = await spawn_in_kill_on_close_job(
        sys.executable, "-c", "import time; time.sleep(60)"
    )
    if job is None:
        raise SystemExit("failed to spawn grandchild inside job")
    print(grandchild.pid, flush=True)
    time.sleep(60)

asyncio.run(main())
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    grandchild_pid = int(child.stdout.readline().strip())
    grandchild_handle = pu.ctypes.windll.kernel32.OpenProcess(
        0x00100000 | 0x1000 | 0x0001, False, grandchild_pid
    )
    assert grandchild_handle
    try:
        child.kill()
        child.wait(timeout=5)
        assert pu.ctypes.windll.kernel32.WaitForSingleObject(grandchild_handle, 5000) == 0
    finally:
        if child.poll() is None:
            child.kill()
        _wait_or_terminate_process_handle(grandchild_handle, timeout_ms=0)
        pu.ctypes.windll.kernel32.CloseHandle(grandchild_handle)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_spawn_in_kill_on_close_job_child_starts_inside_job():
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    child_code = r"""
import time

print("ready", flush=True)
time.sleep(10)
"""

    async def scenario():
        process, job = await pu.spawn_in_kill_on_close_job(
            sys.executable,
            "-c",
            child_code,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        try:
            assert job is not None
            assert (
                await asyncio.wait_for(process.stdout.readline(), timeout=10)
            ).strip() == b"ready"
            from ctypes import wintypes

            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            kernel32.OpenProcess.restype = wintypes.HANDLE
            kernel32.IsProcessInJob.argtypes = [
                wintypes.HANDLE,
                wintypes.HANDLE,
                ctypes.POINTER(wintypes.BOOL),
            ]
            kernel32.IsProcessInJob.restype = wintypes.BOOL
            kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = kernel32.OpenProcess(0x1000, False, process.pid)
            assert handle
            try:
                in_returned_job = wintypes.BOOL()
                assert kernel32.IsProcessInJob(
                    handle, job.handle, ctypes.byref(in_returned_job)
                )
                assert in_returned_job.value
            finally:
                kernel32.CloseHandle(handle)
            process.kill()
            await process.wait()
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if job is not None:
                job.close()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_job_permits_explicit_breakaway_grandchild():
    """The containment job must allow an ordinary descendant to die with it
    while letting a descendant that explicitly requests
    ``CREATE_BREAKAWAY_FROM_JOB`` escape and outlive it -- a real command run
    through this containment (`agent-worktrees update`) can itself spawn a
    daemon cutover successor with exactly that flag
    (``windowless_daemon_kwargs(breakaway=True)``), and Windows rejects that
    spawn outright unless the containing job's limit flags permit it."""
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    child_code = r"""
import subprocess
import sys
import time

CREATE_BREAKAWAY_FROM_JOB = 0x01000000

# Only spawned once this process is itself already a job member (the test
# harness assigns the job before resuming it), so both grandchildren inherit
# membership at spawn time unless they opt out.
ordinary = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
breakaway = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(60)"],
    creationflags=CREATE_BREAKAWAY_FROM_JOB,
)
print(ordinary.pid, breakaway.pid, flush=True)
time.sleep(60)
"""
    process, job = pu.spawn_sync_in_kill_on_close_job(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert job is not None, "failed to spawn the test harness itself inside a job"
    try:
        pids = process.stdout.readline().strip().split()
        assert len(pids) == 2, f"expected 'ordinary breakaway' pids, got: {pids!r}"
        ordinary_pid, breakaway_pid = (int(p) for p in pids)

        ordinary_handle = pu.ctypes.windll.kernel32.OpenProcess(
            0x00100000 | 0x1000 | 0x0001, False, ordinary_pid
        )
        breakaway_handle = pu.ctypes.windll.kernel32.OpenProcess(
            0x00100000 | 0x1000 | 0x0001, False, breakaway_pid
        )
        assert ordinary_handle and breakaway_handle
        try:
            job.close()
            job = None
            assert (
                pu.ctypes.windll.kernel32.WaitForSingleObject(ordinary_handle, 5000) == 0
            ), "ordinary descendant must die with the job"
            assert (
                pu.ctypes.windll.kernel32.WaitForSingleObject(breakaway_handle, 0) == 0x102
            ), "breakaway descendant must survive the job closing"
        finally:
            _wait_or_terminate_process_handle(ordinary_handle, timeout_ms=0)
            _wait_or_terminate_process_handle(breakaway_handle, timeout_ms=0)
            pu.ctypes.windll.kernel32.CloseHandle(ordinary_handle)
            pu.ctypes.windll.kernel32.CloseHandle(breakaway_handle)
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)
        if job is not None:
            job.close()


# -- spawn_daemon ------------------------------------------------------------


class _FakePopen:
    def __init__(self, calls, failures=()):
        self.calls = calls
        self.failures = list(failures)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if self.failures:
            raise self.failures.pop(0)
        proc = Mock()
        proc.pid = 4242
        return proc


def _access_denied() -> OSError:
    exc = OSError(13, "Access is denied")
    exc.winerror = 5
    return exc


@pytest.fixture()
def fake_popen(monkeypatch):
    from agent_procutil import daemon, daemon_broker

    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: False)
    calls: list = []
    fake = _FakePopen(calls)
    monkeypatch.setattr(pu.subprocess, "Popen", fake)
    monkeypatch.setattr(
        daemon, "spawn_brokered_daemon", Mock(side_effect=pu.DaemonSpawnError("fixture")),
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    return fake


@pytest.mark.parametrize("bad", [None, "relative/dir"])
def test_spawn_daemon_requires_absolute_cwd(tmp_path, fake_popen, bad):
    with pytest.raises(ValueError, match="cwd"):
        pu.spawn_daemon(["x"], cwd=bad, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


def test_spawn_daemon_rejects_missing_cwd(tmp_path, fake_popen):
    with pytest.raises(ValueError, match="does not exist"):
        pu.spawn_daemon(["x"], cwd=tmp_path / "gone", log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


def test_spawn_daemon_rejects_payload_cwd(tmp_path, fake_popen):
    payload = tmp_path / "installed-plugins" / "market" / "plugin"
    payload.mkdir(parents=True)
    with pytest.raises(ValueError, match="plugin payload"):
        pu.spawn_daemon(["x"], cwd=payload, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


def _payload_and_link(tmp_path):
    payload = tmp_path / "installed-plugins" / "market" / "plugin"
    payload.mkdir(parents=True)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    return payload, runtime / "current"


def test_spawn_daemon_rejects_cwd_that_resolves_into_payload(tmp_path, monkeypatch, fake_popen):
    """The lexical path is clean; only its resolved target is a payload."""
    payload, link = _payload_and_link(tmp_path)
    link.mkdir()
    real = pu.os.path.realpath
    monkeypatch.setattr(
        pu.os.path, "realpath",
        lambda p, *a, **k: str(payload) if os.fspath(p) == str(link) else real(p, *a, **k),
    )
    with pytest.raises(ValueError, match="plugin payload"):
        pu.spawn_daemon(["x"], cwd=link, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_spawn_daemon_rejects_junction_into_payload(tmp_path, fake_popen):
    _winapi = pytest.importorskip("_winapi")
    payload, link = _payload_and_link(tmp_path)
    _winapi.CreateJunction(str(payload), str(link))
    with pytest.raises(ValueError, match="plugin payload"):
        pu.spawn_daemon(["x"], cwd=link, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


def test_spawn_daemon_rejects_symlink_into_payload(tmp_path, fake_popen):
    payload, link = _payload_and_link(tmp_path)
    try:
        os.symlink(payload, link, target_is_directory=True)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"directory symlinks unavailable here: {exc}")
    with pytest.raises(ValueError, match="plugin payload"):
        pu.spawn_daemon(["x"], cwd=link, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


def test_spawn_daemon_requires_absolute_log_path(tmp_path, fake_popen):
    # A relative log path is never resolved against cwd: the caller meant its
    # own directory, and the daemon's cwd differs from it.
    with pytest.raises(ValueError, match="log_path must be an absolute path"):
        pu.spawn_daemon(["x"], cwd=tmp_path, log_path="d.log")
    assert fake_popen.calls == []
    assert not (tmp_path / "d.log").exists()


def test_spawn_daemon_requires_log_path(tmp_path, fake_popen):
    with pytest.raises(ValueError, match="log_path"):
        pu.spawn_daemon(["x"], cwd=tmp_path, log_path=None)


def test_spawn_daemon_windows_detached_breakaway_and_log(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("SPAWN_DAEMON_BASE", "base")
    log_path = tmp_path / "logs" / "daemon.log"

    proc = pu.spawn_daemon(
        ["py", "-m", "svc"], cwd=tmp_path, log_path=log_path,
        env_overrides={"SVC_TOKEN": "t"},
    )

    assert proc.pid == 4242
    (argv, kwargs), = fake_popen.calls
    assert argv == ["py", "-m", "svc"]
    assert kwargs["creationflags"] == (
        pu._DETACHED_PROCESS | pu._CREATE_NEW_PROCESS_GROUP | pu._CREATE_BREAKAWAY_FROM_JOB
    )
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["stdout"].name == str(log_path)
    assert kwargs["stdout"].closed, "the parent must not keep the daemon log open"
    assert kwargs["close_fds"] is True
    assert kwargs["env"]["SVC_TOKEN"] == "t"
    assert kwargs["env"]["SPAWN_DAEMON_BASE"] == "base"
    assert "spawn_daemon cwd=" in log_path.read_text(encoding="utf-8")


def test_spawn_daemon_windows_console_children_uses_no_window(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    pu.spawn_daemon(
        ["py"], cwd=tmp_path, log_path=tmp_path / "d.log",
        console_children=True, extra_creationflags=0x20,
    )
    (_, kwargs), = fake_popen.calls
    assert kwargs["creationflags"] == (
        pu._CREATE_NO_WINDOW | pu._CREATE_BREAKAWAY_FROM_JOB | 0x20
    )


def test_spawn_daemon_explicit_env_replaces_inherited(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setenv("SPAWN_DAEMON_LEAK", "1")
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log", env={"A": "1"})
    (_, kwargs), = fake_popen.calls
    assert kwargs["env"] == {"A": "1"}


def test_spawn_daemon_posix_new_session(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log", extra_creationflags=0x20)
    (_, kwargs), = fake_popen.calls
    assert kwargs["start_new_session"] is True
    assert "creationflags" not in kwargs


def test_spawn_daemon_contained_mode_stays_contained(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log")
    (_, kwargs), = fake_popen.calls
    assert not kwargs["creationflags"] & pu._CREATE_BREAKAWAY_FROM_JOB


def test_spawn_daemon_breakaway_denied_is_surfaced(tmp_path, monkeypatch, fake_popen):
    """A Job that forbids breakaway must not silently hand the daemon the
    caller's lifetime: the spawn fails (as an OSError) and the log says why."""
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(_access_denied())
    log_path = tmp_path / "d.log"

    with pytest.raises(pu.DaemonSpawnError, match="forbids breakaway") as info:
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=log_path)

    assert isinstance(info.value, OSError)
    assert len(fake_popen.calls) == 1
    assert "daemon not started" in log_path.read_text(encoding="utf-8")


def test_spawn_daemon_breakaway_denied_contained_fallback(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(_access_denied())
    log_path = tmp_path / "d.log"

    proc = pu.spawn_daemon(["py"], cwd=tmp_path, log_path=log_path, contained_fallback=True)

    assert proc.pid == 4242
    first, second = (kwargs["creationflags"] for _, kwargs in fake_popen.calls)
    assert first & pu._CREATE_BREAKAWAY_FROM_JOB
    assert second == pu._DETACHED_PROCESS | pu._CREATE_NEW_PROCESS_GROUP
    assert "starting the daemon inside it" in log_path.read_text(encoding="utf-8")


def test_spawn_daemon_other_spawn_errors_propagate(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(FileNotFoundError(2, "missing"))
    with pytest.raises(FileNotFoundError):
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log")
    assert len(fake_popen.calls) == 1


def test_spawn_daemon_access_denied_without_breakaway_propagates(
    tmp_path, monkeypatch, fake_popen
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(_access_denied())
    with pytest.raises(OSError):
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log", breakaway=False)
    assert len(fake_popen.calls) == 1


def _fake_credential_argv() -> tuple[list[str], str]:
    """An argv carrying a fake credential, assembled from fragments so no
    secret-shaped literal appears in this source file."""
    value = "fake-" + "val" + "ue-" + uuid.uuid4().hex
    flag = "--" + "tok" + "en"
    url = "https://" + "user" + ":" + value + "@" + "example.invalid/"
    return ["py", "-m", "svc", flag, value, "--upstream", url], value


def test_spawn_daemon_log_header_never_records_argv_tail_or_env(
    tmp_path, monkeypatch, fake_popen
):
    """argv and env may carry credentials: the durable header names only the
    cwd and the executable."""
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    argv, value = _fake_credential_argv()
    env_value = "env-" + uuid.uuid4().hex
    log_path = tmp_path / "d.log"

    pu.spawn_daemon(argv, cwd=tmp_path, log_path=log_path,
                    env_overrides={"SVC_" + "KEY": env_value})

    (spawned, _), = fake_popen.calls
    assert spawned == argv, "the child still receives its full argv"
    text = log_path.read_text(encoding="utf-8")
    assert value not in text
    assert env_value not in text
    assert "example.invalid" not in text
    assert "--upstream" not in text
    assert len(text.splitlines()) == 1
    assert f"cwd={tmp_path} executable=py" in text


def test_spawn_daemon_log_header_uses_caller_display_label(tmp_path, monkeypatch, fake_popen):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    argv, value = _fake_credential_argv()
    log_path = tmp_path / "d.log"

    pu.spawn_daemon(argv, cwd=tmp_path, log_path=log_path,
                    display="svc serve\n--upstream <redacted>")

    text = log_path.read_text(encoding="utf-8")
    assert value not in text
    assert len(text.splitlines()) == 1, "a display label never breaks the one-line header"
    assert "executable=py display=svc serve --upstream <redacted>" in text


def test_spawn_daemon_breakaway_denied_never_records_argv_tail(
    tmp_path, monkeypatch, fake_popen
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(_access_denied())
    argv, value = _fake_credential_argv()
    log_path = tmp_path / "d.log"

    with pytest.raises(pu.DaemonSpawnError) as info:
        pu.spawn_daemon(argv, cwd=tmp_path, log_path=log_path)

    assert value not in str(info.value)
    assert value not in log_path.read_text(encoding="utf-8")


def test_spawn_daemon_real_child_writes_to_log_from_cwd(tmp_path):
    workdir = tmp_path / "home"
    workdir.mkdir()
    log_path = tmp_path / "daemon.log"
    proc = pu.spawn_daemon(
        [sys.executable, "-c", "import os; print('cwd=' + os.getcwd())"],
        cwd=workdir,
        log_path=log_path,
    )
    assert proc.wait(timeout=60) == 0
    text = log_path.read_text(encoding="utf-8", errors="replace")
    assert "spawn_daemon cwd=" in text
    reported = text.splitlines()[-1].strip().removeprefix("cwd=")
    assert os.path.samefile(reported, workdir)


@pytest.mark.parametrize(
    "argv", ["python", b"python", Path("python")], ids=["str", "bytes", "path"],
)
def test_spawn_daemon_rejects_a_bare_string_or_path_argv(tmp_path, fake_popen, argv):
    # A str is itself a Sequence[str]: iterating it would launch "p" as the
    # executable with the remaining characters as its arguments.
    with pytest.raises(TypeError, match="sequence of arguments"):
        pu.spawn_daemon(argv, cwd=tmp_path, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []
    assert not (tmp_path / "d.log").exists()


def test_spawn_daemon_rejects_an_empty_argv(tmp_path, fake_popen):
    with pytest.raises(ValueError, match="non-empty argv"):
        pu.spawn_daemon([], cwd=tmp_path, log_path=tmp_path / "d.log")
    assert fake_popen.calls == []


# Every flag spawn_daemon's windowless/lifetime contract owns, plus other
# console, session, Job, debug and suspend flags a caller must not add.
_CONTRACT_OWNED_FLAGS = {
    "CREATE_NEW_CONSOLE": 0x00000010,
    "DETACHED_PROCESS": 0x00000008,
    "CREATE_NO_WINDOW": 0x08000000,
    "CREATE_NEW_PROCESS_GROUP": 0x00000200,
    "CREATE_BREAKAWAY_FROM_JOB": 0x01000000,
    "CREATE_SUSPENDED": 0x00000004,
    "DEBUG_PROCESS": 0x00000001,
    "DEBUG_ONLY_THIS_PROCESS": 0x00000002,
    "CREATE_PRESERVE_CODE_AUTHZ_LEVEL": 0x02000000,
    "CREATE_PROTECTED_PROCESS": 0x00040000,
    "EXTENDED_STARTUPINFO_PRESENT": 0x00080000,
    "CREATE_SECURE_PROCESS": 0x00400000,
    "INHERIT_PARENT_AFFINITY": 0x00010000,
}


@pytest.mark.parametrize("console_children", [False, True], ids=["detached", "console-children"])
@pytest.mark.parametrize(
    "flag", list(_CONTRACT_OWNED_FLAGS.values()), ids=list(_CONTRACT_OWNED_FLAGS),
)
def test_spawn_daemon_rejects_contract_owned_creationflags(
    tmp_path, monkeypatch, fake_popen, flag, console_children
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    with pytest.raises(ValueError, match="extra_creationflags"):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "d.log",
            console_children=console_children, extra_creationflags=flag,
        )
    assert fake_popen.calls == []


def test_spawn_daemon_rejects_owned_flag_mixed_with_a_priority_class(
    tmp_path, monkeypatch, fake_popen
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    with pytest.raises(ValueError, match="0x10 are not permitted"):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "d.log",
            console_children=True, extra_creationflags=0x00004000 | 0x00000010,
        )
    assert fake_popen.calls == []


def test_spawn_daemon_rejects_owned_creationflags_off_windows_too(
    tmp_path, monkeypatch, fake_popen
):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    with pytest.raises(ValueError, match="extra_creationflags"):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "d.log",
            extra_creationflags=0x01000000,
        )
    assert fake_popen.calls == []


@pytest.mark.parametrize("flags", [True, "0x20", 0x20 * 1.0])
def test_spawn_daemon_rejects_non_int_creationflags(tmp_path, fake_popen, flags):
    with pytest.raises(TypeError, match="must be an int"):
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log",
                        extra_creationflags=flags)
    assert fake_popen.calls == []


@pytest.mark.parametrize(
    "flag",
    [0x00000020, 0x00000040, 0x00000080, 0x00004000, 0x00008000, 0x04000000,
     0x00004000 | 0x04000000],
    ids=["normal", "idle", "high", "below-normal", "above-normal",
         "default-error-mode", "below-normal+default-error-mode"],
)
def test_spawn_daemon_passes_allowed_creationflags_through(
    tmp_path, monkeypatch, fake_popen, flag
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log", extra_creationflags=flag)
    (_, kwargs), = fake_popen.calls
    assert kwargs["creationflags"] == (
        pu._DETACHED_PROCESS | pu._CREATE_NEW_PROCESS_GROUP | pu._CREATE_BREAKAWAY_FROM_JOB | flag
    )


def test_spawn_daemon_allowed_flags_keep_contained_mode_contained(
    tmp_path, monkeypatch, fake_popen
):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "d.log",
                    console_children=True, extra_creationflags=0x00004000)
    (_, kwargs), = fake_popen.calls
    assert kwargs["creationflags"] == pu._CREATE_NO_WINDOW | 0x00004000


def test_spawn_daemon_rejects_log_path_inside_payload(tmp_path, fake_popen):
    # The daemon holds its log open for life: inside a payload it would pin
    # the payload directory exactly like a payload cwd.
    payload = tmp_path / "installed-plugins" / "market" / "plugin"
    log_path = payload / "logs" / "d.log"
    with pytest.raises(ValueError, match="log_path is inside a plugin payload"):
        pu.spawn_daemon(["x"], cwd=tmp_path, log_path=log_path)
    assert fake_popen.calls == []
    assert not payload.exists()


def test_spawn_daemon_rejects_log_path_that_resolves_into_payload(
    tmp_path, monkeypatch, fake_popen
):
    payload, link = _payload_and_link(tmp_path)
    link.mkdir()
    log_path = link / "d.log"
    real = pu.os.path.realpath
    monkeypatch.setattr(
        pu.os.path, "realpath",
        lambda p, *a, **k: (
            str(payload / "d.log") if os.fspath(p) == str(log_path) else real(p, *a, **k)
        ),
    )
    with pytest.raises(ValueError, match="log_path is inside a plugin payload"):
        pu.spawn_daemon(["x"], cwd=tmp_path, log_path=log_path)
    assert fake_popen.calls == []
