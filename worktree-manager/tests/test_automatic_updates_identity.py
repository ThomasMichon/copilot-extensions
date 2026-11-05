"""Qualification signals require the original authenticated kernel birth."""

from __future__ import annotations

import importlib
import signal
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_automatic_updates_native import windows_probe as windows_probe


@pytest.mark.parametrize("birth", ["matching", "reused", "missing"])
def test_windows_control_pin_never_terminates_a_reused_pid(
    windows_probe: tuple, monkeypatch: pytest.MonkeyPatch, birth: str,
) -> None:
    from agent_procutil import _daemon_instance_identity

    identity = importlib.import_module("identity")
    kernel = SimpleNamespace(
        OpenProcess=Mock(return_value=901),
        WaitForSingleObject=Mock(return_value=258),
        CloseHandle=Mock(return_value=1),
        TerminateProcess=Mock(return_value=1),
    )
    monkeypatch.setattr(identity, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(identity.ctypes, "WinDLL", lambda *a, **k: kernel, raising=False)
    expected = None if birth == "missing" else "windows:200"
    observed = "windows:201" if birth == "reused" else "windows:200"
    monkeypatch.setattr(_daemon_instance_identity, "_windows_birth", lambda handle: observed)
    if birth != "matching":
        with pytest.raises(RuntimeError, match="original creation identity|identity changed"):
            identity.ProcessPin(123, terminate_control=True, expected_birth=expected)
        kernel.TerminateProcess.assert_not_called()
        if birth == "missing":
            kernel.OpenProcess.assert_not_called()
            kernel.CloseHandle.assert_not_called()
        else:
            kernel.CloseHandle.assert_called_once_with(901)
        return
    pin = identity.ProcessPin(123, terminate_control=True, expected_birth=expected)
    try:
        assert pin.terminate_control is True and pin.identity_verified is True
        pin.stop_contained_control()
        kernel.TerminateProcess.assert_called_once_with(901, 1)
    finally:
        pin.close()
    kernel.CloseHandle.assert_called_once_with(901)


@pytest.mark.parametrize("birth", ["matching", "reused", "missing", "already-exited"])
def test_linux_signal_pin_requires_authenticated_birth_and_live_kernel_custody(
    windows_probe: tuple, monkeypatch: pytest.MonkeyPatch, birth: str,
) -> None:
    from agent_procutil import _caller_owned, _daemon_instance_identity, _linux_pidfd

    identity = importlib.import_module("identity")
    closed = []
    acquire, send = Mock(return_value=91), Mock()
    monkeypatch.setattr(identity, "os", SimpleNamespace(name="posix", close=closed.append))
    monkeypatch.setattr(identity, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(_linux_pidfd, "open_pidfd", acquire)
    monkeypatch.setattr(_linux_pidfd, "send_pidfd_signal", send, raising=False)
    monkeypatch.setattr(_caller_owned, "_pidfd_exited", lambda fd: birth == "already-exited")
    observed = Mock(return_value="linux:foreign" if birth == "reused" else "linux:expected")
    monkeypatch.setattr(_daemon_instance_identity, "birth_identity", observed)
    expected = None if birth == "missing" else "linux:expected"
    if birth in ("reused", "already-exited"):
        with pytest.raises(RuntimeError, match="identity changed"):
            identity.ProcessPin(123, expected_birth=expected)
        assert closed == [91]
        send.assert_not_called()
        return
    pin = identity.ProcessPin(123, expected_birth=expected)
    try:
        acquire.assert_called_once_with(123)
        if birth == "missing":
            with pytest.raises(RuntimeError, match="original authenticated birth"):
                pin.request_term()
            observed.assert_not_called()
            send.assert_not_called()
        else:
            pin.request_term()
            send.assert_called_once_with(91, signal.SIGTERM)
    finally:
        pin.close()
    assert closed == [91]
