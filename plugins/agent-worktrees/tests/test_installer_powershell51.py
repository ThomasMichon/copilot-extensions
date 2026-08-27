from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"
SERVICE_UTILS = PLUGIN / "scripts" / "service-utils.ps1"

pytestmark = pytest.mark.guard


def test_windows_provision_bootstraps_uv_before_runtime_build():
    installer = INSTALLER.read_text(encoding="utf-8")
    provision = installer.split("'provision' {", 1)[1].split("'install' {", 1)[0]
    manifest = installer.split("function Write-V3Manifest", 1)[1].split(
        "function Ensure-UvIndex", 1
    )[0]

    assert "if (-not (Ensure-Uv)) { exit 1 }" in provision
    assert provision.index("Ensure-Uv") < provision.index("Deploy-Venv")
    assert "Invoke-NativeCapture" in installer
    assert "[System.IO.File]::WriteAllText" in manifest
    assert "Set-Content -Path $tmp -Encoding UTF8" not in manifest
    assert "if ($env:APPDATA)" in installer
    assert "if ($env:PROGRAMDATA)" in installer
    assert "$pythonPath = Get-BootstrapPython" in installer
    assert "& $pythonPath -m pip config get global.index-url" in installer


def test_uv_bootstrap_python_survives_windows_powershell_argument_passing():
    installer = INSTALLER.read_text(encoding="utf-8")
    bootstrap = installer.split("$bootstrap = @'", 1)[1].split("'@", 1)[0]

    assert '"' not in bootstrap
    assert "missing_ok" not in bootstrap
    assert "$ErrorActionPreference = 'Continue'" in installer


def test_early_installer_utilities_are_powershell_51_safe_ascii():
    utilities = SERVICE_UTILS.read_text(encoding="utf-8")

    assert "#Requires -Version 7.0" not in utilities
    assert "??" not in utilities
    utilities.encode("ascii")


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_stamp_succeeds(tmp_path: Path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    home = tmp_path / "home"
    home.mkdir()
    env = {
        **os.environ,
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    proc = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(INSTALLER),
            "stamp",
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    runtime = home / ".agent-worktrees"
    assert (runtime / "payload-dir").is_file()
    assert (runtime / "stamped-version").is_file()
    assert (home / ".local" / "bin" / "agent-worktrees.cmd").is_file()
