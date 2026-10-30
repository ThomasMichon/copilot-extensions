"""Mocked native calls establish pin/duplicate ordering, not native proof."""

import ctypes
from unittest.mock import Mock

import pytest

from agent_procutil import _daemon_instance_shell_windows as native
from agent_procutil._daemon_instance_records import ProcessIdentity


@pytest.fixture
def api(monkeypatch):
    kernel = Mock()
    kernel.OpenProcess.return_value = 41
    kernel.WaitForSingleObject.return_value = 258
    kernel.CreateJobObjectW.return_value = 71
    kernel.GetCurrentProcess.return_value = 99
    kernel.GetProcessId.return_value = 4300

    def server(_pipe, target):
        target._obj.value = 4200
        return True

    def duplicate(_source, _handle, _target, output, _access, _inherit, _flags):
        output._obj.value = 81
        return True

    def check(value):
        if not value:
            raise OSError("native fixture refused")

    kernel.GetNamedPipeServerProcessId.side_effect = server
    kernel.DuplicateHandle.side_effect = duplicate
    monkeypatch.setattr(native, "_api", lambda: kernel)
    monkeypatch.setattr(native, "_is_windows", lambda: True)
    monkeypatch.setattr(native, "_check", check)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(
        native, "_windows_birth", lambda handle: "windows:123" if handle == 41 else "windows:124",
    )
    return kernel


def test_shell_requires_actual_input_pipe_server_before_job_or_duplicate(api):
    def foreign(_pipe, target):
        target._obj.value = 4500
        return True

    api.GetNamedPipeServerProcessId.side_effect = foreign
    with pytest.raises(ProcessLookupError, match="original shell"):
        native.ShellProcessOwner(ProcessIdentity(4200, "windows:123"), 51)
    api.CloseHandle.assert_called_once_with(41)
    api.CreateJobObjectW.assert_not_called()
    api.DuplicateHandle.assert_not_called()


def test_shell_birth_mismatch_releases_pin_without_effects(api):
    with pytest.raises(ProcessLookupError, match="birth identity"):
        native.ShellProcessOwner(ProcessIdentity(4200, "windows:122"), 51)
    api.CloseHandle.assert_called_once_with(41)
    api.CreateJobObjectW.assert_not_called()


def test_child_pin_comes_from_callers_original_handle_not_reopened_pid(api, monkeypatch):
    owner = native.ShellProcessOwner(ProcessIdentity(4200, "windows:123"), 51)
    process = Mock(_handle=81)
    factory = Mock(return_value=process)
    monkeypatch.setattr(native.WindowsDaemonProcess, "from_handle", factory)
    try:
        with owner.child(ProcessIdentity(4300, "windows:124"), 61) as observed:
            assert observed is process
        api.DuplicateHandle.assert_called_once()
        call = api.DuplicateHandle.call_args.args
        assert call[:3] == (41, 61, 99)
        assert call[4:] == (0, False, 2)
        api.OpenProcess.assert_called_once()
        factory.assert_called_once_with(81, 4300, ["shell-instance-child"])
        process.close.assert_called_once_with()
    finally:
        owner.close()


def test_changed_child_birth_never_yields_control_or_signals(api, monkeypatch):
    owner = native.ShellProcessOwner(ProcessIdentity(4200, "windows:123"), 51)
    process = Mock(_handle=81)
    monkeypatch.setattr(native.WindowsDaemonProcess, "from_handle", lambda *_: process)
    try:
        with pytest.raises(ProcessLookupError, match="child process identity"):
            with owner.child(ProcessIdentity(4300, "windows:125"), 61):
                pytest.fail("foreign child admitted")
        process.close.assert_called_once_with()
        process.kill.assert_not_called()
        process.terminate.assert_not_called()
    finally:
        owner.close()


def test_disposable_job_assigns_retained_shell_and_stays_until_owner_close(api):
    owner = native.ShellProcessOwner(ProcessIdentity(4200, "windows:123"), 51)
    try:
        scope = owner.contain({})
        assert scope.startswith("Local\\ce-owned-job-")
        api.AssignProcessToJobObject.assert_called_once_with(71, 41)
        assert owner.contain({}) == scope
        api.CreateJobObjectW.assert_called_once()
        api.CloseHandle.assert_not_called()
    finally:
        owner.close()
    assert api.CloseHandle.call_args_list == [((71,),), ((41,),)]


def test_job_setup_failure_refuses_without_returning_a_lifetime(api):
    owner = native.ShellProcessOwner(ProcessIdentity(4200, "windows:123"), 51)
    api.AssignProcessToJobObject.return_value = False
    try:
        with pytest.raises(OSError, match="fixture refused"):
            owner.contain({})
        assert owner.job is None
        api.CloseHandle.assert_called_once_with(71)
    finally:
        owner.close()


def test_unconfirmed_job_close_is_terminal_and_never_retries_handle_value(api):
    owner = native.ShellProcessOwner(ProcessIdentity(4200, "windows:123"), 51)
    api.AssignProcessToJobObject.return_value = False
    api.CloseHandle.side_effect = lambda handle: handle != 71
    try:
        with pytest.raises(RuntimeError, match="Job closure is unconfirmed"):
            owner.contain({})
        assert owner.job is None
        assert [call.args for call in api.CloseHandle.call_args_list] == [(71,)]
    finally:
        owner.close()
    assert [call.args for call in api.CloseHandle.call_args_list] == [(71,), (41,)]
