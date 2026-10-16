"""Windows inventory installs retain durable originals without timeout replay."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import direct_python, no_window_kwargs
from plugin_activation import state

from agent_worktrees import installer_run, reconcile, update_acquisition

pytestmark = [pytest.mark.guard, pytest.mark.real_installer_processes]
IDENTITY = "agent-example@market"


def _initialize(home):
    home.mkdir()
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {IDENTITY: False},
    }), encoding="utf-8")
    (home / "config.json").write_text(json.dumps({
        "installedPlugins": [{"name": IDENTITY, "enabled": False, "version": "old"}],
    }), encoding="utf-8")


def _installed(home):
    (home / "settings.json").write_text(json.dumps({
        "enabledPlugins": {IDENTITY: True}, "peer": "retained",
    }), encoding="utf-8")
    (home / "config.json").write_text(json.dumps({
        "installedPlugins": [{"name": IDENTITY, "enabled": True, "version": "new"}],
    }), encoding="utf-8")


@pytest.mark.parametrize("elapsed", [0.2, 0.6])
def test_install_recovery_and_final_restore_share_the_original_budget(
    tmp_path, monkeypatch, elapsed,
):
    home = tmp_path / "copilot"
    _initialize(home)
    monkeypatch.setattr(state, "copilot_home", lambda: home)
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _: None)
    monkeypatch.setattr(update_acquisition, "_TOTAL_BUDGET_S", 32.5)
    clock = iter([0, 0, 0, elapsed])
    monkeypatch.setattr(update_acquisition, "monotonic", lambda: next(clock))
    deadlines = []

    def run(argv, **kwargs):
        deadlines.append(kwargs["deadline_s"])
        return installer_run.InstallerRun("OK", 0, None, False, 0)

    monkeypatch.setattr(installer_run, "run_installer", run)
    status = update_acquisition.acquire("agent-example", "market", "fixture")
    assert status.startswith("OK") is (elapsed < .5)
    assert deadlines == ([pytest.approx(.3)] if elapsed < .5 else [])
    assert not (home / "activation-preservation" / "transaction.json").exists()


@pytest.mark.parametrize("outcome", [
    "success", "timeout", "timeout-refusal", "watchdog", "cleanup", "launch",
])
def test_windows_install_settlement_and_no_timeout_replay(tmp_path, monkeypatch, outcome):
    home = tmp_path / "copilot"
    _initialize(home)
    monkeypatch.setattr(state, "copilot_home", lambda: home)
    monkeypatch.setattr(update_acquisition, "_WINDOWS", True)
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _: None)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs["require_tree_exit"] is True
        if outcome == "launch":
            raise installer_run.InstallerLaunchError("refused before execution")
        _installed(home)
        if outcome == "cleanup":
            raise installer_run.InstallerCleanupError("Job settlement unknown")
        if outcome in ("timeout-refusal", "watchdog"):
            kwargs["log"](
                "HTTP 403 authentication required" if outcome == "timeout-refusal" else "ECONNRESET",
            )
        expired = outcome in ("timeout", "timeout-refusal", "watchdog")
        return installer_run.InstallerRun(
            "timed out (deadline hit during build)" if expired else "OK",
            124 if outcome == "watchdog" else None if expired else 0, None, False, 0,
        )

    monkeypatch.setattr(installer_run, "run_installer", run)
    status = update_acquisition.acquire("agent-example", "market", "fixture")
    assert len(calls) == 1
    assert status.startswith("OK") is (outcome == "success")
    if outcome == "timeout":
        assert status == "timed out"
    if outcome == "timeout-refusal":
        assert "refusal (403)" in status
    if outcome == "watchdog":
        assert "exit 124" in status
    journal = home / "activation-preservation" / "transaction.json"
    if outcome == "cleanup":
        assert journal.exists()
        assert json.loads(journal.read_text())["phase"] == "running"
        assert state.inspect_plugin_state(IDENTITY, home)["userActivation"] == "true"
        assert update_acquisition.acquire("agent-peer", "market", "fixture").startswith(
            "plugin state error",
        )
        assert len(calls) == 1
    else:
        assert not journal.exists()
        assert state.inspect_plugin_state(IDENTITY, home)["userActivation"] == "false"
        assert state.inspect_plugin_state(IDENTITY, home)["inventoryEnabled"] is False


_PAYLOAD = r'''
import json, os, subprocess, sys, time
from pathlib import Path
from agent_procutil import direct_python, no_window_kwargs
root, home = Path(sys.argv[1]), Path(sys.argv[2])
identity = "agent-example@market"
(home / "settings.json").write_text(json.dumps({
    "enabledPlugins": {identity: True}, "peer": "retained"
}), encoding="utf-8")
(home / "config.json").write_text(json.dumps({
    "installedPlugins": [{"name": identity, "enabled": True, "version": "new"}]
}), encoding="utf-8")
python, extra_env = direct_python(sys.executable)
child = subprocess.Popen([python, "-c",
    "import sys,time; from pathlib import Path; "
    "Path(sys.argv[1]).touch(); time.sleep(20); Path(sys.argv[2]).touch()",
    str(root / "grandchild-ready"), str(root / "late-write")],
    env={**os.environ, **extra_env},
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **no_window_kwargs())
(root / "grandchild.pid").write_text(str(child.pid), encoding="ascii")
deadline = time.monotonic() + 10
while not (root / "grandchild-ready").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("native grandchild did not become ready")
    time.sleep(.02)
(root / "payload-ready").touch()
while not (root / "release-payload").exists():
    if time.monotonic() >= deadline:
        raise RuntimeError("test did not acknowledge the native grandchild")
    time.sleep(.02)
'''

_WORKER = r'''
import os, sys, time
from pathlib import Path
from plugin_activation import preservation, state
from agent_worktrees import installer_run, reconcile, update_acquisition
root, home, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
state.copilot_home = lambda: home
def pause():
    (root / "worker.pid").write_text(str(os.getpid()), encoding="ascii")
    (root / "transaction-ready").touch()
    time.sleep(20)
original_confirm = preservation.ActivationPreservation.confirm_quiescent
def confirm(self):
    if mode == "before":
        pause()
    original_confirm(self)
    if mode == "after":
        pause()
preservation.ActivationPreservation.confirm_quiescent = confirm
original_write = state.write_json_object_atomic
def write(path, value, *args):
    if mode == "partial" and path == home / "config.json":
        pause()
    original_write(path, value, *args)
state.write_json_object_atomic = write
original_run = update_acquisition._run_update
def run(*args, **kwargs):
    result = original_run(*args, **kwargs)
    (root / "native-diagnostic").write_text(
        f"{result.returncode}: {result.stdout}\n{result.stderr}", encoding="utf-8")
    return result
update_acquisition._run_update = run
original_spawn = installer_run._spawn
def spawn(command, *args, **kwargs):
    assert command == ["fixture", "plugin", "install", "agent-example@market"]
    return original_spawn(
        [sys.executable, str(root / "payload.py"), str(root), str(home)], *args, **kwargs)
installer_run._spawn = spawn
reconcile.core_installed_payload_dir = lambda name: None
update_acquisition._ATTEMPT_TIMEOUT_S = 10
result = update_acquisition.acquire("agent-example", "market", "fixture", cwd=root)
assert result.startswith("OK"), result
'''


def _wait_file(path: Path, child: subprocess.Popen) -> None:
    deadline = time.monotonic() + 10
    while not path.exists() and child.poll() is None and time.monotonic() < deadline:
        time.sleep(.02)
    diagnostic = path.parent / "native-diagnostic"
    if not path.exists() and child.poll() is not None:
        stdout, stderr = child.communicate(timeout=5)
        pytest.fail(f"native worker exited before {path.name}: {stdout}\n{stderr}")
    assert path.exists(), (
        f"native worker did not reach {path.name}: "
        + (diagnostic.read_text(encoding="utf-8") if diagnostic.exists() else "no command outcome")
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows Job and process handles")
@pytest.mark.parametrize("mode,blocked", [
    ("before", False), ("after", False), ("partial", False), ("after", True),
])
def test_native_job_witness_survives_parent_death_only_after_publication(
    tmp_path, monkeypatch, mode, blocked,
):
    import ctypes
    from ctypes import wintypes

    home = tmp_path / "copilot"
    _initialize(home)
    monkeypatch.setattr(state, "copilot_home", lambda: home)
    (tmp_path / "payload.py").write_text(_PAYLOAD, encoding="utf-8")
    python, extra_env = direct_python(sys.executable)
    dependency = Path(__file__).resolve().parents[3] / "libs" / "agent-procutil" / "src"
    worker = subprocess.Popen(
        [python, "-c", _WORKER, str(tmp_path), str(home), mode],
        env={
            **os.environ, **extra_env,
            "PYTHONPATH": os.pathsep.join([str(dependency), *sys.path]),
        },
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **no_window_kwargs(),
    )
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = None
    try:
        _wait_file(tmp_path / "payload-ready", worker)
        pid = int((tmp_path / "grandchild.pid").read_text())
        handle = kernel.OpenProcess(0x00100000, False, pid)
        assert handle, ctypes.get_last_error()
        assert kernel.WaitForSingleObject(handle, 0) == 258
        (tmp_path / "release-payload").touch()
        _wait_file(tmp_path / "transaction-ready", worker)
        assert worker.pid == int((tmp_path / "worker.pid").read_text())
        assert kernel.WaitForSingleObject(handle, 1000) == 0
        worker.kill()
        worker.wait(timeout=5)
        journal = home / "activation-preservation" / "transaction.json"
        record = journal.read_bytes()
        assert not (tmp_path / "late-write").exists()
        calls = []

        def update(argv, **kwargs):
            calls.append(argv)
            assert argv[2] == "update"
            assert state.inspect_plugin_state(IDENTITY, home)["userActivation"] == "false"
            assert state.inspect_plugin_state(IDENTITY, home)["inventoryEnabled"] is False
            return subprocess.CompletedProcess(argv, 0, "", "")

        monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _: tmp_path)
        monkeypatch.setattr(update_acquisition, "_run_update", update)
        monkeypatch.setattr(update_acquisition._auth, "blocked", lambda *_: blocked)
        status = update_acquisition.acquire("agent-example", "market", "fixture")
        if mode == "before":
            assert status.startswith("plugin state error") and "settlement is unproven" in status
            assert calls == []
            assert journal.read_bytes() == record
        else:
            assert status == ("auth_required" if blocked else "OK")
            assert len(calls) == (0 if blocked else 1)
            assert state.inspect_plugin_state(IDENTITY, home)["userActivation"] == "false"
            assert state.inspect_plugin_state(IDENTITY, home)["inventoryEnabled"] is False
            config = json.loads((home / "config.json").read_text())
            assert config["installedPlugins"][0]["version"] == "new"
            assert json.loads((home / "settings.json").read_text())["peer"] == "retained"
            assert not journal.exists()
    finally:
        if worker.poll() is None:
            worker.kill()
        worker.communicate(timeout=5)
        if handle:
            assert kernel.CloseHandle(handle)
