"""Atomic spawn wiring through synthetic native calls and actual owned file fds."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _windows_caller_owned as native
from agent_procutil.owned_process import OwnedCleanupError


@pytest.mark.parametrize("thread_close", [True, False])
def test_log_fds_are_duplicated_and_only_unique_stdio_handles_are_inherited(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, thread_close: bool,
) -> None:
    kernel = Mock()
    kernel.CreateJobObjectW.return_value = 300
    kernel.SetInformationJobObject.return_value = True
    kernel.InitializeProcThreadAttributeList.side_effect = (
        lambda attributes, count, flags, size: setattr(size._obj, "value", 128) or True
    )
    kernel.UpdateProcThreadAttribute.return_value = True
    kernel.ResumeThread.return_value = 1
    kernel.CloseHandle.side_effect = lambda handle: handle != 502 or thread_close
    kernel.WaitForSingleObject.return_value = 0
    kernel.GetExitCodeProcess.side_effect = lambda handle, code: setattr(code._obj, "value", 0) or True
    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(native.ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(native.ctypes, "WinError", lambda *args: OSError("native close failed"), raising=False)
    monkeypatch.setattr(native.os, "set_handle_inheritable", lambda *args: None, raising=False)
    monkeypatch.setattr(native.os, "O_BINARY", 0, raising=False)
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: fd))
    job = SimpleNamespace(handle=300, close=Mock(), terminate_and_wait=Mock())
    monkeypatch.setattr(native, "JobHandle", Mock(return_value=job))
    monkeypatch.setenv("COPILOT_EXTENSIONS_OWNED_JOB_SCOPES", r"Local\ce-owned-job-" + "f" * 32)
    observed = {}

    def create(application, command, pa, ta, inherit, flags, environment, cwd, startup, process):
        assert inherit is True and flags == 0x08080404
        stdio = startup._obj.StartupInfo
        assert stdio.hStdOutput == stdio.hStdError
        assert stdio.hStdInput != stdio.hStdOutput
        observed["output"] = stdio.hStdOutput
        observed["environment"] = ctypes.wstring_at(environment, len(environment))
        os.write(stdio.hStdOutput, b"both-streams")
        process._obj.hProcess, process._obj.hThread = 501, 502
        process._obj.dwProcessId = 503
        return True

    kernel.CreateProcessW.side_effect = create
    logfile = tmp_path / "append.log"
    logfile.write_bytes(b"before:")
    with logfile.open("ab", buffering=0) as output:
        original_fd = output.fileno()
        inherited_before = os.get_inheritable(original_fd)
        def spawn():
            return native.spawn(
                ["fixture.exe", "", "a b"], cwd=str(tmp_path), env={"PATH": "fixture"},
                inherit_job_scopes=False, allow_breakaway=False,
                stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
            )

        if not thread_close:
            with pytest.raises(OwnedCleanupError) as failure:
                spawn()
            assert failure.value.job is job
            assert failure.value.process._thread_handle == 502
            assert failure.value.process._handle == 501
            job.terminate_and_wait.assert_called_once_with(2.0)
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
