"""Actor-free shell lifetime framing and admission controls."""

from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

import pytest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "libs/agent-procutil/src/agent_procutil"
NONCE = "a" * 32


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, SOURCE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _native_imports():
    # Native bindings are dependency doubles, never framework-preloaded DLLs.
    # Separate native tests exercise the real ctypes layout and kernel calls.
    native = ModuleType("ctypes")
    native.wintypes = SimpleNamespace(
        HANDLE=lambda: SimpleNamespace(value=None),
        ULONG=lambda: SimpleNamespace(value=0),
    )
    native.byref = lambda value: SimpleNamespace(_obj=value)
    native.sizeof = lambda value: 144
    native.WinError = lambda *args: OSError("native failure")
    native.get_last_error = lambda: 5
    package = ModuleType("agent_procutil")
    package._is_windows = lambda: True
    package._build_job_extended_limit_info = lambda: lambda: SimpleNamespace(
        BasicLimitInformation=SimpleNamespace(LimitFlags=0),
    )
    identity = ModuleType("agent_procutil._daemon_instance_identity")
    identity._windows_birth = lambda handle: "windows:13"
    child = ModuleType("agent_procutil._windows_caller_owned")
    child._api = lambda: pytest.fail("unmocked native API access")
    with patch.dict(sys.modules, {
        "ctypes": native, "agent_procutil": package,
        "agent_procutil._daemon_instance_identity": identity,
        "agent_procutil._windows_caller_owned": child,
    }):
        yield package


owner = _load("unit_shell_caller_owner", "_shell_caller_owned.py")
with _native_imports():
    parent_job = _load("unit_shell_parent_job", "_windows_parent_job.py")


@pytest.mark.parametrize("code", [0, 1, 7, 255])
def test_callback_preserves_exact_exit_codes(code):
    assert owner._callback_code(lambda: code) == code


@pytest.mark.parametrize("code", [False, None, "0", -1, 256])
def test_invalid_callback_results_are_explicit_failures(code, capsys):
    assert owner._callback_code(lambda: code) == 1
    assert "invalid exit code" in capsys.readouterr().err


def test_callback_exception_is_reported_without_success(capsys):
    def broken():
        raise RuntimeError("controlled callback failure")

    assert owner._callback_code(broken) == 1
    assert "controlled callback failure" in capsys.readouterr().err


@pytest.fixture
def windows(monkeypatch):
    calls = []
    incoming = io.BytesIO((NONCE + "\n").encode())
    monkeypatch.setattr(owner, "os", SimpleNamespace(
        name="nt", read=lambda descriptor, size: incoming.read(size),
    ))
    monkeypatch.setattr(owner.sys, "stdin", SimpleNamespace(fileno=lambda: 55))
    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(get_osfhandle=lambda fd: 505))
    monkeypatch.setitem(sys.modules, "agent_procutil", ModuleType("agent_procutil"))
    monkeypatch.setitem(sys.modules, "agent_procutil._windows_parent_job", parent_job)
    monkeypatch.setattr(owner.threading, "active_count", lambda: 1)

    def transfer(**kwargs):
        calls.append(("transfer", kwargs))
        return parent_job.ParentJob(12, "windows:13", 14, "windows:15", 303)

    monkeypatch.setattr(parent_job, "transfer_lifetime_to_pipe_server", transfer)
    return SimpleNamespace(calls=calls, incoming=incoming)


def test_adopted_pipe_job_precedes_ack_and_application(windows, capsys):
    def callback():
        before = capsys.readouterr()
        receipt = json.loads(before.out)
        assert receipt == {
            "version": 1, "nonce": NONCE, "owner_pid": 12, "scope": "windows-job",
            "job": {
                "parent_pid": 12, "parent_birth": "windows:13", "worker_pid": 14,
                "worker_birth": "windows:15", "parent_handle": 303,
            },
        }
        assert windows.incoming.tell() == 33
        windows.calls.append(("callback",))
        print("application output")
        return 7

    assert owner.serve(callback, owner_pid=12, owner_birth="windows:13", nonce=NONCE) == 7
    assert windows.calls == [
        ("transfer", {"parent_pid": 12, "parent_birth": "windows:13", "pipe_handle": 505}),
        ("callback",),
    ]
    assert capsys.readouterr().out == f"application output\n\n__COPILOT_CALLER_EXIT_{NONCE}=7\n"


