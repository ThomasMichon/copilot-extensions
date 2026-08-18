"""Tests for the shared no-console-window spawn helper."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_dispatch import procutil


def test_no_window_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "nt")
    kw = procutil.no_window_kwargs()
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert kw == {"creationflags": expected}


def test_no_window_kwargs_off_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "posix")
    assert procutil.no_window_kwargs() == {}


def test_runtime_root_is_under_home_not_payload():
    root = procutil.runtime_root()
    assert root == Path.home() / ".agent-dispatch"
    # The runtime root must never be inside the Copilot plugin payload tree.
    assert "installed-plugins" not in root.parts


def test_relocate_off_payload_chdirs_to_runtime_root(tmp_path, monkeypatch):
    # Simulate a daemon lazy-started with the plugin payload as its CWD.
    payload = tmp_path / ".copilot" / "installed-plugins" / "x" / "agent-dispatch"
    payload.mkdir(parents=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(payload)
    assert Path.cwd() == payload

    procutil.relocate_off_payload()

    # It relocated OFF the payload to the runtime root (which it created).
    assert Path.cwd() == fake_home / ".agent-dispatch"
    assert "installed-plugins" not in Path.cwd().parts


def test_relocate_off_payload_is_best_effort(monkeypatch):
    # A chdir failure must never be fatal (the daemon still starts).
    monkeypatch.setattr(procutil.os, "chdir", lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")))
    procutil.relocate_off_payload()  # does not raise

