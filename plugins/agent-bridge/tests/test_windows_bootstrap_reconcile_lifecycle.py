"""Real Windows hook executions against a harmless, isolated fake installer."""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import signal
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows reconcile lifecycle")
HOOK = Path(__file__).resolve().parents[1] / "scripts" / "bootstrap-check.ps1"
PWSH = shutil.which("pwsh") or shutil.which("powershell")


def alive(pid: int) -> bool:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
    finally:
        kernel.CloseHandle(handle)


def wait_for(read, predicate, seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = read()
        if predicate(value):
            return value
        time.sleep(0.1)
    raise AssertionError("reconcile condition not reached before deadline")


def read_log(path: Path) -> str:
    data = path.read_bytes()
    encoding = "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
    return data.decode(encoding)


@pytest.fixture
def reconcile(tmp_path):
    if not PWSH:
        pytest.skip("PowerShell unavailable")
    home = tmp_path / "home"
    runtime = home / ".agent-bridge"
    runtime.mkdir(parents=True)
    (runtime / "deploy-manifest.json").write_text(
        json.dumps({"source": {"version": "1.0.0"}}), encoding="utf-8")
    plugin = tmp_path / "plugin with ' quote"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    (plugin / "plugin.json").write_text('{"name":"agent-bridge"}', encoding="utf-8")
    (plugin / "pyproject.toml").write_text('version = "1.0.1"\n', encoding="utf-8")
    shutil.copyfile(HOOK, scripts / HOOK.name)
    (scripts / "install.ps1").write_text(
        r"""
param([string]$Action, [switch]$NonInteractive)
$root = Join-Path $env:USERPROFILE '.agent-bridge'
Set-Content (Join-Path $root "started-$PID") 'started'
$child = $null
if (Test-Path (Join-Path $root 'block')) {
    $child = Start-Process -FilePath (Get-Process -Id $PID).Path -PassThru -NoNewWindow `
        -ArgumentList @('-NoProfile', '-Command', 'Start-Sleep -Seconds 120')
}
[ordered]@{
    pid = $PID
    child = $(if ($child) { $child.Id } else { 0 })
    staged = $env:COPILOT_PLUGIN_INSTALL_STAGED
    staged_from = $env:COPILOT_PLUGIN_STAGED_FROM
    cwd = [IO.Directory]::GetCurrentDirectory()
} | ConvertTo-Json -Compress | Set-Content (Join-Path $root 'stub.json')
if ($child) {
    while (-not (Test-Path (Join-Path $root 'release'))) { Start-Sleep -Milliseconds 100 }
}
exit 9
""", encoding="utf-8")
    env = dict(os.environ)
    env.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    env.update(USERPROFILE=str(home), HOME=str(home),
               COPILOT_PLUGIN_INSTALL_STAGED="1", COPILOT_PLUGIN_STAGED_FROM="other-plugin")
    owned = set()

    def read_json(name):
        path = runtime / name
        if not path.exists():
            return {}
        try:
            result = json.loads(path.read_text(encoding="utf-8-sig"))
        except json.JSONDecodeError:
            return {}  # The publisher is replacing a small status record.
        for key in ("launched_pid", "wrapper_pid", "pid", "child", "child_pid", "grandchild_pid"):
            if result.get(key):
                owned.add(result[key])
        return result

    def run_hook(plugin_dir=None, extra_env=None):
        selected = plugin_dir or plugin
        result = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(selected / "scripts" / HOOK.name)],
            cwd=selected, env=env | (extra_env or {}),
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "{}"
        return result

    try:
        yield runtime, run_hook, read_json, plugin
    finally:
        read_json("reconcile-status.json")
        read_json("stub.json")
        read_json("smoke.json")
        for pid in owned:
            if alive(pid):
                subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, timeout=10, creationflags=subprocess.CREATE_NO_WINDOW)


def test_fast_completion_owns_worker_pid_and_resets_inherited_staging(reconcile):
    runtime, run_hook, read, _ = reconcile
    run_hook()
    status = wait_for(lambda: read("reconcile-status.json"), lambda s: "completed_at" in s)
    stub = read("stub.json")
    assert status["launched_pid"] == stub["pid"]
    assert status["launched_pid"] != status["wrapper_pid"]
    assert status["exit_code"] == 9 and status["success"] is False
    assert status["attempt_id"] and status["worker_started_at"]
    assert not stub["staged"] and not stub["staged_from"]
    assert Path(stub["cwd"]) == runtime.parent


def test_young_worker_prevents_duplicate_even_when_wrapper_is_gone(reconcile):
    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    run_hook()
    stub = wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    status = read("reconcile-status.json")
    os.kill(status["wrapper_pid"], signal.SIGTERM)
    wait_for(lambda: alive(status["wrapper_pid"]), lambda active: not active)
    assert alive(stub["pid"]), "the installer can outlive its console wrapper"
    run_hook()
    assert read("reconcile-status.json")["attempt_id"] == status["attempt_id"]
    assert alive(stub["pid"])


