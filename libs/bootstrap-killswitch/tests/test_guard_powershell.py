"""Subprocess-level tests for the canonical PowerShell guard
(``bootstrap-killswitch-guard.ps1``) -- exercises the actual vendored
PowerShell script against the real cross-platform state-file contract,
including Windows PowerShell 5.1's known ``Set-Content -Encoding utf8``
BOM footgun (fixed by writing BOM-less UTF-8 directly). Mirrors
``test_guard.py``'s coverage of the Bash guard; this is the independently
implemented sibling every vendored ``.ps1`` copy must keep behaviorally
equivalent to.

Interpreter selection: honors ``BOOTSTRAP_KILLSWITCH_TEST_SHELL`` (a full
path or a bare name resolved via PATH) so CI can explicitly exercise
Windows PowerShell 5.1 (``powershell.exe``) -- the BOM regression this
suite specifically guards against is a PS 5.1 defect that PowerShell 7+
(``pwsh``) does not reproduce, so a suite that only ever runs under
``pwsh`` cannot actually catch it. Falls back to ``pwsh`` (PowerShell 7+,
cross-platform) when unset. Skips cleanly when the selected interpreter
isn't found on PATH (e.g. a minimal Linux CI image with no PowerShell
installed) rather than failing the whole suite.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "bootstrap-killswitch-guard.ps1"
_REQUESTED_SHELL = os.environ.get("BOOTSTRAP_KILLSWITCH_TEST_SHELL", "pwsh")
PWSH = shutil.which(_REQUESTED_SHELL)

pytestmark = pytest.mark.skipif(
    PWSH is None, reason=f"{_REQUESTED_SHELL!r} not found on PATH"
)


def _run(args: list[str], state_file: Path) -> subprocess.CompletedProcess:
    # Inherit the real environment (needed on Windows for e.g. SYSTEMROOT,
    # without which powershell.exe/pwsh often fail to start at all) and only
    # override what the guard actually reads; HOME/USERPROFILE don't matter
    # here since every call passes an explicit state-file override.
    env = dict(os.environ)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(state_file)
    return subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GUARD), *args],
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


def test_on_writes_bom_free_utf8_json(tmp_path) -> None:
    """The specific bug this guard must never regress: Windows PowerShell
    5.1's `Set-Content -Encoding utf8` prepends a UTF-8 BOM, which Python's
    `json.load` (used by both the Bash guard and the agent-machines CLI)
    rejects outright -- an activation via this PowerShell surface could be
    silently reported as inactive by the other two surfaces."""
    state_file = tmp_path / "ks.json"
    on_result = _run(["on", "diagnosing a venv"], state_file)
    assert on_result.returncode == 0

    raw = state_file.read_bytes()
    assert not raw.startswith(b"\xef\xbb\xbf"), "state file must not carry a UTF-8 BOM"

    # The real cross-reader contract: Python's plain json.load must parse it.
    data = json.loads(raw.decode("utf-8"))
    assert data["active"] is True
    assert data["reason"] == "diagnosing a venv"


def test_on_then_check_reports_active(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    _run(["on", "diagnosing a venv"], state_file)
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


def test_cross_reader_python_can_parse_powershell_written_state(tmp_path) -> None:
    """The real integration contract: the Bash guard (and the Python CLI)
    read this exact file with Python's `json.load`. Write via PowerShell,
    read via plain Python -- the two independently-implemented guards must
    always agree on the same state."""
    state_file = tmp_path / "ks.json"
    _run(["on", "cross-reader check"], state_file)
    with state_file.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert data["active"] is True
    assert data["reason"] == "cross-reader check"


def test_on_detects_live_in_flight_reconcile(tmp_path) -> None:
    """The switch only prevents a NEW reconcile; it warns, rather than
    silently overclaiming success, when one is already running."""
    fake_home = tmp_path / "home"
    lock_dir = fake_home / ".fake-plugin"
    lock_dir.mkdir(parents=True)
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        (lock_dir / "reconcile.lock").write_text(str(proc.pid), encoding="utf-8")
        env = dict(os.environ)
        # Not $HOME: Windows PowerShell 5.1's $HOME automatic variable does
        # not honor a reassigned HOME env var the way pwsh/bash do, so
        # redirecting via HOME alone is not reliably testable across both
        # interpreters. The guard supports an explicit scan-root override
        # for exactly this reason.
        env["BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT"] = str(fake_home)
        env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(fake_home / "ks.json")
        result = subprocess.run(
            [PWSH, "-NoProfile", "-File", str(GUARD), "on", "test"],
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
    env = dict(os.environ)
    env["BOOTSTRAP_KILLSWITCH_RECONCILE_SCAN_ROOT"] = str(fake_home)
    env["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = str(fake_home / "ks.json")
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(GUARD), "on", "test"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0
    assert "WARNING" not in result.stdout


def test_unknown_subcommand_errors(tmp_path) -> None:
    state_file = tmp_path / "ks.json"
    result = _run(["bogus"], state_file)
    assert result.returncode == 2
