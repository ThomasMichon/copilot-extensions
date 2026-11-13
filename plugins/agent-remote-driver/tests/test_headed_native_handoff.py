"""Actual-child handoff wiring, without default enrollment or auth scope changes."""

import asyncio
import json
import os
import subprocess
import threading
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


@pytest.mark.parametrize("override", [{"shell": True}, {"executable": "another-owner.exe"}])
def test_popen_overrides_cannot_change_the_actual_published_image(tmp_path, monkeypatch, override):
    _windows(monkeypatch)
    image = tmp_path / "copilot.exe"
    image.write_bytes(b"MZfixture")
    spawn, callback = Mock(), Mock()
    monkeypatch.setattr(native_handoff, "spawn_sync_in_kill_on_close_job", spawn)
    with pytest.raises(ValueError, match="cannot override"):
        WindowsNativeHandoff(callback).launch([str(image)], **override)
    spawn.assert_not_called()
    callback.assert_not_called()


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
@pytest.mark.parametrize("root_exits", [False, True], ids=["live-root", "exited-root"])
async def test_cancellation_still_settles_or_retains_handoff_job(
    tmp_path, monkeypatch, cleanup_fails, root_exits,
):
    from agent_procutil.owned_process import OwnedCleanupError

    _windows(monkeypatch)
    image = tmp_path / "copilot.exe"
    image.write_bytes(b"MZfixture")
    child = Mock(pid=os.getpid(), returncode=None)
    child.poll.side_effect = lambda: child.returncode
    child.kill.side_effect = OSError("fixture root kill is unavailable")
    job = Mock()

    def retire(_timeout):
        if cleanup_fails:
            raise OSError("fixture descendants unsettled")
        child.returncode = 23

    job.terminate_and_wait.side_effect = retire
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
            if root_exits:
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
    child.kill.assert_not_called()
    if cleanup_fails:
        assert caught.value.process is child and caught.value.job is job
    else:
        child.wait.assert_called_once_with(timeout=5)


@pytest.mark.asyncio
async def test_interrupted_settlement_retains_the_original_cleanup_task():
    from agent_procutil.owned_process import OwnedCleanupError

    owner = launcher._PaneChild()
    child, job = Mock(pid=123), Mock()
    child.poll.return_value = 23
    owner.proc, owner.job = child, job
    owner.started.set()
    entered, release = threading.Event(), threading.Event()

    def retire(_timeout):
        entered.set()
        assert release.wait(5), "fixture cleanup was not released"

    job.terminate_and_wait.side_effect = retire
    wait = asyncio.create_task(owner.wait())
    try:
        assert await asyncio.wait_for(asyncio.to_thread(entered.wait, 2), 3)
        wait.cancel()
        with pytest.raises(OwnedCleanupError) as caught:
            await wait
        failure = caught.value
        assert failure.process is child and failure.job is job
        assert failure.cleanup_task is owner._cleanup_task
        assert not failure.cleanup_task.done()
    finally:
        release.set()
        await asyncio.gather(wait, return_exceptions=True)
        if owner._cleanup_task is not None:
            await owner._cleanup_task
    assert owner.job is None
    job.terminate_and_wait.assert_called_once_with(5)


