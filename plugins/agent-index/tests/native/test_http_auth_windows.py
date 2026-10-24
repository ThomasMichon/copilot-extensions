"""Opt-in desktop observation of two credential acquisitions from pythonw."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import agent_procutil as pu
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("COPILOT_EXTENSIONS_NATIVE_WINDOWS") != "1",
    reason="requires opted-in native Windows desktop observation",
)

PARENT = """
import ctypes, json, sys
from pathlib import Path
spec = json.loads(sys.argv[1])
sys.path[:0] = spec["pythonpath"]
from agent_index.http_auth import _credential
kernel = ctypes.WinDLL("kernel32")
assert not kernel.GetConsoleWindow(), "parent has a console"
for cycle in range(2):
    value = _credential([spec["console"], "-I", spec["provider"], spec["root"], str(cycle)])
    assert value == "synthetic-observed-credential"
Path(spec["root"], "result.json").write_text(json.dumps({"cycles": 2}))
"""

PROVIDER = """
import json, os, sys, time
from pathlib import Path
root = Path(sys.argv[1])
cycle = sys.argv[2]
(root / (cycle + ".json")).write_text(json.dumps([os.getppid(), os.getpid()]))
deadline = time.monotonic() + 10
while not (root / (cycle + ".release")).exists():
    if time.monotonic() >= deadline:
        raise SystemExit(2)
    time.sleep(0.01)
print("synthetic-observed-credential")
"""


def test_windowless_credential_acquisition_never_surfaces_or_steals_focus(tmp_path):
    repo = Path(__file__).resolve().parents[4]
    path = repo / "libs" / "agent-procutil" / "tests" / "test_spawn_daemon_windows_integration.py"
    spec = importlib.util.spec_from_file_location("http_auth_window_observer", path)
    assert spec is not None and spec.loader is not None
    observer_module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = observer_module
    spec.loader.exec_module(observer_module)
    user32, kernel32, callback_type = observer_module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("no observable interactive desktop")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe") or not Path(pythonw).is_file():
        pytest.skip("no real windowless interpreter")
    parent = tmp_path / "parent.py"
    provider = tmp_path / "provider.py"
    parent.write_text(PARENT, encoding="utf-8")
    provider.write_text(PROVIDER, encoding="utf-8")
    request = {
        "pythonpath": list(sys.path),
        "console": observer_module._console_python(),
        "provider": str(provider),
        "root": str(tmp_path),
    }
    log_path = tmp_path / "parent.log"
    with observer_module._WindowObserver(user32, kernel32, callback_type) as observer:
        with log_path.open("wb") as log:
            process, job = pu.spawn_sync_in_kill_on_close_job(
                [pythonw, "-I", str(parent), json.dumps(request)],
                require_job=True, allow_breakaway=False,
                env={**os.environ, **pu.windowless_python_env(sys.executable)},
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                **pu.no_window_kwargs(),
            )
            try:
                assert job is not None
                for cycle in range(2):
                    observer_module._wait_for(
                        lambda cycle=cycle: observer_module._read_json(
                            tmp_path / f"{cycle}.json"
                        ),
                        10, "credential provider startup", log_path,
                    )
                    (tmp_path / f"{cycle}.release").touch()
                assert process.wait(timeout=10) == 0, log_path.read_text(errors="replace")
                assert json.loads((tmp_path / "result.json").read_text()) == {"cycles": 2}
            finally:
                job.terminate_and_wait(5)
                process.wait(timeout=5)
    assert observer.surfaced == {}
    assert observer.focus_changes == {}
