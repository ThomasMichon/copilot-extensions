"""Guard passive-daemon flags for recurring console descendants."""

from __future__ import annotations

import subprocess

from agent_bridge import __main__ as main

# Win32 process-creation constants, resolved with a getattr fallback so this
# test runs on non-Windows CI (where subprocess lacks these attributes) while
# still asserting against the real Windows values.
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
CREATE_BREAKAWAY_FROM_JOB = 0x01000000


def test_win32_uses_hidden_console_and_job_breakaway(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = main._passive_daemon_creationflags()
    assert flags & CREATE_NO_WINDOW
    assert flags & CREATE_BREAKAWAY_FROM_JOB


def test_non_windows_no_flags(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "linux")
    assert main._passive_daemon_creationflags() == 0
