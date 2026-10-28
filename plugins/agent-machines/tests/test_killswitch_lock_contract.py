"""Native producer/kill-switch reader contract, without running an installer."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from agent_machines import killswitch_cli

REPO = Path(__file__).resolve().parents[3]
WATCHDOG = REPO / "libs" / "installer-engine" / "installer-watchdog.ps1"
GUARD = REPO / "libs" / "bootstrap-killswitch" / "bootstrap-killswitch-guard.ps1"
pytestmark = pytest.mark.skipif(os.name != "nt", reason="native Windows lock producer")


def _literal(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def _run(shell: str, command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        text=True,
        timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )


@pytest.mark.parametrize("shell_name", ["powershell.exe", "pwsh"])
@pytest.mark.parametrize("helper_available", [False, True])
@pytest.mark.parametrize(
    "shape,expected",
    [("native", True), ("legacy", True), ("reused-native", False), ("reused-legacy", False)],
)
def test_native_lock_producer_preserves_killswitch_warnings(
    tmp_path, monkeypatch, capsys, shell_name, helper_available, shape, expected
) -> None:
    shell = shutil.which(shell_name)
    if not shell:
        pytest.skip(f"{shell_name} unavailable")
    home = tmp_path / "home"
    lock = home / ".example-plugin" / "reconcile.lock"
    lock.parent.mkdir(parents=True)
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT", str(home))
    monkeypatch.setenv("BOOTSTRAP_KILLSWITCH_STATE_FILE", str(home / "ks.json"))
    scripts = tmp_path / "guard-only-payload"
    scripts.mkdir()
    guard_path = scripts / GUARD.name
    shutil.copyfile(GUARD, guard_path)
    if helper_available:
        shutil.copyfile(WATCHDOG, scripts / WATCHDOG.name)
    command = (
        f". {_literal(WATCHDOG)}; "
        f"Write-InstallReconcileLock -Path {_literal(lock)} -ProcessId $PID; "
        "Start-Sleep -Seconds 60"
    )
    owner = subprocess.Popen(
        [shell, "-NoProfile", "-NonInteractive", "-Command", command],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            parts = lock.read_text().split() if lock.exists() else []
            if len(parts) == 2:
                break
            assert owner.poll() is None, "native lock producer exited before publication"
            time.sleep(0.05)
        assert len(parts) == 2, "native lock producer did not publish before deadline"
        pid, birth = map(int, parts)
        assert pid == owner.pid and birth > 0
        if shape.startswith("reused"):
            # A live PID with a different process birth must not own this lock.
            if shape == "reused-native":
                lock.write_text(f"{pid} {birth - 10000}", encoding="utf-8")
            else:
                lock.write_text(str(pid), encoding="utf-8")
                os.utime(lock, ((birth - 10000) / 1000,) * 2)
        elif shape == "legacy":
            lock.write_text(str(pid), encoding="utf-8")

        oracle = _run(
            shell,
            f". {_literal(WATCHDOG)}; Test-InstallReconcileLock -Path {_literal(lock)}",
        )
        assert oracle.returncode == 0, oracle.stderr
        assert oracle.stdout.strip().lower() == str(expected).lower()
        assert owner.poll() is None

        original_lock = lock.read_bytes(), lock.stat().st_mtime_ns
        observations = {}
        args = argparse.Namespace(reason="contract control", json=False)
        assert killswitch_cli.cmd_on(args) == 0
        text = capsys.readouterr().out
        observations["python-text"] = "WARNING" in text and "example-plugin" in text
        args.json = True
        assert killswitch_cli.cmd_on(args) == 0
        payload = json.loads(capsys.readouterr().out)
        observations["python-json"] = payload.get("in_flight_reconciles") == ["example-plugin"]
        guard = _run(shell, f"& {_literal(guard_path)} on 'contract control'")
        assert guard.returncode == 0, guard.stderr
        observations["powershell-on"] = (
            "WARNING" in guard.stdout and "example-plugin" in guard.stdout
        )
        assert owner.poll() is None, "owner must remain live throughout every reader"
        assert (lock.read_bytes(), lock.stat().st_mtime_ns) == original_lock
        assert observations == dict.fromkeys(observations, expected), (
            f"{shape}: producer lock ownership={expected}, warning visibility={observations}"
        )
    finally:
        if owner.poll() is None:
            owner.terminate()
        owner.wait(timeout=5)
