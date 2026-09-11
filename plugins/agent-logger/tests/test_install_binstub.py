"""POSIX installer regressions for the agent-logger binstub."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

_PLUGIN_ROOT = Path(__file__).resolve().parents[1]
_INSTALL_SH = _PLUGIN_ROOT / "scripts" / "install.sh"
_INSTALL_PS1 = _PLUGIN_ROOT / "scripts" / "install.ps1"
_COMMANDS = (
    "agent-logger",
    "collate-session",
    "read-session-digest",
    "prepare-session-log",
    "ramp-up-session",
    "session-sync",
)


@pytest.mark.skipif(os.name == "nt", reason="POSIX installer behavior")
def test_stamp_replaces_dangling_legacy_binstub(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    home = tmp_path / "home"
    local_bin = home / ".local" / "bin"
    local_bin.mkdir(parents=True)
    binstub = local_bin / "agent-logger"
    binstub.symlink_to(home / ".agent-logger" / ".venv" / "bin" / "agent-logger")

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        }
    )
    result = subprocess.run(
        ["bash", str(payload / "scripts" / "install.sh"), "stamp"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert binstub.is_file()
    assert not binstub.is_symlink()
    assert "agent-logger binstub -- self-provisioning" in binstub.read_text(
        encoding="utf-8"
    )
    assert "stamped-version: No such file or directory" not in result.stderr
    for command in _COMMANDS:
        command_path = local_bin / command
        assert command_path.is_file(), command
        assert os.access(command_path, os.X_OK), command
    auxiliary = (local_bin / "collate-session").read_text(encoding="utf-8")
    assert 'exec "$_shim" "$@"' in auxiliary
    assert "command -v collate-session" not in auxiliary
    payload_dir = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert payload_dir.is_dir()
    assert payload_dir != _PLUGIN_ROOT
    assert (payload_dir / "bin" / "collate-session").is_file()
    shutil.rmtree(payload)
    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow = shadow_bin / "collate-session"
    shadow.write_text("#!/bin/sh\nexit 91\n", encoding="utf-8")
    shadow.chmod(0o755)
    env["AGENT_LOGGER_NO_SELFPROVISION"] = "1"
    env["PATH"] = f"{shadow_bin}{os.pathsep}{env.get('PATH', '')}"
    delegated = subprocess.run(
        [str(local_bin / "collate-session"), "--example-argument"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode == 1
    assert "runtime not provisioned" in delegated.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows installer behavior")
def test_windows_stamp_publishes_complete_command_family(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        }
    )
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell is not None
    result = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    local_bin = home / ".local" / "bin"
    for command in _COMMANDS:
        assert (local_bin / f"{command}.ps1").is_file(), command
        assert (local_bin / f"{command}.cmd").is_file(), command
    auxiliary = (local_bin / "collate-session.ps1").read_text(encoding="utf-8")
    assert "COPILOT_PLUGIN_ROOT" in auxiliary
    assert r"bin\$($_command).ps1" in auxiliary
    shutil.rmtree(payload)
    env["AGENT_LOGGER_NO_SELFPROVISION"] = "1"
    delegated = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(local_bin / "collate-session.ps1"),
            "--example-argument",
        ],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode == 1
    assert "runtime not provisioned" in delegated.stderr


def test_installers_preserve_payload_delegating_auxiliary_wrappers() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")

    assert "function Write-Binstubs" in install_ps1
    assert "Deploy-AuxiliaryCompatibilityBinstubs" in install_ps1.split(
        "function Write-Binstubs", 1
    )[1].split("function Install-Package", 1)[0]
    install_package = install_sh.split("install_package() {", 1)[1].split(
        "write_units() {", 1
    )[0]
    assert "deploy_auxiliary_compatibility_binstubs" in install_package
    assert 'ln -sf "${LINK_DIR}/bin/${name}"' not in install_package
    assert "publish_payload_snapshot" in install_package
    assert "Publish-PayloadSnapshot | Out-Null" in install_ps1.split(
        "function Install-Package", 1
    )[1].split("function Register-SyncTask", 1)[0]


def test_windows_update_rebinds_existing_sync_task_runtime() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    update_binding = install_ps1.split(
        "function Update-SyncTaskBinding", 1
    )[1].split("function Deploy-SelfProvisioningBinstub", 1)[0]
    update_action = install_ps1.split("'update' {", 1)[1].split(
        "'uninstall' {", 1
    )[0]

    assert "Set-ScheduledTask" in update_binding
    assert "$action = New-SyncTaskAction" in update_binding
    assert "-Action $action" in update_binding
    assert "no provisioned runtime" in update_binding
    assert "-Trigger" not in update_binding
    assert "Update-SyncTaskBinding" in update_action


def test_windows_sync_task_writes_wrap_both_register_and_update_branches() -> None:
    """Both Register-SyncTask branches (existing-task update AND new-task
    creation), not just Update-SyncTaskBinding, must downgrade an Access
    Denied failure to a warning instead of aborting the install (a stale
    admin-only task ACL can just as easily block *creation* on a machine that
    has no task yet, e.g. after `schtasks /Delete`)."""
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    register_sync_task = install_ps1.split(
        "function Register-SyncTask", 1
    )[1].split("function Test-IsAccessDenied", 1)[0]

    assert register_sync_task.count("try {") == 2
    assert register_sync_task.count("Write-TaskAccessDeniedWarning") == 2
    assert "Write-TaskAccessDeniedWarning $_ 'update'" in register_sync_task
    assert "Write-TaskAccessDeniedWarning $_ 'register'" in register_sync_task


def _extract_ps1_functions(*names: str) -> str:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    chunks = []
    for name in names:
        rest = install_ps1.split(f"function {name}", 1)[1]
        # Each of these functions is closed by a `}` at column 0 (the file's
        # top-level function-closing convention).
        body = rest.split("\n}\n", 1)[0]
        chunks.append(f"function {name}{body}\n}}")
    return "\n\n".join(chunks)


@pytest.mark.parametrize("shell", ["powershell.exe", "pwsh"])
def test_windows_task_access_denied_classifier_downgrades_and_rethrows(
    tmp_path: Path, shell: str
) -> None:
    """Regression for both catch outcomes of the stale-ACL handling: an
    Access Denied error is downgraded to a warning (install continues), while
    any other exception still propagates (install still fails loudly). Runs
    under both Windows PowerShell 5.1 (`powershell.exe`) and PowerShell 7
    (`pwsh`) since the installer must work on either."""
    exe = shutil.which(shell)
    if not exe:
        pytest.skip(f"{shell} is not installed")
    harness = tmp_path / f"harness-{shell.replace('.exe', '')}.ps1"
    harness.write_text(
        _extract_ps1_functions("Test-IsAccessDenied", "Write-TaskAccessDeniedWarning")
        + """

