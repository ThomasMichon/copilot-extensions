"""Subprocess-level tests for the canonical bash guard
(``bootstrap-killswitch-guard.sh``) -- exercises the actual vendored shell
script, not a Python reimplementation of its logic (``killswitch_cli.py``'s
own unit tests cover the Python CLI; this file is the behavioral contract
every vendored ``.sh`` copy must keep, since ``sync-bootstrap-killswitch.py``
fans this exact file out byte-identically to every adopter).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

GUARD = Path(__file__).resolve().parents[1] / "bootstrap-killswitch-guard.sh"


def _run(args: list[str], state_file: Path) -> subprocess.CompletedProcess:
    env = {"PATH": "/usr/bin:/bin", "HOME": str(state_file.parent.parent)}
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(state_file)
    return subprocess.run(
        ["bash", str(GUARD), *args],
        capture_output=True,
        text=True,
        env=env,
    )


def test_check_exits_nonzero_when_no_state_file(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    result = _run(["check"], state_file)
    assert result.returncode == 1


def test_status_reports_inactive_when_no_state_file(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    result = _run(["status"], state_file)
    assert result.returncode == 0
    assert json.loads(result.stdout)["active"] is False


def test_on_then_check_reports_active(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    on_result = _run(["on", "diagnosing a venv"], state_file)
    assert on_result.returncode == 0
    assert state_file.exists()

    check_result = _run(["check"], state_file)
    assert check_result.returncode == 0
    assert "diagnosing a venv" in check_result.stderr
    assert check_result.stdout == ""


def test_on_then_off_then_check_reports_inactive(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    _run(["on", "temporary"], state_file)
    off_result = _run(["off"], state_file)
    assert off_result.returncode == 0
    assert not state_file.exists()

    check_result = _run(["check"], state_file)
    assert check_result.returncode == 1


def test_malformed_state_file_fails_open(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    state_file.write_text("not json", encoding="utf-8")
    result = _run(["check"], state_file)
    assert result.returncode == 1


def test_status_reflects_on_state_fields(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    _run(["on", "a reason"], state_file)
    result = _run(["status"], state_file)
    data = json.loads(result.stdout)
    assert data["active"] is True
    assert data["reason"] == "a reason"
    assert data["set_by"]
    assert data["set_at"]


def test_unknown_subcommand_errors(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    result = _run(["bogus"], state_file)
    assert result.returncode == 2


def test_on_detects_live_in_flight_reconcile(tmp_path) -> None:
    """The switch only prevents a NEW reconcile; it warns, rather than
    silently overclaiming success, when one is already running."""
    fake_home = tmp_path / "home"
    lock_dir = fake_home / ".fake-plugin"
    lock_dir.mkdir(parents=True)
    proc = subprocess.Popen(["sleep", "30"])
    try:
        (lock_dir / "reconcile.lock").write_text(str(proc.pid), encoding="utf-8")
        env = {"PATH": "/usr/bin:/bin"}
        env["BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT"] = str(fake_home)
        env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(fake_home / "ks.json")
        result = subprocess.run(
            ["bash", str(GUARD), "on", "test"],
            capture_output=True,
            text=True,
            env=env,
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5)
    assert result.returncode == 0
    assert "WARNING" in result.stdout
    assert "fake-plugin" in result.stdout


def test_on_reports_no_warning_when_no_lock_present(tmp_path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = {"PATH": "/usr/bin:/bin"}
    env["BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT"] = str(fake_home)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(fake_home / "ks.json")
    result = subprocess.run(
        ["bash", str(GUARD), "on", "test"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "WARNING" not in result.stdout


def test_on_ignores_stale_lock_with_dead_pid(tmp_path) -> None:
    fake_home = tmp_path / "home"
    lock_dir = fake_home / ".fake-plugin"
    lock_dir.mkdir(parents=True)
    # A PID astronomically unlikely to be alive right now.
    (lock_dir / "reconcile.lock").write_text("999999999", encoding="utf-8")
    env = {"PATH": "/usr/bin:/bin"}
    env["BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT"] = str(fake_home)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(fake_home / "ks.json")
    result = subprocess.run(
        ["bash", str(GUARD), "on", "test"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "WARNING" not in result.stdout