def test_stale_reap_kills_worker_and_grandchild_before_replacement(reconcile):
    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    run_hook()
    stub = wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    status = read("reconcile-status.json")
    status["at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    (runtime / "reconcile-status.json").write_text(json.dumps(status), encoding="utf-8")
    (runtime / "block").unlink()
    run_hook()
    new = wait_for(lambda: read("reconcile-status.json"),
                   lambda s: s.get("attempt_id") != status["attempt_id"] and "completed_at" in s)
    assert new["launched_pid"] != stub["pid"]
    wait_for(lambda: (alive(stub["pid"]), alive(stub["child"])), lambda s: s == (False, False))


def test_old_completion_cannot_overwrite_another_attempt(reconcile):
    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    run_hook()
    stub = wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    newer = {"attempt_id": "replacement", "launched_pid": 0}
    (runtime / "reconcile-status.json").write_text(json.dumps(newer), encoding="utf-8")
    (runtime / "block").unlink()
    (runtime / "release").touch()
    wait_for(lambda: alive(stub["pid"]), lambda active: not active)
    assert read("reconcile-status.json") == newer


def test_inherited_staging_cannot_bypass_real_installer_watchdog(reconcile):
    runtime, run_hook, read, plugin = reconcile
    installed = runtime.parent / ".copilot" / "installed-plugins" / "fixture" / "agent-bridge"
    shutil.copytree(plugin, installed)
    shutil.copyfile(HOOK.with_name("install.ps1"), installed / "scripts" / "install.ps1")
    run_hook(installed, {
        "COPILOT_PLUGIN_INSTALL_SMOKE": "1",
        "COPILOT_PLUGIN_INSTALL_SMOKE_SLEEP": "60",
        "COPILOT_PLUGIN_INSTALL_SMOKE_GRANDCHILD": "1",
        "AGENT_BRIDGE_INSTALL_DEADLINE_SEC": "10",
    })
    status = wait_for(lambda: read("reconcile-status.json"),
                      lambda s: "completed_at" in s, seconds=20)
    smoke = read("smoke.json")
    assert smoke, (status, read_log(runtime / "reconcile.log"))
    assert smoke["staged"] is True
    assert Path(smoke["staged_from"]) == installed
    assert ".install-stage" in smoke["ran_from"]
    assert status["exit_code"] == 124 and status["success"] is False
    assert smoke["child_pid"] > 0 and smoke["grandchild_pid"] > 0
    wait_for(lambda: (alive(smoke["child_pid"]), alive(smoke["grandchild_pid"])),
             lambda s: s == (False, False))
    assert "WATCHDOG-KILL" in (runtime / "reconcile.err.log").read_text(encoding="utf-8-sig")


def test_concurrent_hooks_admit_only_one_installer(reconcile):
    from concurrent.futures import ThreadPoolExecutor

    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    with ThreadPoolExecutor(max_workers=2) as executor:
        list(executor.map(lambda _: run_hook(), range(2)))
    wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    assert len(list(runtime.glob("started-*"))) == 1


def test_reused_pid_is_not_reaped(reconcile):
    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    run_hook()
    stub = wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    status = read("reconcile-status.json")
    old = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    status.update(at=old, worker_started_at=old)
    (runtime / "reconcile-status.json").write_text(json.dumps(status), encoding="utf-8")
    (runtime / "block").unlink()
    run_hook()
    wait_for(lambda: read("reconcile-status.json"),
             lambda s: s.get("attempt_id") != status["attempt_id"] and "completed_at" in s)
    assert alive(stub["pid"]) and alive(stub["child"]), "a reused PID must remain untouched"


def test_invalid_owner_identity_blocks_duplicate_reconcile(reconcile):
    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    run_hook()
    wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    status = read("reconcile-status.json")
    status["worker_started_at"] = "invalid"
    (runtime / "reconcile-status.json").write_text(json.dumps(status), encoding="utf-8")
    result = run_hook()
    assert "could not validate prior reconcile ownership" in result.stderr
    assert read("reconcile-status.json")["attempt_id"] == status["attempt_id"]
    assert len(list(runtime.glob("started-*"))) == 1


def test_native_stderr_does_not_preempt_successful_installer(reconcile):
    runtime, run_hook, read, plugin = reconcile
    (plugin / "scripts" / "install.ps1").write_text(
        r"""
param([string]$Action, [switch]$NonInteractive)
$ErrorActionPreference = 'Continue'
& (Get-Process -Id $PID).Path -NoProfile -Command `
    '[Console]::Error.WriteLine(123); exit 0'
exit $LASTEXITCODE
""", encoding="utf-8")
    run_hook()
    status = wait_for(lambda: read("reconcile-status.json"), lambda s: "completed_at" in s)
    assert status["exit_code"] == 0 and status["success"] is True
    assert "123" in read_log(runtime / "reconcile.log")


def test_stale_legacy_pid_without_birth_evidence_is_not_reaped(reconcile):
    runtime, run_hook, read, _ = reconcile
    (runtime / "block").touch()
    run_hook()
    stub = wait_for(lambda: read("stub.json"), lambda s: s.get("child"))
    status = read("reconcile-status.json")
    status.pop("worker_started_at")
    status.pop("wrapper_started_at")
    status["at"] = (datetime.now(timezone.utc) - timedelta(minutes=20)).isoformat()
    (runtime / "reconcile-status.json").write_text(json.dumps(status), encoding="utf-8")
    result = run_hook()
    assert "legacy reconcile ownership is unverified" in result.stderr
    assert alive(stub["pid"]) and alive(stub["child"])
    assert read("reconcile-status.json")["attempt_id"] == status["attempt_id"]


def test_terminating_installer_error_is_recorded_in_status_and_log(reconcile):
    runtime, run_hook, read, plugin = reconcile
    (plugin / "scripts" / "install.ps1").write_text(
        "param([string]$Action, [switch]$NonInteractive)\nthrow 'fixture failure'\n",
        encoding="utf-8")
    run_hook()
    status = wait_for(lambda: read("reconcile-status.json"), lambda s: "completed_at" in s)
    assert status["exit_code"] == 1 and status["success"] is False
    assert "fixture failure" in read_log(runtime / "reconcile.log")
    assert status["attempt_id"] in read_log(runtime / "reconcile.log")
