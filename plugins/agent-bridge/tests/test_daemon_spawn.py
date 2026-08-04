"""Test the console-free daemon interpreter selection (headed-console fix).

The cutover spawns the passive daemon straight from Python (no `conhost --headless`
wrapper the installer uses). Under DefTerm (Windows Terminal as the default
terminal app), a `python.exe` + `CREATE_NO_WINDOW` spawn is still captured as a
visible window/tab, so `_windowless_daemon_executable` must prefer the
GUI-subsystem `pythonw.exe` to allocate no console at all.
"""

from __future__ import annotations

import os

from agent_bridge import __main__ as main


def test_prefers_pythonw_on_windows(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.sys, "executable", r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(os.path, "exists", lambda p: p.endswith("pythonw.exe"))
    exe = main._windowless_daemon_executable()
    assert exe.replace("/", "\\").endswith(r"Scripts\pythonw.exe")


def test_falls_back_when_pythonw_absent(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "win32")
    monkeypatch.setattr(main.sys, "executable", r"C:\venv\Scripts\python.exe")
    monkeypatch.setattr(os.path, "exists", lambda p: False)
    assert main._windowless_daemon_executable() == r"C:\venv\Scripts\python.exe"


def test_uses_sys_executable_off_windows(monkeypatch):
    monkeypatch.setattr(main.sys, "platform", "linux")
    monkeypatch.setattr(main.sys, "executable", "/venv/bin/python")
    # os.path.exists must not even be consulted off Windows, but guard anyway.
    monkeypatch.setattr(os.path, "exists", lambda p: True)
    assert main._windowless_daemon_executable() == "/venv/bin/python"
