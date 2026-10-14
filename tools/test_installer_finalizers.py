"""Exercise production cleanup in a still-live PowerShell host."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


@pytest.mark.skipif(os.name != "nt", reason="Windows FileShare.None contract")
def test_failed_construction_cleanup_releases_lock_in_same_powershell_host(tmp_path):
    source = (
        Path(__file__).resolve().parents[1] / "plugins" / "agent-pull-requests"
        / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")
    functions = source[
        source.index("function Enter-InstallLock {"):source.index("function Invoke-Stamp {")
    ]
    finalizer = source[source.index("} finally {\n    try {") + len("} finally {"):].rsplit("}", 1)[0]
    script = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$InstallDir = '{str(tmp_path).replace(chr(39), chr(39) * 2)}'\n"
        + functions + "\n"
        "function Stop-RuntimeConstruction { throw 'construction-cleanup-failed' }\n"
        "$needsInstallLock = $true\n"
        "if (-not (Enter-InstallLock -TimeoutSec 0)) { throw 'initial acquisition failed' }\n"
        "try { try { } finally {\n" + finalizer + "\n} } catch {\n"
        "if ($_.Exception.Message -ne 'construction-cleanup-failed') { throw }\n"
        "}\n"
        "if ($script:InstallLockHandle) { throw 'handle survived cleanup failure' }\n"
        "if (-not (Enter-InstallLock -TimeoutSec 0)) { throw 'same-host reacquisition failed' }\n"
        "Exit-InstallLock\n"
        "Write-Output 'same-host-lock-reacquired'\n"
    )
    result = subprocess.run(
        [shutil.which("powershell.exe"), "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=15, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "same-host-lock-reacquired"
