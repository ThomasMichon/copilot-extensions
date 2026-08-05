"""Guard the detached passive-daemon creation flags (headed-console fix).

The ZDD cutover spawns the passive daemon straight from Python (no
`conhost --headless` wrapper the installer uses). Under DefTerm (Windows Terminal
as the default terminal app), `CREATE_NO_WINDOW` still surfaces a visible
window/tab -- it *creates* a console and merely hides its window, which DefTerm
overrides. `DETACHED_PROCESS` alone creates NO console, so there is nothing for
DefTerm to grab. This pins that choice so a future edit can't reintroduce
`CREATE_NO_WINDOW` (the headed-console bug).
"""

from __future__ import annotations

import subprocess

from agent_bridge import __main__ as main


def test_win32_uses_detached_not_create_no_window(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    flags = main._passive_daemon_creationflags()
    assert flags & subprocess.DETACHED_PROCESS
    # CREATE_NO_WINDOW is the headed-console bug under DefTerm -- must NOT be set.
    assert not (flags & subprocess.CREATE_NO_WINDOW)


def test_non_windows_no_flags(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "linux")
    assert main._passive_daemon_creationflags() == 0
