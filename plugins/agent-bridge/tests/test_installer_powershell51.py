from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


PLUGIN = Path(__file__).resolve().parents[1]
INSTALLER = PLUGIN / "scripts" / "install.ps1"
ENGINE = PLUGIN.parents[1] / "libs" / "installer-engine" / "installer-engine.ps1"

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
    engine = ENGINE.read_text(encoding="utf-8")

    native_capture = _function_source(
        engine, "Invoke-NativeCapture", "\nfunction Test-IsSreModuleMismatch {"
    )
    ensure_uv = _function_source(engine, "Ensure-Uv", "\nfunction Get-SignedBasePython {")
    bridge_ensure_uv = _function_source(installer, "Ensure-Uv", "\nfunction New-SignedVenv {")
    signed_venv = _function_source(
        engine, "New-SignedVenv", "\nfunction Write-DeployManifest {"
    )
    update = _function_source(installer, "Invoke-Update", "\n# -- Dispatch")

    assert (
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')" in installer
        or ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in installer
    )
    assert "$exitCode = 1" in native_capture
    assert "} catch {" in native_capture
    assert "$env:AGENT_BRIDGE_UV_BOOTSTRAP_URL" in bridge_ensure_uv
    assert "$env:AGENT_BRIDGE_UV_BOOTSTRAP_SHA256" in bridge_ensure_uv
    assert "Ensure-UvShared -InstallRoot $InstallDir" in bridge_ensure_uv
    assert "[string]$BootstrapVersion = '0.12.6'" in ensure_uv
    assert '/releases/download/$BootstrapVersion/$asset' in ensure_uv
    assert "/releases/latest/" not in ensure_uv
    assert "[Security.Cryptography.SHA256]::Create()" in ensure_uv
    assert "$sha256.ComputeHash($archiveStream)" in ensure_uv
    assert "uv archive SHA-256 mismatch" in ensure_uv
    assert "System.IO.Compression.FileSystem" in ensure_uv
    assert "$client.DownloadFile($url, $archive)" in ensure_uv
    assert "'uv.exe'" in ensure_uv
    assert "'uvx.exe'" in ensure_uv
    assert "Invoke-UvVenvResilient" in signed_venv
    assert "$arguments = @('--python', $PythonVersion)" in signed_venv
    assert "if ($AllowExisting) { $arguments += '--allow-existing' }" in signed_venv
    assert "if ($AllowExisting) { $fallbackArgs += '--allow-existing' }" in signed_venv
    assert "if (-not (Ensure-Uv)) { exit 1 }" in update


