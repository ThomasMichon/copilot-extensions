from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import tomllib

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


@pytest.mark.parametrize(
    "host",
    [host for host in (shutil.which("powershell.exe"), shutil.which("pwsh")) if host],
)
def test_stop_daemon_final_verdict_handles_listener_cardinality(host):
    source = INSTALLER.read_text(encoding="utf-8-sig")
    functions = _function_source(
        source, "Get-PortListeners", "\nfunction Stop-DaemonProcesses {",
    ) + _function_source(
        source, "Stop-DaemonProcesses", "\nfunction Invoke-Drain {",
    )
    script = """
Set-StrictMode -Version 2.0
$ErrorActionPreference = 'Stop'
$Port = 41001
$RelayPort = 41002
$VenvPython = ''
$LinkPython = ''
function Get-NetTCPConnection {
    param([int]$LocalPort, [string]$ErrorAction)
    $script:probeCalls++
    if ($script:probeCalls -le 2) {
        [pscustomobject]@{ State = 'Listen'; OwningProcess = 12345 }
    } else {
        $listeners = if ($LocalPort -eq $Port) { $case.service } else { $case.relay }
        foreach ($listener in $listeners) {
            [pscustomobject]@{ State = 'Listen'; OwningProcess = $listener }
        }
    }
}
function Stop-Process {
    param([int]$Id, [switch]$Force, [string]$ErrorAction)
    $script:stopped += $Id
}
function Start-Sleep { param([int]$Milliseconds) }
""" + functions + """
$cases = @(
    @{ service = @(); relay = @(); expected = $true },
    @{ service = @(12345); relay = @(); expected = $false },
    @{ service = @(); relay = @(12345); expected = $false },
    @{ service = @(12345, 12346); relay = @(); expected = $false },
    @{ service = @(); relay = @(12345, 12346); expected = $false }
)
foreach ($case in $cases) {
    $script:probeCalls = 0
    $script:stopped = @()
    $result = Stop-DaemonProcesses -MaxAttempts 1
    if ($result -isnot [bool] -or $result -ne $case.expected) {
        throw 'Incorrect final listener verdict'
    }
    if ($script:stopped.Count -ne 1 -or $script:stopped[0] -ne 12345) {
        throw 'Unexpected stop targets'
    }
}
Write-Output 'listener verdicts passed'
"""
    result = subprocess.run(
        [host, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "listener verdicts passed"


@pytest.mark.parametrize("host_name", ["powershell.exe", "pwsh"])
def test_codec_snapshot_embeds_every_declared_library_under_real_powershell(tmp_path, host_name):
    host = shutil.which(host_name)
    if not host:
        pytest.skip(f"{host_name} is unavailable")
    references = tomllib.loads((PLUGIN / "pyproject.toml").read_text(encoding="utf-8"))[
        "tool"
    ]["uv"]["sources"]
    libraries = {package: Path(source["path"]).name for package, source in references.items()}
    sources = tmp_path / "authoring-libs"
    sources.mkdir()
    codec = PLUGIN.parents[1] / "libs/worktree-identity"
    for package, name in libraries.items():
        destination = sources / name
        if name in {"worktree-identity", "process-telemetry"}:
            shutil.copytree(PLUGIN.parents[1] / "libs" / name, destination,
                           ignore=shutil.ignore_patterns(
                "build", "dist", "__pycache__", "*.egg-info", ".pytest_cache", ".ruff_cache",
            ))
        else:
            destination.mkdir()
            (destination / "pyproject.toml").write_text(
                f'[project]\nname = "{package}"\nversion = "1.0.0"\n', encoding="utf-8",
            )
    snapshot = tmp_path / "standalone"
    snapshot.mkdir()
    shutil.copy2(PLUGIN / "pyproject.toml", snapshot / "pyproject.toml")
    source = INSTALLER.read_text(encoding="utf-8")
    materializer = _function_source(
        source, "Materialize-SnapshotVendoredLibs", "\nfunction Materialize-SnapshotInstallerEngine {",
    )
    root = str(sources).replace("'", "''")
    target = str(snapshot).replace("'", "''")
    script = f"""
$ErrorActionPreference = 'Stop'
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$LibraryRoot = '{root}'
function Resolve-SshManager {{ return Join-Path $LibraryRoot 'ssh-manager' }}
function Resolve-CredentialRelay {{ return Join-Path $LibraryRoot 'credential-relay' }}
function Resolve-Zdd {{ return Join-Path $LibraryRoot 'zdd' }}
function Resolve-SingleInstanceLease {{ return Join-Path $LibraryRoot 'single-instance-lease' }}
function Resolve-ConfigMigrate {{ return Join-Path $LibraryRoot 'config-migrate' }}
function Resolve-VendoredLib {{ param($LibName) return Join-Path $LibraryRoot $LibName }}
{materializer}
Materialize-SnapshotVendoredLibs -SnapshotDir '{target}'
"""
    result = subprocess.run(
        [
            host,
            "-NoProfile", "-NonInteractive", "-Command", script,
        ],
        capture_output=True, text=True, timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    project = tomllib.loads((snapshot / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["tool"]["uv"]["sources"] == {
        package: {"path": f"libs/{name}"} for package, name in libraries.items()
    }
    for name in libraries.values():
        assert (snapshot / "libs" / name / "pyproject.toml").is_file()
    embedded = snapshot / "libs/worktree-identity/src/worktree_identity/__init__.py"
    assert embedded.read_bytes() == (codec / "src/worktree_identity/__init__.py").read_bytes()
    shutil.move(str(sources), str(tmp_path / "authoring-unavailable"))
    imported = subprocess.run(
        [
            sys.executable, "-I", "-S", "-c",
            "import sys; sys.path.insert(0,sys.argv[1]); "
            "sys.path.insert(0,sys.argv[2]); "
            "from process_telemetry import SinkSlot; assert not SinkSlot().enabled(); "
            "from worktree_identity import WorktreeMachineIdentity,parse_machine_identity; "
            "identity=WorktreeMachineIdentity('host','wsl','guest'); "
            "assert parse_machine_identity(identity.to_record_fields()) == identity",
            str(snapshot / "libs/worktree-identity/src"),
            str(snapshot / "libs/process-telemetry/src"),
        ],
        capture_output=True, text=True, timeout=20,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert imported.returncode == 0, imported.stderr


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
