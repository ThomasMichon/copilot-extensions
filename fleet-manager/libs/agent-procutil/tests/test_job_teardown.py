"""Strict owned-Job teardown, separate from the legacy best-effort close."""

from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import agent_procutil as pu


@pytest.fixture
def kernel(monkeypatch):
    calls = []
    active = iter([2, 1, 0])

    def terminate(handle, code):
        calls.append(("terminate", handle, code))
        return 1

    def query(handle, kind, pointer, size, returned):
        calls.append(("query", handle, kind, size))
        ctypes.cast(pointer, ctypes.POINTER(pu._JobBasicAccountingInformation)).contents \
            .ActiveProcesses = next(active)
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        return 1

    def close(handle):
        calls.append(("close", handle))
        return 1

    api = SimpleNamespace(
        TerminateJobObject=Mock(side_effect=terminate),
        QueryInformationJobObject=Mock(side_effect=query),
        CloseHandle=Mock(side_effect=close), calls=calls,
    )
    monkeypatch.setattr(pu, "_kernel32", lambda: api)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(ctypes, "WinError", lambda code: OSError(code, "native failure"),
                        raising=False)
    monkeypatch.setattr(pu.time, "sleep", lambda seconds: None)
    return api


def test_strict_teardown_waits_for_zero_before_close(kernel):
    handle = 0x123456789ABC
    job = pu.JobHandle(handle)
    job.terminate_and_wait(15.0)
    assert job.handle is None
    assert [call[0] for call in kernel.calls] == ["terminate", "query", "query", "query", "close"]
    assert all(call[1] == handle for call in kernel.calls)
    from ctypes import wintypes

    assert kernel.TerminateJobObject.argtypes[0] == wintypes.HANDLE
    assert kernel.QueryInformationJobObject.argtypes[0] == wintypes.HANDLE
    assert kernel.CloseHandle.restype == wintypes.BOOL
    assert ctypes.sizeof(pu._JobBasicAccountingInformation) == 48
    assert kernel.calls[1][2:] == (1, 48)


@pytest.mark.parametrize("operation", ["TerminateJobObject", "QueryInformationJobObject",
                                       "CloseHandle"])
def test_native_failure_retains_exact_handle(kernel, operation):
    getattr(kernel, operation).side_effect = None
    getattr(kernel, operation).return_value = 0
    job = pu.JobHandle(123)
    with pytest.raises(OSError, match="native failure"):
        job.terminate_and_wait(15)
    assert job.handle == 123
    if operation != "CloseHandle":
        assert not kernel.CloseHandle.called
    # Explicitly dispose the fake handle, keeping destructor out of assertions.
    job._handle = None


def test_timeout_retains_handle_and_never_closes(kernel, monkeypatch):
    def query(handle, kind, pointer, size, returned):
        ctypes.cast(pointer, ctypes.POINTER(pu._JobBasicAccountingInformation)).contents \
            .ActiveProcesses = 1
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        return 1

    kernel.QueryInformationJobObject.side_effect = query
    ticks = iter([0.0, 16.0])
    monkeypatch.setattr(pu.time, "monotonic", lambda: next(ticks))
    job = pu.JobHandle(123)
    with pytest.raises(TimeoutError):
        job.terminate_and_wait(15)
    assert job.handle == 123
    assert not kernel.CloseHandle.called
    job._handle = None


def test_bad_accounting_size_retains_handle(kernel):
    kernel.QueryInformationJobObject.side_effect = None
    kernel.QueryInformationJobObject.return_value = 1
    job = pu.JobHandle(123)
    with pytest.raises(RuntimeError, match="structure size"):
        job.terminate_and_wait(15)
    assert job.handle == 123
    assert not kernel.CloseHandle.called
    job._handle = None


def test_already_empty_job_closes_after_single_query(kernel):
    def empty(handle, kind, pointer, size, returned):
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        return 1

    kernel.QueryInformationJobObject.side_effect = empty
    job = pu.JobHandle(123)
    assert job.terminate_and_wait(15.0) is None
    assert job.handle is None
    assert kernel.QueryInformationJobObject.call_count == 1
    assert kernel.CloseHandle.call_count == 1


def test_close_failure_can_retry_retained_handle(kernel):
    def empty(handle, kind, pointer, size, returned):
        ctypes.cast(returned, ctypes.POINTER(ctypes.c_uint32)).contents.value = size
        return 1

    kernel.QueryInformationJobObject.side_effect = empty
    kernel.CloseHandle.side_effect = [0, 1]
    job = pu.JobHandle(123)
    with pytest.raises(OSError):
        job.terminate_and_wait(15.0)
    assert job.handle == 123
    job.terminate_and_wait(15.0)
    assert job.handle is None


@pytest.mark.parametrize("value", [0, -1, math.inf, math.nan, True, "15", None])
def test_timeout_validation_before_native_calls(kernel, value):
    job = pu.JobHandle(123)
    with pytest.raises(ValueError, match="finite and positive"):
        job.terminate_and_wait(value)
    assert not kernel.TerminateJobObject.called
    assert job.handle == 123
    job._handle = None


def test_closed_handle_explicit_failure(kernel):
    with pytest.raises(RuntimeError, match="closed"):
        pu.JobHandle(None).terminate_and_wait(15)
    assert not kernel.TerminateJobObject.called


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job accounting")
def test_real_child_grandchild_strict_job_cleanup(tmp_path):
    receipt = tmp_path / "owned-pids.json"
    script = """
import json,os,subprocess,sys,time
from pathlib import Path
child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(120)'],
    stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
Path(sys.argv[1]).write_text(json.dumps({'parent':os.getpid(),'child':child.pid}))
time.sleep(120)
"""
    process, job = pu.spawn_sync_in_kill_on_close_job(
        [pu.windowless_python(sys.executable), "-c", script, str(receipt)],
        env={**os.environ, **pu.windowless_python_env(sys.executable)},
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        **pu.no_window_kwargs(),
    )
    try:
        assert job is not None, "owned Job assignment must succeed for literal process proof"
        until = time.monotonic() + 15
        while not receipt.exists() and time.monotonic() < until:
            assert process.poll() is None
            time.sleep(0.05)
        assert receipt.exists()
        identity = json.loads(receipt.read_text())
        assert identity["parent"] == process.pid
        kernel32 = pu._kernel32()
        pu._configure_job_teardown_api(kernel32)
        info = pu._JobBasicAccountingInformation()
        returned = ctypes.c_uint32()
        assert kernel32.QueryInformationJobObject(
            job.handle, 1, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(returned),
        )
        assert info.ActiveProcesses >= 2
        job.terminate_and_wait(15.0)
        assert job.handle is None
        process.wait(timeout=15)
        assert process.poll() is not None
    finally:
        if job is not None and job.handle is not None:
            job.terminate_and_wait(15.0)
        elif job is None and process.poll() is None:
            process.terminate()
        process.wait(timeout=15)