@pytest.mark.skipif(os.name != "nt", reason="Windows PowerShell compatibility")
# Regression (coverage-guided-ci's full-matrix local validation pass,
# 2026-10-04): this test's own subprocess call already anticipates a real
# Windows PowerShell 5.1 startup (`timeout=60`), but with no
# `@pytest.mark.timeout` override, `run-plugin-tests.py`'s own blanket
# 30s-per-test pytest-timeout default can fire first under real machine
# contention -- same false-positive class `test_first_install_bootstrap.py
# ::test_posix_lean_provision_installs_resolver_and_launchers_reenter_
# runtime` already hit and fixed the same way: real headroom above the
# test's own internal subprocess ceiling, not a global timeout bump.
@pytest.mark.timeout(90)
def test_powershell_51_corrupt_cached_uv_fails_cleanly(tmp_path: Path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    installer = INSTALLER.read_text(encoding="utf-8")
    engine = ENGINE.read_text(encoding="utf-8")
    native_capture = _function_source(
        engine, "Invoke-NativeCapture", "\nfunction Test-IsSreModuleMismatch {"
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
def test_powershell_51_rejects_uv_archive_with_wrong_digest(tmp_path: Path):
    powershell = shutil.which("powershell.exe")
    if not powershell:
        pytest.skip("Windows PowerShell 5.1 is unavailable")

    installer = INSTALLER.read_text(encoding="utf-8")
    engine = ENGINE.read_text(encoding="utf-8")
    native_capture = _function_source(
        engine, "Invoke-NativeCapture", "\nfunction Test-IsSreModuleMismatch {"
    )
    ensure_uv = _function_source(installer, "Ensure-Uv", "\nfunction Get-PayloadHash {")

    install_dir = tmp_path / "home" / ".agent-bridge"
    tool_dir = install_dir / "tool"
    tool_dir.mkdir(parents=True)
    arch = os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get(
        "PROCESSOR_ARCHITECTURE"
    )
    assets = {
        "AMD64": "uv-x86_64-pc-windows-msvc.zip",
        "ARM64": "uv-aarch64-pc-windows-msvc.zip",
    }
    if arch not in assets:
        pytest.skip(f"unsupported Windows architecture: {arch}")
    archive = tmp_path / assets[arch]
    archive.write_bytes(b"not the pinned uv archive")

    def ps_quote(value: str) -> str:
        return value.replace("'", "''")

    script = tmp_path / "wrong-digest.ps1"
    script.write_text(
        "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$InstallDir = '{ps_quote(str(install_dir))}'",
                "function Write-Fail { param([string]$Msg) Write-Host \"[FAIL] $Msg\" }",
                "function Write-Ok { param([string]$Msg) Write-Host \"[OK] $Msg\" }",
                native_capture,
                ensure_uv,
                "if (Ensure-Uv) { throw 'wrong digest unexpectedly succeeded' }",
                "Write-Output 'EXPECTED_DIGEST_FAILURE'",
            ]
        ),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "AGENT_BRIDGE_UV_BOOTSTRAP_URL": archive.as_uri(),
        "AGENT_BRIDGE_UV_BOOTSTRAP_SHA256": "0" * 64,
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
    output = " ".join(proc.stdout.split())
    assert "EXPECTED_DIGEST_FAILURE" in output
    assert "uv archive SHA-256 mismatch" in output
    assert not (tool_dir / "uv.exe").exists()
    assert not (tool_dir / "uvx.exe").exists()


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
    payload_dir = home / ".agent-bridge" / "payload-dir"
    assert payload_dir.is_file()
    assert (home / ".local" / "bin" / "agent-bridge.cmd").is_file()
    snapshot = Path(payload_dir.read_text(encoding="utf-8").strip())
    assert (snapshot / "scripts" / "installer-engine.ps1").is_file()
    assert (snapshot / "scripts" / "installer-engine.sh").is_file()
    assert (snapshot / "scripts" / "install.ps1").is_file()
    assert (snapshot / "scripts" / "install.sh").is_file()
    pyproject = (snapshot / "pyproject.toml").read_text(encoding="utf-8")
    assert 'agent-ssh-manager = { path = "libs/ssh-manager" }' in pyproject
    assert 'agent-procutil = { path = "libs/agent-procutil" }' in pyproject
    assert 'agent-machine-transport = { path = "libs/machine-transport" }' in pyproject
    assert (snapshot / "libs" / "machine-transport" / "src" / "machine_transport" / "registry.py").is_file()
    nested_transport = (snapshot / "libs" / "machine-transport" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'agent-remote-login-shell = { path = "../remote-login-shell" }' in nested_transport
    assert "editable = true" not in nested_transport
    assert 'agent-ssh-manager = { path = "../../libs/ssh-manager", editable = true }' not in pyproject
    nested_ssh_manager = (snapshot / "libs" / "ssh-manager" / "pyproject.toml").read_text(encoding="utf-8")
    nested_plugin_activation = (snapshot / "libs" / "plugin-activation" / "pyproject.toml").read_text(encoding="utf-8")
    assert 'agent-procutil = { path = "../agent-procutil" }' in nested_ssh_manager
    assert 'agent-procutil = { path = "../agent-procutil", editable = true }' not in nested_ssh_manager
    assert 'agent-dropin-registry = { path = "../dropin-registry" }' in nested_plugin_activation
    assert 'agent-plugin-resolve = { path = "../plugin-resolve" }' in nested_plugin_activation
    assert 'agent-dropin-registry = { path = "../dropin-registry", editable = true }' not in nested_plugin_activation
    assert 'agent-plugin-resolve = { path = "../plugin-resolve", editable = true }' not in nested_plugin_activation
    assert ". (Join-Path $PSScriptRoot 'installer-engine.ps1')" in (
        snapshot / "scripts" / "install.ps1"
    ).read_text(encoding="utf-8")
    assert '. "$SCRIPT_DIR/installer-engine.sh"' in (
        snapshot / "scripts" / "install.sh"
    ).read_text(encoding="utf-8")
