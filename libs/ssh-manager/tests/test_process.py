"""Tests for SSH process-tree console isolation."""

from __future__ import annotations

from ssh_manager import process


def test_ssh_subprocess_kwargs_windows_uses_hidden_console(monkeypatch):
    class FakeStartupInfo:
        def __init__(self):
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(process.sys, "platform", "win32")
    monkeypatch.setattr(process.subprocess, "STARTUPINFO", FakeStartupInfo, raising=False)
    monkeypatch.setattr(process.subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(process.subprocess, "SW_HIDE", 0, raising=False)
    monkeypatch.setattr(process.subprocess, "CREATE_NEW_CONSOLE", 16, raising=False)

    kwargs = process.ssh_subprocess_kwargs(limit=123)

    assert kwargs["limit"] == 123
    assert kwargs["creationflags"] == 16
    startupinfo = kwargs["startupinfo"]
    assert startupinfo.dwFlags & 1
    assert startupinfo.wShowWindow == 0


def test_ssh_subprocess_kwargs_posix_starts_new_session(monkeypatch):
    monkeypatch.setattr(process.sys, "platform", "linux")

    assert process.ssh_subprocess_kwargs(limit=123) == {
        "limit": 123,
        "start_new_session": True,
    }
