from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard


def test_first_use_installer_captures_python_probes_and_bootstraps_uv():
    installer = INSTALLER.read_text(encoding="utf-8")

    assert "function Invoke-NativeCapture" in installer
    assert "Invoke-NativeCapture {" in installer
    assert "if (-not (Ensure-Uv)) { exit 1 }" in installer
    assert "$env:AGENT_BRIDGE_UV_BOOTSTRAP_URL" in installer
    assert "System.IO.Compression.FileSystem" in installer
    assert "$client.DownloadFile($url, $archive)" in installer
    assert "no Python is available to bootstrap" not in installer
    update = installer.split("function Invoke-Update", 1)[1]
    assert "if (-not (Ensure-Uv)) { exit 1 }" in update


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
    assert (home / ".agent-bridge" / "payload-dir").is_file()
    assert (home / ".local" / "bin" / "agent-bridge.cmd").is_file()
