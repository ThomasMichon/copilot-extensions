"""Fallback daemon spawn resolves a real executable, never a bare name (#4432).

``_service_start``'s no-systemd/no-scheduled-task fallback used to
``subprocess.Popen(["agent-bridge", "start"])`` -- a bare, extensionless command
name. On Windows the on-PATH artifact is ``agent-bridge.cmd`` and
``CreateProcess`` does not apply PATHEXT, so the spawn failed with WinError 2 and
(because ``service restart`` stops first) left the bridge down. The launch argv
must invoke the interpreter directly (``python -m agent_bridge start``) so it
never routes through the ``.cmd`` shim.
"""

from __future__ import annotations

import sys

from agent_bridge import __main__ as m


def test_launch_argv_uses_interpreter_not_bare_name():
    argv = m._daemon_launch_argv()
    # Never the bare, shim-routed command name that trips WinError 2.
    assert argv != ["agent-bridge", "start"]
    # Runs the daemon as a module through a resolved interpreter.
    assert argv[0] == sys.executable
    assert argv[1:] == ["-m", "agent_bridge", "start"]


def test_launch_argv_falls_back_to_venv_python(monkeypatch, tmp_path):
    # With no running-interpreter path, prefer the installed venv interpreter.
    monkeypatch.setattr(m.sys, "executable", "")
    venv = tmp_path / "venv"
    subdir = "Scripts" if sys.platform == "win32" else "bin"
    pyname = "python.exe" if sys.platform == "win32" else "python"
    (venv / subdir).mkdir(parents=True)
    py = venv / subdir / pyname
    py.write_text("")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path))
    argv = m._daemon_launch_argv()
    assert argv == [str(py), "-m", "agent_bridge", "start"]


def test_launch_argv_uses_console_interpreter_for_descendant_containment(monkeypatch):
    monkeypatch.setattr(m.sys, "executable", "PYTHON")
    argv = m._daemon_launch_argv()
    assert argv == ["PYTHON", "-m", "agent_bridge", "start"]


def test_launch_argv_falls_back_to_binstub_on_path(monkeypatch, tmp_path):
    # No interpreter and no venv -> resolve the binstub via which (POSIX shims
    # are plain exec scripts and do not re-parse, so they are safe here).
    monkeypatch.setattr(m.sys, "executable", "")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-bridge")
    argv = m._daemon_launch_argv()
    assert argv == ["/usr/bin/agent-bridge", "start"]
