"""Actor-free original-identity and passive completed-cleanup controls."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_remote_driver.headed import auth_child as api
from agent_procutil.owned_process import OwnedCleanupError


async def _inline(function, *args, **kwargs):
    return function(*args, **kwargs)


def _original_child(monkeypatch, *, confirmed=True):
    monkeypatch.setattr(api, "require_auth_custody", lambda: None)
    monkeypatch.setattr(api.asyncio, "to_thread", _inline)
    process = SimpleNamespace(
        pid=123, wait=Mock(), poll=Mock(side_effect=AssertionError("inspection must not poll")),
    )
    job = SimpleNamespace(
        try_terminate_and_confirm_empty=Mock(return_value=confirmed),
        terminate_and_wait=Mock(), close=Mock(),
    )
    monkeypatch.setattr(api, "spawn_sync_in_kill_on_close_job", Mock(return_value=(process, job)))
    return api.AuthPaneChild(), process, job


@pytest.mark.parametrize("confirmed", [True, False])
def test_original_scope_survives_cleanup_and_inspection_is_passive(monkeypatch, confirmed):
    async def exercise():
        child, process, job = _original_child(monkeypatch, confirmed=confirmed)
        assert child.native_scope is None and child.scope_settled(None) is None
        child.launch(["fixture"])
        api.spawn_sync_in_kill_on_close_job.assert_called_once_with(
            ["fixture"], require_job=True, allow_breakaway=False,
        )
        identity = child.native_scope
        assert identity is not None and child.proc is process and child.job is job
        assert child.scope_settled(identity) is None
        with pytest.raises(RuntimeError, match="twice"):
            child.launch(["fixture"])
        other = api.AuthPaneChild()
        other.launch(["fixture"])
        assert other.native_scope is not identity and other.scope_settled(identity) is None

        class NotTheIdentity:
            def __eq__(self, _other):
                raise AssertionError("Native identity must not use value equality")

        assert child.scope_settled(NotTheIdentity()) is None
        await child.terminate()
        assert child.native_scope is identity and child.scope_settled(identity) is True
        job.try_terminate_and_confirm_empty.assert_called_once_with()
        assert job.terminate_and_wait.call_count == (0 if confirmed else 1)
        process.wait.assert_called_once_with(timeout=2)
        job.close.assert_called_once_with()
        for operation in (process.wait, job.try_terminate_and_confirm_empty,
                          job.terminate_and_wait, job.close):
            operation.reset_mock()
            operation.side_effect = AssertionError("inspection must not initiate cleanup")
        child.retirement_started = False
        for _ in range(2):
            assert child.native_scope is identity and child.scope_settled(identity) is True
            assert child.scope_settled(other.native_scope) is None
        process.poll.assert_not_called()
        for operation in (process.wait, job.try_terminate_and_confirm_empty,
                          job.terminate_and_wait, job.close):
            operation.assert_not_called()

    asyncio.run(exercise())


@pytest.mark.parametrize("failure", ["request", "confirmation", "wait", "close"])
def test_failed_cleanup_never_publishes_settlement(monkeypatch, failure):
    async def exercise():
        child, process, job = _original_child(monkeypatch, confirmed=False)
        child.launch(["fixture"])
        identity = child.native_scope
        operation = {
            "request": job.try_terminate_and_confirm_empty,
            "confirmation": job.terminate_and_wait,
            "wait": process.wait,
            "close": job.close,
        }[failure]
        operation.side_effect = OSError("fixture cleanup failed")
        with pytest.raises(OwnedCleanupError):
            await child.terminate()
        assert child.scope_settled(identity) is None
        assert child.native_scope is identity
        process.poll.assert_not_called()

    asyncio.run(exercise())


def test_retirement_and_partial_confirmation_are_not_completed_cleanup(monkeypatch):
    child, _process, job = _original_child(monkeypatch)
    child.launch(["fixture"])
    identity = child.native_scope
    child.retirement_started = child._job_empty = child._settled = True
    assert child.scope_settled(identity) is None
    job.try_terminate_and_confirm_empty.assert_not_called()
    job.terminate_and_wait.assert_not_called()
    job.close.assert_not_called()


def test_failed_launch_does_not_issue_a_native_identity(monkeypatch):
    child, _process, _job = _original_child(monkeypatch)
    api.spawn_sync_in_kill_on_close_job.side_effect = OSError("fixture launch failed")
    with pytest.raises(OSError, match="launch failed"):
        child.launch(["fixture"])
    assert child.native_scope is None and child.scope_settled(None) is None
