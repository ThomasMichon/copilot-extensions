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

import json
import os
from pathlib import Path
import subprocess
import sys
import re

import pytest

from agent_bridge import __main__ as m
from agent_procutil import no_window_kwargs, python_cache_args


def _cache_args(argv, root):
    assert argv[0] == "-X" and len(argv) == 2
    cache = Path(argv[1].removeprefix("pycache_prefix="))
    assert cache.parent == Path(python_cache_args(root)[1].partition("=")[2]).parent
    assert re.fullmatch(r"unsealed-[0-9a-f]{32}", cache.name)
    return str(cache)


@pytest.fixture(autouse=True)
def neutral_launch_directory(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)


def test_launch_argv_uses_interpreter_not_bare_name():
    argv = m._daemon_launch_argv()
    # Never the bare, shim-routed command name that trips WinError 2.
    assert argv != ["agent-bridge", "start"]
    # Runs the daemon as a module through a resolved interpreter.
    assert argv[0] == sys.executable
    _cache_args(argv[1:3], m._INSTALL_DIR)
    assert argv[3:] == ["-m", "agent_bridge", "start"]


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
    assert argv[0] == str(py)
    _cache_args(argv[1:3], tmp_path)
    assert argv[3:] == ["-m", "agent_bridge", "start"]


def test_launch_argv_uses_console_interpreter_for_descendant_containment(monkeypatch):
    monkeypatch.setattr(m.sys, "executable", "PYTHON")
    argv = m._daemon_launch_argv()
    assert argv[0] == "PYTHON"
    _cache_args(argv[1:3], m._INSTALL_DIR)
    assert argv[3:] == ["-m", "agent_bridge", "start"]


def test_launch_argv_falls_back_to_binstub_on_path(monkeypatch, tmp_path):
    # No interpreter and no venv -> resolve the binstub via which (POSIX shims
    # are plain exec scripts and do not re-parse, so they are safe here).
    monkeypatch.setattr(m.sys, "executable", "")
    monkeypatch.setattr(m, "_INSTALL_DIR", str(tmp_path / "nonexistent"))
    monkeypatch.setattr(m.shutil, "which", lambda name: "/usr/bin/agent-bridge")
    argv = m._daemon_launch_argv()
    assert argv == ["/usr/bin/agent-bridge", "start"]


def test_daemon_python_prefix_caches_cold_and_warm_imports_outside_the_slot(monkeypatch, tmp_path):
    root = tmp_path / "runtime with spaces"
    slot = root / "versions" / "1.0.0"
    slot.mkdir(parents=True)
    source = slot / "cache_probe.py"
    source.write_text("value = 42\n", encoding="utf-8")
    before = source.read_bytes(), source.stat().st_mtime_ns, source.stat().st_ino
    monkeypatch.setattr(m, "_INSTALL_DIR", str(root))
    caller_env = dict(os.environ)
    command = m._daemon_launch_argv()
    prefix = _cache_args(command[1:3], root)
    program = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);import cache_probe;"
        "print(json.dumps({'cached':cache_probe.__cached__,'prefix':sys.pycache_prefix,"
        "'enabled':not sys.dont_write_bytecode,'value':cache_probe.value}))"
    )
    command = [*command[:command.index("-m")], "-c", program, str(slot)]
    env = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "wrong ambient cache")
    previous = None
    for _cycle in range(2):
        result = subprocess.run(
            command, env=env, capture_output=True, text=True, check=True, timeout=30,
            **no_window_kwargs(),
        )
        observed = json.loads(result.stdout)
        cache = Path(observed["cached"])
        assert observed["enabled"] and observed["value"] == 42
        assert cache.is_relative_to(Path(observed["prefix"])) and cache.is_file()
        assert observed["prefix"] == prefix
        current = cache.read_bytes(), cache.stat().st_mtime_ns, cache.stat().st_ino
        if previous is not None:
            assert current == previous
        previous = current
    assert not list(slot.rglob("*.pyc"))
    assert (source.read_bytes(), source.stat().st_mtime_ns, source.stat().st_ino) == before
    previous_stat = source.stat()
    source.write_text("value = 84\n", encoding="utf-8")
    os.utime(source, ns=(previous_stat.st_atime_ns, previous_stat.st_mtime_ns))
    replacement = m._daemon_launch_argv()
    replacement = [*replacement[:replacement.index("-m")], "-c", program, str(slot)]
    result = subprocess.run(
        replacement, env=env, capture_output=True, text=True, check=True, timeout=30,
        **no_window_kwargs(),
    )
    replaced = json.loads(result.stdout)
    assert replaced["enabled"] and replaced["value"] == 84
    assert replaced["prefix"] != prefix
    assert (cache.read_bytes(), cache.stat().st_mtime_ns, cache.stat().st_ino) == previous
    assert dict(os.environ) == caller_env


def test_cmd_start_imports_cleanly_on_this_platform(monkeypatch, tmp_path):
    """``_cmd_start`` crashed the daemon on Linux (#3238 follow-up): a
    ``windows_proactor.resilient_loop_factory`` import at the top of the
    function was unconditional even though its only use is inside an
    ``if sys.platform == "win32":`` guard further down -- ``windows_proactor``
    only defines that name inside its own matching guard, so any non-Windows
    platform hit an immediate ``ImportError`` before the daemon could even
    bind a socket. Exercise the real function up through every one of its
    top-of-body imports (stopping cleanly at the first controllable exit
    point, the singleton guard) rather than re-deriving the import list,
    so a reintroduced unconditional import fails this test the same way it
    failed the real daemon.

    Stubs the two real-process side effects ``_cmd_start`` performs before
    reaching that exit point (the kill-on-close job attach and a CWD
    ``chdir`` into the config dir) -- neither is what this test is about,
    and leaving them live would attach the pytest worker to a real Job
    Object on Windows and strand it in a temp dir for later tests.
    """
    import argparse
    import os

    from agent_bridge import config as bridge_config
    from agent_bridge import service_start_cli, winjob
    from agent_bridge.singleton import AlreadyRunningError, SingleInstance

    def _fake_acquire(self):
        raise AlreadyRunningError(lock_path=self.lock_path, holder_pid=1234)

    monkeypatch.setattr(SingleInstance, "acquire", _fake_acquire)
    monkeypatch.setattr(winjob, "setup_kill_on_close_job", lambda: None)
    monkeypatch.setattr(bridge_config, "config_dir", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)

    args = argparse.Namespace(
        port=0, bind=None, idle_shutdown=None, passive=False,
    )
    # No exception at all (in particular no ImportError) -- the function
    # returns as soon as the (mocked) singleton guard reports another
    # instance already holds the lock. monkeypatch's own fixture teardown
    # restores the real cwd afterward regardless of _cmd_start's chdir.
    service_start_cli._cmd_start(args)
    assert os.getcwd() == str(tmp_path)
