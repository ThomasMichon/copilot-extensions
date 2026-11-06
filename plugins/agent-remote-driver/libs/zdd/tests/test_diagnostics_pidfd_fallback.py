"""Portable Python builds still use identity-bound native process handles."""

from __future__ import annotations

import os
import signal
import sys

import pytest

from zdd import diagnostic_exit, diagnostics, singleton_linux


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="native Linux pidfd adapter")


def test_diagnostics_reuses_libc_adapter_without_python_pidfd_exports(monkeypatch):
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "expected-start")
    calls = []
    monkeypatch.setattr(singleton_linux, "_open_pidfd", lambda pid: calls.append(("open", pid)) or 123)
    monkeypatch.setattr(
        singleton_linux, "_send_pidfd_signal",
        lambda fd, number: calls.append(("signal", fd, number)),
    )
    monkeypatch.setattr(os, "close", lambda fd: calls.append(("close", fd)))
    result = diagnostics._terminate_posix_if_identity(456, "expected-start")
    assert result["killed"] and result["identity_verified"]
    assert calls == [("open", 456), ("signal", 123, signal.SIGTERM), ("close", 123)]


def test_diagnostics_refuses_when_both_python_and_libc_pidfd_are_unavailable(monkeypatch):
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)

    def unavailable(pid):
        raise NotImplementedError("fixture native handle unavailable")

    monkeypatch.setattr(singleton_linux, "_open_pidfd", unavailable)
    result = diagnostics._terminate_posix_if_identity(456, "expected-start")
    assert result == {
        "killed": False, "identity_verified": False, "method": "pidfd-unavailable"
    }


@pytest.mark.parametrize("exited", [False, True])
def test_liveness_reuses_libc_exit_evidence_without_python_pidfd(monkeypatch, exited):
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    calls = []
    monkeypatch.setattr(
        singleton_linux, "_open_pidfd", lambda pid: calls.append(("open", pid)) or 123,
    )
    monkeypatch.setattr(
        diagnostic_exit, "wait_posix", lambda fd, timeout: exited,
    )
    monkeypatch.setattr(os, "close", lambda fd: calls.append(("close", fd)))
    monkeypatch.setattr(os, "kill", lambda *_: pytest.fail("native exit evidence was bypassed"))
    assert diagnostics._pid_alive(456) is (not exited)
    assert calls == [("open", 456), ("close", 123)]


@pytest.mark.parametrize("error,alive", [
    (NotImplementedError(), True), (OSError(), True), (ProcessLookupError(), False),
])
def test_liveness_libc_failure_preserves_conservative_fallback(monkeypatch, error, alive):
    monkeypatch.delattr(os, "pidfd_open", raising=False)

    def unavailable(pid):
        raise error

    monkeypatch.setattr(singleton_linux, "_open_pidfd", unavailable)
    calls = []
    monkeypatch.setattr(os, "kill", lambda pid, number: calls.append((pid, number)))
    assert diagnostics._pid_alive(456) is alive
    assert calls == ([(456, 0)] if alive else [])