function Write-Warn2 { param([string]$m) Write-Host "WARN: $m" }
$TaskName = 'Test Task'

$deniedRecord = $null
try { throw [System.UnauthorizedAccessException]::new('Access is denied.') }
catch { $deniedRecord = $_ }

$otherRecord = $null
try { throw [System.IO.IOException]::new('disk full') }
catch { $otherRecord = $_ }

Write-TaskAccessDeniedWarning $deniedRecord 'update'
Write-Host 'DENIED-HANDLED-WITHOUT-THROW'

try {
    Write-TaskAccessDeniedWarning $otherRecord 'update'
    Write-Host 'SHOULD-NOT-REACH-HERE'
} catch {
    Write-Host "OTHER-RETHROWN: $($_.Exception.Message)"
}
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [exe, "-NoProfile", "-File", str(harness)],
        check=True,
        capture_output=True,
        text=True,
    )
    assert "WARN: could not update scheduled task 'Test Task' (Access is denied)" in result.stdout
    assert "DENIED-HANDLED-WITHOUT-THROW" in result.stdout
    assert "OTHER-RETHROWN: disk full" in result.stdout
    assert "SHOULD-NOT-REACH-HERE" not in result.stdout


def test_installers_scope_sync_supervision_by_install_root() -> None:
    install_ps1 = _INSTALL_PS1.read_text(encoding="utf-8")
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")

    assert 'TIMER_NAME="agent-logger-sync${SERVICE_SUFFIX:+-$SERVICE_SUFFIX}"' in install_sh
    assert "Environment=AGENT_LOGGER_HOME=${INSTALL_DIR}" in install_sh
    assert "Agent Logger Session Sync - $serviceSuffix" in install_ps1
    assert "$TaskLauncher = Join-Path (Join-Path $InstallDir 'bin') 'session-sync-task.ps1'" in install_ps1
    assert "$env:AGENT_LOGGER_HOME = $_root" in install_ps1