@pytest.mark.parametrize("ack", [b"", b"wrong\n", b"a" * 31, b"a" * 32 + b"\r\n"])
def test_missing_or_changed_ack_never_starts_application(windows, capsys, ack):
    windows.incoming = io.BytesIO(ack)
    owner.os.read = lambda fd, size: windows.incoming.read(size)

    def forbidden():
        pytest.fail("unacknowledged caller reached application work")

    with pytest.raises(RuntimeError, match="did not acknowledge"):
        owner.serve(forbidden, owner_pid=12, owner_birth="windows:13", nonce=NONCE)
    assert len(windows.calls) == 1
    assert "__COPILOT_CALLER_EXIT_" not in capsys.readouterr().out


@pytest.mark.parametrize("pid", [False, 0, -1, 1 << 32, "12", None])
def test_invalid_caller_never_attempts_lifetime_transfer(windows, pid):
    with pytest.raises(ValueError, match="original caller"):
        owner.serve(lambda: 0, owner_pid=pid, owner_birth="windows:13", nonce=NONCE)
    assert windows.calls == []


@pytest.mark.parametrize("nonce", [None, "", "a" * 31, "A" * 32, "z" * 32])
def test_invalid_correlation_never_attempts_lifetime_transfer(windows, nonce):
    with pytest.raises(ValueError, match="correlated request"):
        owner.serve(lambda: 0, owner_pid=12, owner_birth="windows:13", nonce=nonce)
    assert windows.calls == []


def test_native_transfer_failure_never_admits_or_starts_application(windows, monkeypatch, capsys):
    def failed(**kwargs):
        raise OSError("controlled Job transfer failure")

    monkeypatch.setattr(parent_job, "transfer_lifetime_to_pipe_server", failed)
    with pytest.raises(OSError, match="controlled Job transfer"):
        owner.serve(lambda: pytest.fail("failed Job reached application"), owner_pid=12,
                    owner_birth="windows:13", nonce=NONCE)
    assert not capsys.readouterr().out


@pytest.mark.parametrize("failure", ["", "birth", "pipe", "duplicate", "assign"])
def test_real_pipe_transfer_core_retains_one_original_owner(monkeypatch, failure):
    calls = []
    parent_pid = os.getpid() + 10000

    def result(name, value=1):
        calls.append(name)
        return value

    def pipe(handle, output):
        assert handle == 505
        output._obj.value = parent_pid if failure != "pipe" else parent_pid + 1
        return result("pipe")

    def duplicate(source, handle, target, output, rights, inherit, options):
        assert not inherit
        output._obj.value = 404 if options & 1 else 303
        return result("reclaim" if options & 1 else "duplicate", failure != "duplicate")

    kernel = SimpleNamespace(
        OpenProcess=lambda rights, inherit, pid: result("open", 101),
        GetCurrentProcess=lambda: -1,
        GetNamedPipeServerProcessId=pipe,
        WaitForSingleObject=lambda handle, timeout: result("live", 0x102),
        CreateJobObjectW=lambda security, name: result("create", 202),
        SetInformationJobObject=lambda *args: result("limits"),
        DuplicateHandle=duplicate,
        AssignProcessToJobObject=lambda *args: result("assign", failure != "assign"),
        CloseHandle=lambda handle: result("close"),
    )
    monkeypatch.setattr(parent_job, "_api", lambda: kernel)
    monkeypatch.setattr(parent_job, "_is_windows", lambda: True)
    monkeypatch.setattr(parent_job, "_transferred", False)
    monkeypatch.setattr(parent_job.threading, "active_count", lambda: 1)
    monkeypatch.setattr(parent_job, "_windows_birth", lambda handle: (
        "windows:13" if handle == 101 and failure != "birth" else "windows:15"
    ))
    monkeypatch.setattr(parent_job.ctypes, "WinError", lambda *args: OSError("native failure"), raising=False)
    monkeypatch.setattr(parent_job.ctypes, "get_last_error", lambda: 5, raising=False)
    if failure:
        with pytest.raises((ProcessLookupError, OSError)):
            parent_job.transfer_lifetime_to_pipe_server(
                parent_pid=parent_pid, parent_birth="windows:13", pipe_handle=505,
            )
        if failure in ("birth", "pipe"):
            assert "create" not in calls
        if failure == "assign":
            assert "reclaim" in calls
    else:
        receipt = parent_job.transfer_lifetime_to_pipe_server(
            parent_pid=parent_pid, parent_birth="windows:13", pipe_handle=505,
        )
        assert receipt.parent_pid == parent_pid and receipt.parent_handle == 303
        assert calls == [
            "open", "pipe", "live", "create", "limits", "duplicate",
            "live", "assign", "close", "close",
        ]
