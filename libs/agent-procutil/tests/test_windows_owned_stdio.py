"""Atomic spawn wiring through synthetic native calls and actual owned file fds."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import TypedDict, cast
from unittest.mock import Mock

import pytest

from agent_procutil import JobHandle, _windows_caller_owned as native
from agent_procutil.owned_process import OwnedCleanupError


class _Observed(TypedDict, total=False):
    output: int
    environment: str


def test_inherited_console_requires_current_attachment_before_create(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 300
    kernel.SetInformationJobObject.return_value = True
    kernel.GetConsoleMode.return_value = True
    kernel.GetConsoleProcessList.return_value = 0
    kernel.CloseHandle.return_value = True
    jobs: list[JobHandle] = []

    def job(handle: int, *, name: str | None = None) -> JobHandle:
        owner = JobHandle(handle, name=name)
        jobs.append(owner)
        return owner

    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(native, "JobHandle", job)
    monkeypatch.setattr(os, "O_BINARY", 0, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    with (tmp_path / "input").open("w+b") as source, pytest.raises(
        OSError, match="attached caller console",
    ):
        native.spawn(
            ["git"], cleanup_deadline=time.monotonic() + 10, stdin=source,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
    kernel.CreateProcessW.assert_not_called()
    assert jobs and all(owner.handle is None for owner in jobs)


@pytest.mark.parametrize("thread_close", [True, False])
def test_log_fds_are_duplicated_and_only_unique_stdio_handles_are_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thread_close: bool,
) -> None:
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 300
    kernel.SetInformationJobObject.return_value = True
    def initialize(attributes: object, count: int, flags: int, size: object) -> bool:
        ctypes.cast(cast(ctypes.c_void_p, size), ctypes.POINTER(ctypes.c_size_t)).contents.value = 128
        return True

    def exit_code(handle: int, code: object) -> bool:
        ctypes.cast(cast(ctypes.c_void_p, code), ctypes.POINTER(wintypes.DWORD)).contents.value = 0
        return True

    kernel.InitializeProcThreadAttributeList.side_effect = initialize
    kernel.UpdateProcThreadAttribute.return_value = True
    kernel.ResumeThread.return_value = 1
    kernel.GetConsoleMode.return_value = False
    kernel.CloseHandle.side_effect = lambda handle: handle != 502 or thread_close
    kernel.WaitForSingleObject.return_value = 0
    kernel.GetExitCodeProcess.side_effect = exit_code
    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda *args: OSError("native close failed"), raising=False)
    monkeypatch.setattr(os, "set_handle_inheritable", lambda *args: None, raising=False)
    monkeypatch.setattr(os, "O_BINARY", 0, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    job_mock = Mock(spec=JobHandle, handle=300, close=Mock(), terminate_and_wait=Mock())
    job = cast(JobHandle, job_mock)
    monkeypatch.setattr(native, "JobHandle", Mock(return_value=job))
    monkeypatch.setenv("COPILOT_EXTENSIONS_OWNED_JOB_SCOPES", r"Local\ce-owned-job-" + "f" * 32)
    observed: _Observed = {}

    def create(
        application: str | None, command: ctypes.Array[ctypes.c_wchar],
        pa: object, ta: object, inherit: bool, flags: int,
        environment: ctypes.Array[ctypes.c_wchar], cwd: str | None,
        startup: object, process: object,
    ) -> bool:
        assert inherit is True and flags == 0x08080404
        stdio = ctypes.cast(cast(ctypes.c_void_p, startup), ctypes.POINTER(native._StartupInfoEx)).contents.StartupInfo
        assert stdio.hStdOutput == stdio.hStdError
        assert stdio.hStdInput != stdio.hStdOutput
        observed["output"] = stdio.hStdOutput
        observed["environment"] = ctypes.wstring_at(environment, len(environment))
        os.write(stdio.hStdOutput, b"both-streams")
        process_info = ctypes.cast(cast(ctypes.c_void_p, process), ctypes.POINTER(native._ProcessInfo)).contents
        process_info.hProcess, process_info.hThread = 501, 502
        process_info.dwProcessId = 503
        return True

    kernel.CreateProcessW.side_effect = create
    logfile = tmp_path / "append.log"
    logfile.write_bytes(b"before:")
    with logfile.open("ab", buffering=0) as output:
        original_fd = output.fileno()
        inherited_before = os.get_inheritable(original_fd)
        def spawn() -> tuple[native._Process, JobHandle]:
            return native.spawn(
                ["fixture.exe", "", "a b"], cwd=str(tmp_path), env={"PATH": "fixture"},
                inherit_job_scopes=False, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            )

        if not thread_close:
            with pytest.raises(OwnedCleanupError) as failure:
                spawn()
            assert failure.value.job is job
            assert isinstance(failure.value.process, native._Process)
            assert failure.value.process._thread_handle == 502
            assert failure.value.process._handle == 501
            job_mock.terminate_and_wait.assert_called_once_with(2.0)
            assert kernel.CloseHandle.call_args_list.count(((502,), {})) == 1
            return
        process, actual_job = spawn()
        assert actual_job is job
        assert observed["output"] != original_fd
        assert os.get_inheritable(original_fd) is inherited_before
        assert output.closed is False
        attributes = {call.args[2]: call.args[3] for call in kernel.UpdateProcThreadAttribute.call_args_list}
        assert len(attributes[0x20002]) == 2
        assert len(set(attributes[0x20002])) == 2
        assert list(attributes[0x2000D]) == [300]
        assert "f" * 32 not in observed["environment"]
        assert observed["environment"].count("ce-owned-job-") == 1
        with pytest.raises(OSError):
            os.fstat(observed["output"])
        process.close()
    assert logfile.read_bytes() == b"before:both-streams"
