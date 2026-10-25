"""Opt-in Windows desktop observation of the actual account-token launch."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import agent_procutil as pu
import pytest

pytestmark = [
    pytest.mark.portfolio_tier("T3"),
    pytest.mark.effect("process"),
    pytest.mark.skipif(sys.platform != "win32", reason="Windows desktop observation"),
    pytest.mark.skipif(
        os.environ.get("COPILOT_EXTENSIONS_NATIVE_WINDOWS") != "1",
        reason="opt in with COPILOT_EXTENSIONS_NATIVE_WINDOWS=1",
    ),
]


def test_account_token_lookup_from_windowless_parent(tmp_path):
    repo = Path(__file__).resolve().parents[3]
    path = (
        repo / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    spec = importlib.util.spec_from_file_location("account_token_observer", path)
    observer_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = observer_module
    spec.loader.exec_module(observer_module)
    user32, kernel32, callback_type = observer_module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("desktop observation requires an observable foreground window")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe") or not Path(pythonw).is_file():
        pytest.skip("this interpreter has no real windowless parent")
    parent = tmp_path / "parent.py"
    parent.write_text(
        "import ctypes, json, subprocess, sys\n"
        "from pathlib import Path\n"
        "spec = json.loads(sys.argv[1])\n"
        "sys.path[:0] = spec['paths']\n"
        "from agent_worktrees import git_ops, lease_store\n"
        "from agent_worktrees.lease_config import LeaseSettings\n"
        "assert not ctypes.windll.kernel32.GetConsoleWindow()\n"
        "real_run = subprocess.run\n"
        "def run(args, **kwargs):\n"
        "    assert args[:3] == ['gh', 'auth', 'token'] or args[:1] == ['git']\n"
        "    return real_run([spec['console'], '-I', '-c',\n"
        "        \"import time; time.sleep(0.5); print('synthetic-credential')\"],\n"
        "        **kwargs)\n"
        "git_ops.subprocess.run = run\n"
        "git_ops.shutil.which = lambda name: spec['console']\n"
        "for cycle in range(2):\n"
        "    git_ops._gh_token_for_owner.cache_clear()\n"
        "    assert git_ops._gh_token_for_owner('selected-writer') == 'synthetic-credential'\n"
        "    store = lease_store.GitLeaseStore(LeaseSettings(origin='synthetic-origin'))\n"
        "    assert store._git(['--version']).stdout.strip() == 'synthetic-credential'\n"
        "Path(spec['result']).write_text('4', encoding='ascii')\n",
        encoding="utf-8",
    )
    result = tmp_path / "result"
    arguments = {
        "paths": [str(Path(item).resolve()) for item in sys.path if item],
        "console": observer_module._console_python(),
        "result": str(result),
    }
    log_path = tmp_path / "parent.log"
    with observer_module._WindowObserver(user32, kernel32, callback_type) as observer:
        with log_path.open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [pythonw, "-I", "-B", str(parent), json.dumps(arguments)],
                require_job=True, allow_breakaway=False, cwd=tmp_path,
                env={**os.environ, **pu.windowless_python_env(sys.executable)},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
            try:
                assert job is not None
                assert process.wait(timeout=25) == 0, log_path.read_text(errors="replace")
            finally:
                job.terminate_and_wait(10)
                process.wait(timeout=10)
    assert result.read_text(encoding="ascii") == "4"
    assert observer.surfaced == {}, f"visible windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
