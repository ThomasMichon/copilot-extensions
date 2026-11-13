"""Actual-child handoff wiring, without default enrollment or auth scope changes."""

import asyncio
import json
import os
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_remote_driver.headed import launcher, native_handoff
from agent_remote_driver.headed.native_handoff import WindowsNativeHandoff


def _windows(monkeypatch):
    windows = SimpleNamespace(**{**vars(os), "name": "nt"})
    monkeypatch.setattr(launcher, "os", windows)
    monkeypatch.setattr(native_handoff, "os", windows)
    if not hasattr(subprocess, "STARTUPINFO"):
        monkeypatch.setattr(subprocess, "STARTUPINFO", SimpleNamespace, raising=False)


@pytest.mark.parametrize("selected", ["copilot.cmd", "python.exe", "relative.exe"])
def test_forwarders_and_coordinators_cannot_be_published(tmp_path, monkeypatch, selected):
    _windows(monkeypatch)
    spawn = Mock()
    monkeypatch.setattr(native_handoff, "spawn_sync_in_kill_on_close_job", spawn)
    image = tmp_path / selected
    image.write_bytes(b"MZfixture")
    with pytest.raises(ValueError, match="direct Windows Copilot"):
        WindowsNativeHandoff(Mock()).launch([str(image)])
    spawn.assert_not_called()


@pytest.mark.asyncio
async def test_auth_receiver_rejects_native_handoff_before_staging(tmp_path, monkeypatch):
    _windows(monkeypatch)
    policy = Mock(side_effect=AssertionError("must refuse before effects"))
    monkeypatch.setattr(launcher, "_require_receiving_policy", policy)
    with pytest.raises(ValueError, match="non-auth"):
        await launcher.run_headed(
            ["copilot.exe", "--session-id", "selected"], session_id="selected",
            attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
            auth_wait={}, native_handoff=WindowsNativeHandoff(Mock()),
        )
    policy.assert_not_called()
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_headed_owner_passes_actual_creation_and_retains_original_wait(tmp_path, monkeypatch):
    _windows(monkeypatch)
    image = tmp_path / "copilot.exe"
    image.write_bytes(b"MZfixture")
    child = Mock(pid=os.getpid(), _handle=1234)
    child.poll.return_value = 23
    job = Mock()
    captured = []
    handle_checks = []
    monkeypatch.setattr(launcher, "contained_test_mode", lambda: True)
    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    monkeypatch.setattr(
        native_handoff.os, "get_handle_inheritable",
        lambda handle: handle_checks.append(handle) or True, raising=False,
    )

    def capture(created):
        assert created is child and created._handle == 1234
        captured.append(created)

    def create(argv, **kwargs):
        assert argv[0] == str(image)
        assert kwargs["require_job"] is True and kwargs["allow_breakaway"] is False
        assert kwargs["close_fds"] is True
        assert kwargs["startupinfo"].lpAttributeList == {"handle_list": [77]}
        kwargs["on_suspended"](child)
        return child, job

    spawn = Mock(side_effect=create)
    monkeypatch.setattr(native_handoff, "spawn_sync_in_kill_on_close_job", spawn)
    state_file = tmp_path / "state.json"
    assert await launcher.run_headed(
        [str(image), "--session-id", "selected"], session_id="selected",
        attempt_directory=tmp_path / "attempt", state_file=state_file,
        native_handoff=WindowsNativeHandoff(capture, (77,)),
    ) == 23
    assert captured == [child] and handle_checks == [77]
    assert spawn.call_count == 1
    job.terminate_and_wait.assert_called_once_with(5)
    child.wait.assert_called_once_with(timeout=5)
    assert json.loads(state_file.read_text())["child_exit_code"] == 23


@pytest.mark.parametrize("handles", [[1], (True,), (0,), (7, 7)])
def test_handoff_does_not_accept_ambiguous_handle_custody(handles):
    with pytest.raises(ValueError, match="unique positive"):
        WindowsNativeHandoff(lambda _process: None, handles)


@pytest.mark.asyncio
@pytest.mark.parametrize("cleanup_fails", [False, True])
async def test_cancellation_after_root_exit_still_settles_or_retains_handoff_job(
    tmp_path, monkeypatch, cleanup_fails,
):
    from agent_procutil.owned_process import OwnedCleanupError

    _windows(monkeypatch)
    image = tmp_path / "copilot.exe"
    image.write_bytes(b"MZfixture")
    child = Mock(pid=os.getpid(), returncode=None)
    child.poll.side_effect = lambda: child.returncode
    job = Mock()
    if cleanup_fails:
        job.terminate_and_wait.side_effect = OSError("fixture descendants unsettled")
    created = asyncio.Event()

    class Backend:
        def __init__(self, *_args, **_kwargs):
            self.bound = asyncio.Event()

        async def serve(self):
            pass

        def declaration(self):
            return {}

        async def close(self):
            # The root exits after waiter cancellation, before final native cleanup.
            child.returncode = 23

    def create(*_args, **_kwargs):
        created.set()
        return child, job

    monkeypatch.setattr(launcher, "HeadedBackend", Backend)
    monkeypatch.setattr(launcher, "contained_test_mode", lambda: True)
    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    monkeypatch.setattr(native_handoff, "spawn_sync_in_kill_on_close_job", create)
    task = asyncio.create_task(launcher.run_headed(
        [str(image), "--session-id", "selected"], session_id="selected",
        attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
        native_handoff=WindowsNativeHandoff(lambda _process: None),
    ))
    await asyncio.wait_for(created.wait(), 2)
    task.cancel()
    with pytest.raises(OwnedCleanupError if cleanup_fails else asyncio.CancelledError) as caught:
        await task
    job.terminate_and_wait.assert_called_once_with(5)
    job.close.assert_not_called()
    if cleanup_fails:
        assert caught.value.process is child and caught.value.job is job
    else:
        child.wait.assert_called_once_with(timeout=5)