def test_posix_snapshot_uses_self_staged_payload_not_original() -> None:
    install_sh = _INSTALL_SH.read_text(encoding="utf-8")
    publisher = install_sh.split("publish_payload_snapshot() {", 1)[1].split(
        "# Cheap 'stamp'", 1
    )[0]

    assert 'cp -a "${PLUGIN_DIR}/."' in publisher
    assert "COPILOT_PLUGIN_STAGED_FROM" not in publisher


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is required")
def test_provision_publishes_durable_compatibility_wrappers(
    tmp_path: Path,
) -> None:
    payload = tmp_path / "payload"
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    home = tmp_path / "home"
    home.mkdir()
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        }
    )
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "provision",
        ]
        wrapper = home / ".local" / "bin" / "collate-session.ps1"
        invoke = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(wrapper),
            "--help",
        ]
    else:
        command = ["bash", str(payload / "scripts" / "install.sh"), "provision"]
        wrapper = home / ".local" / "bin" / "collate-session"
        invoke = [str(wrapper), "--help"]

    provision = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert provision.returncode == 0, provision.stderr
    snapshot = Path(
        (home / ".agent-logger" / "payload-dir").read_text(encoding="utf-8").strip()
    )
    assert snapshot.is_dir()
    assert wrapper.is_file()
    assert "payload-dir" in wrapper.read_text(encoding="utf-8")

    sentinel = snapshot / ".snapshot-sentinel"
    sentinel.write_text("keep", encoding="utf-8")
    repeat = [*command[:-1], "stamp"]
    stamped = subprocess.run(
        repeat,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert stamped.returncode == 0, stamped.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"

    shutil.rmtree(payload)
    delegated = subprocess.run(
        invoke,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert delegated.returncode != 127
    assert "owning payload shim not found" not in delegated.stderr


def test_scoped_stamp_avoids_global_compatibility_wrappers(tmp_path: Path) -> None:
    payload = tmp_path / "payload"
    shutil.copytree(
        _PLUGIN_ROOT,
        payload,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            "tests",
        ),
    )
    home = tmp_path / "home"
    home.mkdir()
    install_dir = (
        home
        / ".copilot-extensions"
        / "marketplaces"
        / "example--1234"
        / "plugins"
        / "agent-logger"
    )
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "USERPROFILE": str(home),
            "LOCALAPPDATA": str(home / "AppData" / "Local"),
            "COPILOT_PLUGIN_INSTALL_STAGED": "1",
        }
    )
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell")
        assert powershell is not None
        command = [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(payload / "scripts" / "install.ps1"),
            "stamp",
            "-InstallDir",
            str(install_dir),
        ]
    else:
        command = [
            "bash",
            str(payload / "scripts" / "install.sh"),
            "stamp",
            "--install-dir",
            str(install_dir),
        ]
    result = subprocess.run(
        command,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert (install_dir / "payload-dir").is_file()
    local_bin = home / ".local" / "bin"
    for command_name in _COMMANDS:
        assert not (local_bin / command_name).exists()
        assert not (local_bin / f"{command_name}.ps1").exists()
        assert not (local_bin / f"{command_name}.cmd").exists()
