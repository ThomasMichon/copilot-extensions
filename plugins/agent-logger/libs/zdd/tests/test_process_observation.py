"""Crash observation never turns unknown native state into exit authority."""

from __future__ import annotations

import io
import os
import subprocess
import sys

import pytest

from zdd import diagnostics, process_exit, singleton_linux
from .test_process_exit import _windows_kernel


@pytest.mark.parametrize("token,exited,result", [("456", False, False), ("456", True, True), ("123", False, True)])
def test_windows_observation_uses_query_wait_handle_without_signaling(monkeypatch, token, exited, result):
    calls = _windows_kernel(monkeypatch, exited=exited)
    assert process_exit._observe_windows(123, token) is result
    assert calls[0] == ("open", 0x1000 | 0x00100000, False, 123)
    assert calls[-1] == ("close", 99333)
    assert all(call[0] in {"open", "query", "wait", "close"} for call in calls)


@pytest.mark.parametrize("code,result", [(87, True), (5, None), (50, None)])
def test_windows_only_open_process_nonexistence_is_absence(monkeypatch, code, result):
    import ctypes

    calls = _windows_kernel(monkeypatch, open_ok=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: code)
    if result is None:
        with pytest.raises(OSError):
            process_exit._observe_windows(123, "456")
    else:
        assert process_exit._observe_windows(123, "456") is True
    assert not any(call[0] == "close" for call in calls)


def test_windows_query_invalid_parameter_is_not_process_absence(monkeypatch):
    import ctypes

    calls = _windows_kernel(monkeypatch, query_ok=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 87)
    with pytest.raises(OSError):
        process_exit._observe_windows(123, "456")
    assert calls[-1] == ("close", 99333)


@pytest.mark.parametrize("error,result", [(ProcessLookupError(), True), (PermissionError(), None), (NotImplementedError(), None)])
def test_linux_open_distinguishes_native_absence_from_denied_or_unsupported(monkeypatch, error, result):
    def fail(_):
        raise error

    monkeypatch.setattr(singleton_linux, "_open_pidfd", fail)
    if result is None:
        with pytest.raises(type(error)):
            process_exit._observe_linux(123, "456")
    else:
        assert process_exit._observe_linux(123, "456") is True


@pytest.mark.parametrize("evidence", ["same", "reused", "denied", "missing", "malformed", "oversized", "unicode-name", "missing-exited"])
def test_linux_birth_read_is_strict_and_closes_exact_reference(monkeypatch, evidence):
    closed = []
    monkeypatch.setattr(singleton_linux, "_open_pidfd", lambda _: 12345)
    monkeypatch.setattr(process_exit.os, "close", closed.append)
    waits = iter([False, evidence == "missing-exited"])
    monkeypatch.setattr(process_exit.diagnostic_exit, "wait_posix", lambda *_: next(waits))

    def read(_path, *_args, **_kwargs):
        if evidence == "denied":
            raise PermissionError("test-owned denied proc read")
        if evidence in {"missing", "missing-exited"}:
            raise FileNotFoundError("test-owned missing proc read")
        if evidence == "malformed":
            return io.BytesIO(b"unverifiable")
        if evidence == "oversized":
            return io.BytesIO(b"x" * 65_537)
        name = b"\xe2\x98\x83)" if evidence == "unicode-name" else b"test-owned"
        token = b"789" if evidence == "reused" else b"456"
        return io.BytesIO(b"123 (" + name + b") " + b" ".join([b"S"] + [b"0"] * 18 + [token]))

    monkeypatch.setattr(process_exit.Path, "open", read)
    if evidence in {"denied", "missing", "malformed", "oversized"}:
        with pytest.raises((PermissionError, ProcessLookupError, ValueError)):
            process_exit._observe_linux(123, "456")
    else:
        assert process_exit._observe_linux(123, "456") is (
            evidence in {"reused", "missing-exited"}
        )
    assert closed == [12345]


@pytest.mark.parametrize("pid,token", [(True, "1"), (0, "1"), (2**32, "1"), (1, None), (1, "\u0661")])
def test_invalid_observation_never_opens_a_native_process(monkeypatch, pid, token):
    monkeypatch.setattr(
        process_exit, "_observe_windows", lambda *_: pytest.fail("invalid identity"),
    )
    monkeypatch.setattr(
        process_exit, "_observe_linux", lambda *_: pytest.fail("invalid identity"),
    )
    with pytest.raises(ValueError, match="identity"):
        process_exit.observe_original_exit(pid, token)


def test_unsupported_observation_never_uses_tolerant_diagnostics(monkeypatch):
    monkeypatch.setattr(process_exit.sys, "platform", "darwin")
    monkeypatch.setattr(
        diagnostics, "process_start_time", lambda *_: pytest.fail("no tolerant birth lookup"),
    )
    with pytest.raises(NotImplementedError, match="native"):
        process_exit.observe_original_exit(123, "456")


@pytest.mark.skipif(sys.platform not in {"linux", "win32"}, reason="native original-exit observation")
def test_real_crash_observation_requires_no_precaptured_reference_or_late_pid_signal(monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('ready', flush=True); sys.stdin.buffer.read()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
    )
    try:
        assert child.stdout.readline().strip() == "ready"
        token = diagnostics.process_start_time(child.pid)
        assert token is not None
        monkeypatch.setattr(
            diagnostics, "process_start_time", lambda *_: pytest.fail("no tolerant birth lookup"),
        )
        assert process_exit.observe_original_exit(child.pid, token) is False
        # A different captured birth identifies an original that no longer occupies
        # this PID. This is evidence only, never permission to stop the live child.
        assert process_exit.observe_original_exit(child.pid, token + "1") is True
        assert child.poll() is None
        child.stdin.close()
        child.wait(timeout=5)
        assert process_exit.observe_original_exit(child.pid, token) is True
    finally:
        if child.poll() is None:
            child.stdin.close()
            child.wait(timeout=5)
        child.stdout.close()
