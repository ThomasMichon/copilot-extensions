"""Tests for the shared headless/detached process-spawn kwargs."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import AsyncMock

import pytest

import agent_procutil as pu


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
    def __init__(self, calls):
        self.calls = calls
        self.NtResumeProcess = _FakeWinFunc("NtResumeProcess", self._resume_process)

    def _resume_process(self, process):
        self.calls.append(("NtResumeProcess", process))
        return 0


class _FakeProcess:
    pid = 12345

    def __init__(self):
        self.killed = False

    def kill(self):
        self.killed = True


def test_bind_to_kill_on_close_job_noop_off_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.bind_to_kill_on_close_job(12345) is None


def test_bind_to_kill_on_close_job_assigns_pid_to_job(monkeypatch):
    fake = _FakeKernel32()
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)

    job = pu.bind_to_kill_on_close_job(12345)

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
    assert fake.limit_flags == pu._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
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
def test_bind_to_kill_on_close_job_failures_return_none_and_close_handles(
    monkeypatch, failure, closed_handles
):
    fake = _FakeKernel32()
    fake.fail = failure
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setattr(pu, "_kernel32", lambda: fake)
    monkeypatch.setattr(pu.ctypes, "get_last_error", lambda: 5, raising=False)  # Windows-only in ctypes

    assert pu.bind_to_kill_on_close_job(12345) is None
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


def _windows_pid_alive(pid: int) -> bool:
    from ctypes import wintypes

    process = pu.ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return False
    try:
        code = wintypes.DWORD()
        if not pu.ctypes.windll.kernel32.GetExitCodeProcess(
            process, pu.ctypes.byref(code)
        ):
            return False
        return code.value == 259
    finally:
        pu.ctypes.windll.kernel32.CloseHandle(process)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_job_kills_grandchild_when_owner_is_hard_killed():
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    child_code = r"""
import subprocess
import sys
import time

from agent_procutil import bind_to_kill_on_close_job

grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
job = bind_to_kill_on_close_job(grandchild.pid)
if job is None:
    raise SystemExit("failed to bind grandchild to job")
print(grandchild.pid, flush=True)
time.sleep(60)
"""
    child = subprocess.Popen(
        [sys.executable, "-c", child_code],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    grandchild_pid = int(child.stdout.readline().strip())
    try:
        child.kill()
        child.wait(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _windows_pid_alive(grandchild_pid):
            time.sleep(0.1)
        assert not _windows_pid_alive(grandchild_pid)
    finally:
        if child.poll() is None:
            child.kill()
        if _windows_pid_alive(grandchild_pid):
            subprocess.run(
                ["taskkill", "/PID", str(grandchild_pid), "/T", "/F"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Job Object integration")
def test_windows_spawn_in_kill_on_close_job_child_starts_inside_job():
    src = Path(__file__).resolve().parents[1] / "src"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(src) + os.pathsep + env.get("PYTHONPATH", "")
    child_code = r"""
import ctypes
from ctypes import wintypes

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetCurrentProcess.restype = wintypes.HANDLE
kernel32.IsProcessInJob.argtypes = [
    wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)
]
in_job = wintypes.BOOL()
if not kernel32.IsProcessInJob(kernel32.GetCurrentProcess(), None, ctypes.byref(in_job)):
    raise ctypes.WinError(ctypes.get_last_error())
print("1" if in_job.value else "0", flush=True)
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
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=10)
            assert process.returncode == 0, stderr.decode(errors="replace")
            assert stdout.decode().strip() == "1"
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            if job is not None:
                job.close()

    asyncio.run(scenario())
