"""Opt-in desktop observation of the provider's actual captured-child sites."""

from __future__ import annotations

import ctypes
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading

import pytest
from agent_procutil import no_window_flags, spawn_sync_in_kill_on_close_job


pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.skipif(
        os.name != "nt" or os.environ.get("CODESPACES_ACTIVATION_WINDOWS_E2E") != "1",
        reason="opt-in interactive Windows activation observation",
    ),
]


def test_activation_children_stay_windowless_for_two_cycles(tmp_path: Path) -> None:
    session = ctypes.c_ulong()
    assert ctypes.WinDLL("kernel32").ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    assert session.value != 0, "desktop observation requires an interactive Windows session"
    pythonw = Path(sys.base_prefix) / "pythonw.exe"
    gh = shutil.which("gh")
    assert pythonw.is_file() and gh and Path(gh).suffix.lower() == ".exe"
    source = Path(__file__).resolve().parents[4]
    observer_path = source / "worktree-manager" / "tests" / "mux_windows_observer.py"
    spec = importlib.util.spec_from_file_location("activation_window_observer", observer_path)
    assert spec is not None and spec.loader is not None
    observer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer)
    options = tmp_path / "options.json"
    options.write_text(json.dumps({"paths": sys.path, "gh": gh}), encoding="utf-8")
    output = tmp_path / "result.json"
    script = r"""
import argparse, json, pathlib, subprocess, sys, time
options = json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
sys.path[:] = options["paths"]
from agent_codespaces import activation_cli
activation_cli.launch_policy.refused_exit_code = lambda name: None
activation_cli.lifecycle.get_codespace_status_with_account = (
    lambda name, account=None: (True, "Shutdown", account))
activation_cli.verified_account_token = lambda account: "fixture-token"
activation_cli.lifecycle.wait_for_available = lambda *a, **k: True
real_run = subprocess.run
calls = []
def run(argv, **kwargs):
    identity = argv == ["gh", "api", "user", "--jq", ".login"]
    assert identity or argv == [
        "gh", "api", "--method", "POST", "/user/codespaces/fixture/start"]
    done = real_run([options["gh"], "--version"], **kwargs)
    assert done.returncode == 0, done.stderr
    calls.append("identity" if identity else "start")
    if identity:
        done.stdout = "fixture-owner\n"
    return done
activation_cli.subprocess.run = run
time.sleep(.25)
for cycle in range(2):
    assert activation_cli.cmd_start(argparse.Namespace(name="fixture")) == 0
    time.sleep(.25)
pathlib.Path(sys.argv[2]).write_text(json.dumps(calls), encoding="utf-8")
"""
    environment = {
        key: value for key, value in os.environ.items()
        if key not in {"GH_TOKEN", "GITHUB_TOKEN", "PYTHONPATH", "PYTHONHOME"}
    }
    baseline_windows, baseline_foreground = observer.windows()
    baseline_pids = set(observer.processes())
    roots: set[int] = set()
    owned: set[int] = set()
    visible: set[int] = set()
    focused: set[int] = set()
    stop = threading.Event()
    failures: list[BaseException] = []

    def observe() -> None:
        try:
            while not stop.is_set():
                table = observer.processes()
                for root in roots.copy():
                    owned.update(observer.descendants(root, table))
                windows, foreground = observer.windows()
                for hwnd, pid in windows.items():
                    terminal = table.get(pid, ("", 0))[0].lower() in {
                        "windowsterminal.exe", "openconsole.exe", "conhost.exe",
                    }
                    if pid in owned or hwnd not in baseline_windows and terminal:
                        visible.add(hwnd)
                if foreground in owned or (
                    foreground != baseline_foreground
                    and table.get(foreground, ("", 0))[0].lower() in {
                        "windowsterminal.exe", "openconsole.exe", "conhost.exe",
                    }
                ):
                    focused.add(foreground)
                for pid, (name, _) in table.items():
                    if pid not in baseline_pids and name.lower() == "openconsole.exe":
                        visible.add(pid)
                stop.wait(.01)
        except BaseException as error:
            failures.append(error)

    monitor = threading.Thread(target=observe)
    monitor.start()
    process = job = None
    try:
        process, job = spawn_sync_in_kill_on_close_job(
            [str(pythonw), "-c", script, str(options), str(output)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=environment, creationflags=no_window_flags(), require_job=True,
        )
        assert job is not None
        roots.add(process.pid)
        stdout, stderr = process.communicate(timeout=60)
        assert process.returncode == 0, (stdout, stderr)
        assert json.loads(output.read_text(encoding="utf-8")) == ["identity", "start"] * 2
    finally:
        if job is not None:
            job.close()
        if process is not None:
            process.communicate(timeout=5)
        stop.set()
        monitor.join(timeout=3)
    assert not monitor.is_alive() and not failures
    assert roots and owned
    assert not (owned & observer.processes().keys()), "fixture descendants remain live"
    assert not visible, "activation child surfaced a visible window or terminal acquisition"
    assert not focused, "activation child changed foreground focus"
