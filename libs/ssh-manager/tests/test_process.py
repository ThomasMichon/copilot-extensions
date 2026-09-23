"""Tests for SSH process-tree console isolation."""

from __future__ import annotations

import asyncio

from ssh_manager import process


def test_ssh_subprocess_kwargs_windows_uses_detached_process(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.subprocess, "DETACHED_PROCESS", 8, raising=False)

    kwargs = process.ssh_subprocess_kwargs(limit=123)

    assert kwargs == {"limit": 123, "creationflags": 8}


def test_ssh_subprocess_kwargs_posix_starts_new_session(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "linux")

    assert process.ssh_subprocess_kwargs(limit=123) == {
        "limit": 123,
        "start_new_session": True,
    }


def test_terminate_windows_tree_runs_taskkill_off_event_loop(monkeypatch):
    class FakeProc:
        returncode = None
        pid = 123

        async def wait(self):
            self.returncode = 0
            return 0

        def kill(self):
            self.returncode = -1

    inside_thread = False
    calls = []

    def fake_run(*args, **kwargs):
        assert inside_thread
        calls.append((args, kwargs))

    async def fake_to_thread(func, *args, **kwargs):
        nonlocal inside_thread
        inside_thread = True
        try:
            return func(*args, **kwargs)
        finally:
            inside_thread = False

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.subprocess, "run", fake_run)
    monkeypatch.setattr(process.asyncio, "to_thread", fake_to_thread)
    monkeypatch.setattr(process.subprocess, "CREATE_NO_WINDOW", 8, raising=False)

    asyncio.run(process.terminate_ssh_process_tree(FakeProc()))

    assert calls[0][0][0] == ["taskkill", "/PID", "123", "/T", "/F"]


def test_terminate_tree_ignores_already_exited_kill_race():
    class FakeProc:
        returncode = None
        pid = None

        async def wait(self):
            self.returncode = 0
            return 0

        def kill(self):
            raise ProcessLookupError

    asyncio.run(process.terminate_ssh_process_tree(FakeProc()))


def test_run_process_cleanup_timeout_does_not_block_caller():
    """A bounded caller must not be stuck behind a slow shielded cleanup.

    Regression for the observed Windows symptom: a per-command SSH proxy's
    cleanup (`taskkill /T /F` against a `gh cs ssh --stdio` child) can take
    100+ seconds under endpoint-protection scanning. An ambient watcher with
    no one waiting on it for correctness must be able to give up on its own
    schedule while the real cleanup keeps running in the background.
    """
    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_close():
        started.set()
        await release.wait()

    fake_process = object()
    process.register_process_cleanup(fake_process, slow_close)

    async def scenario():
        async with asyncio.timeout(5):
            await process.run_process_cleanup(fake_process, timeout=0.05)
            # The bounded wait gave up quickly...
            await started.wait()
            # ...but the shielded cleanup task is still alive in the
            # background rather than abandoned/cancelled outright.
            entry = process._CLEANUPS[id(fake_process)]
            assert not entry.task.done()
            release.set()
            await entry.task

    asyncio.run(scenario())


def test_run_process_cleanup_default_is_unbounded():
    """Existing unbounded callers keep waiting for the real result."""
    calls = []

    async def close():
        calls.append("done")

    fake_process = object()
    process.register_process_cleanup(fake_process, close)

    asyncio.run(process.run_process_cleanup(fake_process))

    assert calls == ["done"]
    assert id(fake_process) not in process._CLEANUPS