@pytest.mark.asyncio
async def test_executor_refusal_retains_native_custody_without_replaying_dispatch(monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError

    owner = launcher._PaneChild()
    child, job = Mock(pid=123), Mock()
    child.poll.return_value = 23
    owner.proc, owner.job = child, job
    owner.started.set()
    dispatch = Mock(side_effect=RuntimeError("fixture executor refused dispatch"))
    monkeypatch.setattr(asyncio.get_running_loop(), "run_in_executor", dispatch)
    for operation in (owner.wait, owner.terminate):
        with pytest.raises(OwnedCleanupError) as caught:
            await operation()
        assert caught.value.process is child and caught.value.job is job
        assert caught.value.cleanup_task is owner._cleanup_task
        assert isinstance(caught.value.cleanup_error, RuntimeError)
    assert owner.job is job
    assert dispatch.call_count == 1
    job.terminate_and_wait.assert_not_called()
    child.kill.assert_not_called()


@pytest.mark.asyncio
async def test_peer_loss_cannot_turn_cancelled_settlement_into_normal_exit(tmp_path, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError

    _windows(monkeypatch)
    image = tmp_path / "copilot.exe"
    image.write_bytes(b"MZfixture")
    child, job = Mock(pid=os.getpid()), Mock()
    peer_lost, running, unavailable = (asyncio.Event() for _ in range(3))
    actions = []

    class Owner:
        proc = None
        pid = os.getpid()
        returncode = None

        def launch(self, *_args, **_kwargs):
            self.proc = child

        async def wait(self):
            actions.append("wait")
            if actions.count("wait") > 1:
                return 23
            try:
                await asyncio.Future()
            except asyncio.CancelledError as error:
                raise OwnedCleanupError(child, job) from error

        async def terminate(self):
            actions.append("settle")

    class Backend:
        extension_pid = 321

        def __init__(self, *_args, **_kwargs):
            self.bound = asyncio.Event()
            self.lost = peer_lost

        async def serve(self):
            self.bound.set()

        def declaration(self):
            return {}

        async def close(self):
            pass

    class Host:
        def __init__(self, *_args, **_kwargs):
            pass

        async def serve(self, **_kwargs):
            return 12345

        async def close(self):
            pass

    original_write = launcher._write_host_state

    def publish(path, state):
        original_write(path, state)
        if state["state"] == "running":
            running.set()
        elif state["state"] == "control_unavailable":
            unavailable.set()

    monkeypatch.setattr(launcher, "_PaneChild", Owner)
    monkeypatch.setattr(launcher, "HeadedBackend", Backend)
    monkeypatch.setattr(launcher, "SessionHost", Host)
    monkeypatch.setattr(launcher, "_write_host_state", publish)
    monkeypatch.setattr(launcher, "contained_test_mode", lambda: True)
    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    task = asyncio.create_task(launcher.run_headed(
        [str(image), "--session-id", "selected"], session_id="selected",
        attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
        native_handoff=WindowsNativeHandoff(lambda _process: None),
    ))
    try:
        await asyncio.wait_for(running.wait(), 2)
        peer_lost.set()
        await asyncio.wait_for(unavailable.wait(), 2)
        task.cancel()
        with pytest.raises(OwnedCleanupError) as caught:
            await task
        assert caught.value.process is child and caught.value.job is job
        assert actions == ["wait", "settle"]
        assert "control_error" not in json.loads((tmp_path / "state.json").read_text())
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
async def test_repeated_cancellation_during_task_drain_still_reaches_owned_job(tmp_path, monkeypatch):
    _windows(monkeypatch)
    image = tmp_path / "copilot.exe"
    image.write_bytes(b"MZfixture")
    child, job = Mock(pid=os.getpid(), returncode=None), Mock()
    child.poll.side_effect = lambda: child.returncode
    child.kill.side_effect = AssertionError("the retained Job owns native retirement")
    started, draining, backend_closed = (asyncio.Event() for _ in range(3))
    never = asyncio.Event()
    original_wait = launcher._PaneChild.wait
    first_wait = True

    async def delayed_wait(owner):
        nonlocal first_wait
        if first_wait:
            first_wait = False
            started.set()
            try:
                await never.wait()
            except asyncio.CancelledError:
                draining.set()
                await never.wait()
        return await original_wait(owner)

    def retire(_timeout):
        child.returncode = 23

    class Backend:
        def __init__(self, *_args, **_kwargs):
            self.bound = asyncio.Event()

        async def serve(self):
            pass

        def declaration(self):
            return {}

        async def close(self):
            backend_closed.set()

    job.terminate_and_wait.side_effect = retire
    monkeypatch.setattr(launcher._PaneChild, "wait", delayed_wait)
    monkeypatch.setattr(launcher, "HeadedBackend", Backend)
    monkeypatch.setattr(launcher, "contained_test_mode", lambda: True)
    monkeypatch.setattr(launcher, "_require_receiving_policy", Mock())
    monkeypatch.setattr(
        native_handoff, "spawn_sync_in_kill_on_close_job", Mock(return_value=(child, job)),
    )
    task = asyncio.create_task(launcher.run_headed(
        [str(image), "--session-id", "selected"], session_id="selected",
        attempt_directory=tmp_path / "attempt", state_file=tmp_path / "state.json",
        native_handoff=WindowsNativeHandoff(lambda _process: None),
    ))
    try:
        await asyncio.wait_for(started.wait(), 2)
        task.cancel()
        await asyncio.wait_for(draining.wait(), 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert backend_closed.is_set()
        job.terminate_and_wait.assert_called_once_with(5)
        child.wait.assert_called_once_with(timeout=5)
        child.kill.assert_not_called()
    finally:
        never.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
