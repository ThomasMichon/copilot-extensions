"""Tests for the shared headless/detached process-spawn kwargs."""
from __future__ import annotations

import agent_procutil as pu


def test_contained_test_mode_reads_explicit_runner_marker(monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert not pu.contained_test_mode()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.contained_test_mode()
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "true")
    assert not pu.contained_test_mode()


def test_no_window_kwargs_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    kw = pu.no_window_kwargs()
    assert kw == {"creationflags": pu._CREATE_NO_WINDOW}


def test_no_window_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.no_window_kwargs() == {}


def test_no_window_flags(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.no_window_flags() == pu._CREATE_NO_WINDOW
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.no_window_flags() == 0


def test_windowless_python_prefers_pythonw_on_windows(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    pythonw = tmp_path / "pythonw.exe"
    pythonw.write_text("")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(pythonw)


def test_windowless_python_falls_back_without_pythonw(monkeypatch, tmp_path):
    python = tmp_path / "python.exe"
    python.write_text("")
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    assert pu.windowless_python(python) == str(python)


def test_windowless_python_noop_off_windows(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    assert pu.windowless_python("/usr/bin/python3") == "/usr/bin/python3"


def test_detached_kwargs_windows_plain(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = pu.detached_kwargs()["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert not (flags & pu._CREATE_BREAKAWAY_FROM_JOB)


def test_detached_kwargs_windows_breakaway(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = pu.detached_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert flags & pu._CREATE_BREAKAWAY_FROM_JOB


def test_detached_kwargs_windows_contained_suppresses_breakaway(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    flags = pu.detached_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._DETACHED_PROCESS
    assert flags & pu._CREATE_NEW_PROCESS_GROUP
    assert not (flags & pu._CREATE_BREAKAWAY_FROM_JOB)


def test_detached_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert pu.detached_kwargs() == {"start_new_session": True}
    assert pu.detached_kwargs(breakaway=True) == {"start_new_session": True}


def test_detached_kwargs_posix_contained_suppresses_new_session(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.detached_kwargs() == {}
    assert pu.detached_kwargs(breakaway=True) == {}


def test_windowless_daemon_kwargs_windows_preserves_no_window_host(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    flags = pu.windowless_daemon_kwargs(breakaway=True)["creationflags"]
    assert flags & pu._CREATE_NO_WINDOW
    assert flags & pu._CREATE_BREAKAWAY_FROM_JOB
    assert not (flags & pu._DETACHED_PROCESS)
    assert not (flags & pu._CREATE_NEW_PROCESS_GROUP)


def test_windowless_daemon_kwargs_windows_contained_suppresses_breakaway(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.windowless_daemon_kwargs(breakaway=True) == {
        "creationflags": pu._CREATE_NO_WINDOW
    }


def test_windowless_daemon_kwargs_posix(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    assert pu.windowless_daemon_kwargs(breakaway=True) == {
        "start_new_session": True
    }


def test_windowless_daemon_kwargs_posix_contained(monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: False)
    monkeypatch.setenv("COPILOT_EXTENSIONS_TEST_CONTAINED", "1")
    assert pu.windowless_daemon_kwargs(breakaway=True) == {}
