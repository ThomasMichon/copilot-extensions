"""Tests for the shared no-console-window spawn helper."""

from __future__ import annotations

import subprocess

from agent_dispatch import procutil


def test_no_window_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "nt")
    kw = procutil.no_window_kwargs()
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert kw == {"creationflags": expected}


def test_no_window_kwargs_off_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "posix")
    assert procutil.no_window_kwargs() == {}
