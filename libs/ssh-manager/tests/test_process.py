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
