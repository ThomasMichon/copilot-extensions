"""Opt-in Windows desktop observation of the actual ``fleet`` host probes.

``test_fleet_cli.py`` mocks ``subprocess.run``, which proves flag wiring but
not behavior. This drives ``list_views_cli._probe_host`` from a real
windowless parent (``pythonw.exe`` inside a kill-on-close Job) through two
cycles of each launch path while observing actual top-level windows and
foreground changes:

* local: the probe's own interpreter argv and kwargs, with a real console
  ``python.exe`` child standing in for the ``list`` run;
* ssh: the real ``ssh.exe`` with the probe's own argv and kwargs, pointed
  through an isolated ``-F`` config at a loopback alias whose
  ``ProxyCommand`` is a real console ``python.exe`` that closes the
  connection, so no remote host is contacted.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
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

_PARENT = """\
import ctypes, json, subprocess, sys
from pathlib import Path
spec = json.loads(sys.argv[1])
sys.path[:0] = spec['paths']
from agent_worktrees import list_views_cli
assert not ctypes.windll.kernel32.GetConsoleWindow()
real_run = subprocess.run
def run(args, **kwargs):
    assert kwargs['stdin'] is subprocess.DEVNULL
    if spec['path'] == 'local':
        assert args == [sys.executable, '-I', '-X', 'utf8', '-m', 'agent_worktrees',
            '--project', 'probe-project', 'list', '--json', '--passive'], args
        return real_run([spec['console'], '-I', '-c',
            "import time; time.sleep(0.5); print('{\\"worktrees\\": []}')"],
            **kwargs)
    assert args[:3] == ['ssh', '-o', 'BatchMode=yes'] and args[5] == (
        'fleet-probe-target'), args
    return real_run([spec['ssh'], '-F', spec['ssh_config'], *args[1:]], **kwargs)
list_views_cli.subprocess.run = run
for cycle in range(2):
    row = list_views_cli._probe_host(
        'probe-machine', '', 'fleet-probe-target', 'pwsh',
        spec['path'] == 'local', project='probe-project',
        extra_args=['--passive'], timeout=5)
    if spec['path'] == 'local':
        assert row['reachable'] is True and row['worktrees'] == [], row
    else:
        assert row['reachable'] is False and row['error'], row
Path(spec['result']).write_text('2', encoding='ascii')
"""


def _observer_module():
    repo = Path(__file__).resolve().parents[3]
    path = (
        repo / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    spec = importlib.util.spec_from_file_location("fleet_window_observer", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module._SURFACED_PROCESS_NAMES.add("ssh.exe")
    return module


@pytest.mark.parametrize("launch_path", ["local", "ssh"])
def test_fleet_probe_from_windowless_parent(tmp_path, launch_path):
    observer_module = _observer_module()
    user32, kernel32, callback_type = observer_module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("desktop observation requires an observable foreground window")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe") or not Path(pythonw).is_file():
        pytest.skip("this interpreter has no real windowless parent")
    ssh = shutil.which("ssh") or ""
    if launch_path == "ssh" and not ssh:
        pytest.skip("no ssh client on PATH")
    console = observer_module._console_python()
    proxy = tmp_path / "proxy.py"
    proxy.write_text("import time; time.sleep(0.5)\n", encoding="ascii")
    ssh_config = tmp_path / "ssh_config"
    ssh_config.write_text(
        "Host fleet-probe-target\n"
        "  HostName 127.0.0.1\n"
        f'  ProxyCommand "{console}" -I "{proxy}"\n'
        "  UserKnownHostsFile NUL\n"
        "  GlobalKnownHostsFile NUL\n",
        encoding="ascii",
    )
    parent = tmp_path / "parent.py"
    parent.write_text(_PARENT, encoding="utf-8")
    result = tmp_path / "result"
    arguments = {
        "paths": [str(Path(item).resolve()) for item in sys.path if item],
        "path": launch_path,
        "console": console,
        "ssh": ssh,
        "ssh_config": str(ssh_config),
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
                assert process.wait(timeout=60) == 0, log_path.read_text(errors="replace")
            finally:
                job.terminate_and_wait(10)
                process.wait(timeout=10)
    assert result.read_text(encoding="ascii") == "2"
    assert observer.surfaced == {}, f"visible windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
