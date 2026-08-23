"""Tests for the shared headless/detached process-spawn kwargs."""
from __future__ import annotations

import os

import agent_procutil as pu


def test_no_window_kwargs_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    kw = pu.no_window_kwargs()
    assert kw == {"creationflags": pu._CREATE_NO_WINDOW}


def test_no_window_kwargs_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert pu.no_window_kwargs() == {}


def test_no_window_flags(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    assert pu.no_window_flags() == pu._CREATE_NO_WINDOW
    monkeypatch.setattr(os, "name", "posix")
    assert pu.no_window_flags() == 0


def test_windowless_python_prefers_pythonw_on_windows(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("")
    monkeypatch.setattr(os, "name", "nt")
    assert pu.windowless_python(python) == str(pythonw)


def test_windowless_python_falls_back_without_pythonw(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    monkeypatch.setattr(os, "name", "nt")
    assert pu.windowless_python(python) == str(python)


def test_windowless_python_noop_off_windows(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert pu.windowless_python("/usr/bin/python3") == "/usr/bin/python3"


def test_detached_kwargs_windows_plain(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    flags = pu.detached_kwargs()["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert not (flags & pu._CREATE_BREAKAWAY_FROM_JOB)


def test_detached_kwargs_windows_breakaway(monkeypatch):
    monkeypatch.setattr(os, "name", "nt")
    flags = pu.detached_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert flags & pu._CREATE_BREAKAWAY_FROM_JOB


def test_detached_kwargs_posix(monkeypatch):
    monkeypatch.setattr(os, "name", "posix")
    assert pu.detached_kwargs() == {"start_new_session": True}
    assert pu.detached_kwargs(breakaway=True) == {"start_new_session": True}
