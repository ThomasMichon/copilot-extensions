"""Proxy commands request GUI intent from procutil, never from target names."""
from __future__ import annotations

import os
from pathlib import Path
import shlex
import stat
import subprocess
from types import SimpleNamespace

import pytest
import agent_procutil as interpreter

from ssh_manager import proxy


def _bind_selection(monkeypatch, tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").touch()
    (scripts.parent / "pyvenv.cfg").write_text(f"home = {base}\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)

    def select(*, require_windowless=False):
        calls.append(require_windowless)
        return interpreter.windowless_python(
            scripts / "python.exe", require_windowless=require_windowless,
        )

    monkeypatch.setattr(proxy, "windowless_python", select)
    return base, calls


@pytest.mark.parametrize("shell", [False, True], ids=["native", "shell"])
@pytest.mark.parametrize("gui_name", ["pythonw.exe", "gui-host.exe"], ids=["relocated", "renamed"])
def test_proxy_command_accepts_canonical_gui_target(monkeypatch, tmp_path, gui_name, shell):
    base, calls = _bind_selection(monkeypatch, tmp_path)
    gui_dir = tmp_path / "gui-only"
    gui_dir.mkdir()
    gui = gui_dir / gui_name
    gui.touch()
    alias = str(base / "pythonw.exe")
    lstat = os.lstat
    readlink = os.readlink
    monkeypatch.setattr(os, "lstat", lambda path, *a, **k: (
        SimpleNamespace(st_mode=stat.S_IFLNK) if os.fspath(path) == alias else lstat(path, *a, **k)
    ))
    monkeypatch.setattr(os, "readlink", lambda path: (
        str(gui) if os.fspath(path) == alias else readlink(path)
    ))
    client = str(Path(proxy.__file__).with_name("proxy_client.py"))
    expected = (
        shlex.join([str(gui).replace("\\", "/"), client.replace("\\", "/"), "1234", "capability"])
        if shell else subprocess.list2cmdline([str(gui), client, "1234", "capability"])
    )
    assert proxy._client_command(1234, "capability", shell=shell) == expected
    assert calls == [True]


def test_proxy_command_refuses_canonical_console_fallback(monkeypatch, tmp_path):
    _, calls = _bind_selection(monkeypatch, tmp_path)
    with pytest.raises(RuntimeError, match="windowless Python interpreter is unavailable"):
        proxy._client_command(1234, "capability")
    assert calls == [True]
