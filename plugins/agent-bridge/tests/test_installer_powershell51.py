from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"

pytestmark = pytest.mark.guard


def _function_source(source: str, name: str, next_marker: str) -> str:
    start_marker = f"function {name} {{"
    assert source.count(start_marker) == 1, f"missing unique {start_marker!r}"
    assert next_marker in source, f"missing delimiter {next_marker!r}"

    start = source.index(start_marker)
    end = source.index(next_marker, start + len(start_marker))
    assert end > start, f"{next_marker!r} does not follow {start_marker!r}"
    return source[start:end]


def test_first_use_installer_captures_python_probes_and_bootstraps_uv():
    installer = INSTALLER.read_text(encoding="utf-8")

    native_capture = _function_source(
        installer, "Invoke-NativeCapture", "\nfunction Ensure-Uv {"
    )
    ensure_uv = _function_source(installer, "Ensure-Uv", "\nfunction Get-PayloadHash {")
    signed_venv = _function_source(
        installer, "New-SignedVenv", "\nfunction Install-SiblingPlugins {"
    )
    update = _function_source(installer, "Invoke-Update", "\n# -- Dispatch")

    assert "$exitCode = 1" in native_capture
    assert "} catch {" in native_capture
    assert "$env:AGENT_BRIDGE_UV_BOOTSTRAP_URL" in ensure_uv
    assert "System.IO.Compression.FileSystem" in ensure_uv
    assert "$client.DownloadFile($url, $archive)" in ensure_uv
    assert "'uv.exe'" in ensure_uv
    assert "'uvx.exe'" in ensure_uv
    assert "Invoke-NativeCapture {" in signed_venv
    assert "& uv venv $VenvDir --python 3.10 --allow-existing" in signed_venv
    assert "& uv venv $VenvDir --allow-existing" in signed_venv
    assert "if (-not (Ensure-Uv)) { exit 1 }" in update


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
def test_powershell_51_corrupt_cached_uv_fails_cleanly(tmp_path: Path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    installer = INSTALLER.read_text(encoding="utf-8")
    native_capture = _function_source(
        installer, "Invoke-NativeCapture", "\nfunction Ensure-Uv {"
    )
    ensure_uv = _function_source(installer, "Ensure-Uv", "\nfunction Get-PayloadHash {")

    install_dir = tmp_path / "home" / ".agent-bridge"
    tool_dir = install_dir / "tool"
    tool_dir.mkdir(parents=True)
    uv_path = tool_dir / "uv.exe"
    uvx_path = tool_dir / "uvx.exe"
    uv_path.write_bytes(b"not a Windows executable")
    uvx_path.write_bytes(b"stale companion")

    def ps_quote(value: str) -> str:
        return value.replace("'", "''")

    script = tmp_path / "corrupt-uv.ps1"
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$InstallDir = '{ps_quote(str(install_dir))}'",
                "function Write-Fail { param([string]$Msg) Write-Host \"[FAIL] $Msg\" }",
                "function Write-Ok { param([string]$Msg) Write-Host \"[OK] $Msg\" }",
                native_capture,
                ensure_uv,
                "if (Ensure-Uv) { throw 'corrupt uv unexpectedly succeeded' }",
                f"if (Test-Path -LiteralPath '{ps_quote(str(uv_path))}') {{ throw 'uv.exe was not removed' }}",
                f"if (Test-Path -LiteralPath '{ps_quote(str(uvx_path))}') {{ throw 'uvx.exe was not removed' }}",
                "Write-Output 'EXPECTED_FAILURE'",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AGENT_BRIDGE_UV_BOOTSTRAP_URL": (tmp_path / "missing-{asset}").as_uri(),
        "PATH": str(Path(powershell).parent),
    }
    proc = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert proc.returncode == 0, proc.stderr
    assert "EXPECTED_FAILURE" in proc.stdout
    assert "Failed to vendor uv" in proc.stdout
    assert "Retry the installer, or install uv" in proc.stdout
    assert not uv_path.exists()
    assert not uvx_path.exists()


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
