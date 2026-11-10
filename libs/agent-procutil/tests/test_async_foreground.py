"""Unsupported composition and invalid admission never create a native scope."""

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import async_foreground as api
from agent_procutil.owned_process import OwnedCleanupError


@pytest.mark.parametrize("argv", [[], ["bad\0argument"], "not-an-argv"])
def test_invalid_foreground_admission_refuses_before_native_support(monkeypatch, argv):
    support = Mock(side_effect=AssertionError("must not acquire native custody"))
    monkeypatch.setattr(api, "require_foreground_custody", support)
    with pytest.raises(ValueError, match="literal argv"):
        asyncio.run(api.AsyncForegroundProcess.start(argv))
    support.assert_not_called()


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_foreground_platform_refuses_before_native_creation(monkeypatch, platform):
    launch = Mock(side_effect=AssertionError("must not create a guardian"))
    monkeypatch.setattr(api, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(api.subprocess, "Popen", launch)
    with pytest.raises(RuntimeError, match="Linux pidfd"):
        api.require_foreground_custody()
    launch.assert_not_called()


@pytest.mark.parametrize("failure", ["confirmation", "drain"])
def test_failed_cleanup_restores_terminal_without_disposing_custody(monkeypatch, failure):
    async def exercise():
        owner = api.AsyncForegroundProcess()
        owner._guardian = SimpleNamespace(pid=123)
        owner._terminal = SimpleNamespace(restore=Mock(), close=Mock())
        owner._request_retirement = Mock()

        async def frame():
            return b"settled 123"

        owner._frame = frame
        confirm_error = OSError("fixture confirmation failed") if failure == "confirmation" else None
        monkeypatch.setattr(api, "confirm_session", Mock(side_effect=confirm_error))
        drain_error = OSError("fixture drain failed") if failure == "drain" else None
        monkeypatch.setattr(api, "_drain", Mock(side_effect=drain_error))
        with pytest.raises(OwnedCleanupError) as caught:
            await owner.wait()
        assert caught.value.__cause__ is (confirm_error or drain_error)
        assert caught.value.cleanup_resources == (owner,)
        assert caught.value.process is owner._guardian
        owner._terminal.restore.assert_called_once_with()
        owner._terminal.close.assert_not_called()
        assert not owner.retirement_started

    asyncio.run(exercise())


def test_failed_restoration_preserves_both_errors_and_terminal_custody(monkeypatch):
    async def exercise():
        owner = api.AsyncForegroundProcess()
        original, restoration = OSError("fixture observation failed"), OSError("fixture restore failed")
        owner._terminal = SimpleNamespace(restore=Mock(side_effect=restoration), close=Mock())
        monkeypatch.setattr(api, "_drain", Mock(side_effect=original))
        with pytest.raises(OwnedCleanupError) as caught:
            await owner.wait()
        assert caught.value.__cause__ is restoration
        assert restoration.__cause__ is original
        assert caught.value.cleanup_resources == (owner,)
        owner._terminal.close.assert_not_called()

    asyncio.run(exercise())


def test_cleanup_retry_deadline_bounds_progressing_terminal_output(monkeypatch):
    from agent_procutil import _interactive_terminal as terminal
    from agent_procutil import owned_process

    clock = [0.0]

    def write(_descriptor, _data):
        clock[0] += .04
        return 1

    time_source = SimpleNamespace(monotonic=lambda: clock[0])
    monkeypatch.setattr(terminal, "time", time_source)
    monkeypatch.setattr(owned_process, "time", time_source)
    monkeypatch.setattr(terminal, "os", SimpleNamespace(write=write))
    monkeypatch.setattr(terminal, "select", SimpleNamespace(
        select=lambda _readers, writers, _errors, _timeout: ([], writers, []),
    ))
    monkeypatch.setattr(terminal, "_read_pty", lambda *_args: b"")
    owner = api.AsyncForegroundProcess()
    owner._scope_settled = True
    owner._master = 8
    owner._terminal = SimpleNamespace(fd=7)
    owner._output = bytearray(b"x" * 100)
    owner._dispose = Mock()
    error = OwnedCleanupError(
        None, cleanup_resources=(owner,), cleanup_retry=owner._retry_cleanup,
    )
    with pytest.raises(TimeoutError, match="cleanup deadline"):
        error.retry_cleanup(deadline=.1)
    assert clock[0] <= .12 and owner._output
    assert error.cleanup_resources == (owner,)
    owner._dispose.assert_not_called()
