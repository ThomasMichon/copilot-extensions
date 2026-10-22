"""Credential-free protocol and custody checks; never register a host task."""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

import windows_launch_broker_probe as probe


def test_probe_refuses_inspection_outside_authorized_ci(monkeypatch, tmp_path):
    monkeypatch.setattr(probe.sys, "argv", ["probe", "inspect", str(tmp_path), "a" * 32])
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.setattr(probe, "Client", lambda *a, **kw: pytest.fail("host pipe opened"))
    with pytest.raises(RuntimeError, match="ephemeral CI"):
        probe.main()


@pytest.mark.parametrize("wire", [b"[]", b"null", b"bad-json"])
def test_probe_rejects_non_object_frames(wire):
    connection = Mock()
    connection.recv_bytes.return_value = wire
    with pytest.raises(ValueError):
        probe.receive(connection)
    connection.recv_bytes.assert_called_once_with(4096)


def test_probe_deadline_precedes_read():
    connection = Mock()
    connection.poll.return_value = False
    with pytest.raises(TimeoutError):
        probe.receive(connection)
    connection.recv_bytes.assert_not_called()


def test_probe_bounds_outgoing_frames():
    connection = Mock()
    with pytest.raises(ValueError, match="budget"):
        probe.send(connection, {"value": "x" * 4096})
    connection.send_bytes.assert_not_called()
    probe.send(connection, {"adopt": 42})
    assert json.loads(connection.send_bytes.call_args.args[0]) == {"adopt": 42}


def native(monkeypatch):
    api = Mock()
    api.OpenProcess.return_value = 100
    api.CloseHandle.return_value = True

    def times(handle, birth, *_):
        birth._obj.dwLowDateTime = 123
        return True

    api.GetProcessTimes.side_effect = times
    monkeypatch.setattr(probe, "kernel", lambda: api)
    monkeypatch.setattr(probe.ctypes, "get_last_error", lambda: 5, raising=False)
    monkeypatch.setattr(probe.ctypes, "WinError", lambda code: OSError(code, "refused"), raising=False)
    return api


def test_probe_pins_birth_before_control(monkeypatch):
    api = native(monkeypatch)
    with pytest.raises(RuntimeError, match="generation"):
        probe.Process(42, expected_birth=456)
    api.CloseHandle.assert_called_once_with(100)
    api.TerminateProcess.assert_not_called()


def test_probe_never_reopens_pid_for_retirement(monkeypatch):
    api = native(monkeypatch)
    api.WaitForSingleObject.side_effect = [0x102, 0]
    process = probe.Process(42, expected_birth=123)
    process.retire()
    process.close()
    api.OpenProcess.assert_called_once()
    api.TerminateProcess.assert_called_once_with(100, 1)
    api.CloseHandle.assert_called_once_with(100)


def test_probe_retains_custody_on_failed_close(monkeypatch):
    api = native(monkeypatch)
    process = probe.Process(42)
    api.CloseHandle.return_value = False
    with pytest.raises(OSError):
        process.close()
    assert process.handle == 100
    api.CloseHandle.return_value = True
    process.close()
    assert process.handle is None
