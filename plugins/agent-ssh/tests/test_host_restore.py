from __future__ import annotations

import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from agent_ssh import host_restore

PLUGIN = host_restore.Path(__file__).parents[1]
INSTALL_HOST = (
    PLUGIN / "transports" / "dtssh" / "scripts" / "install-host.ps1"
)
PWSH = shutil.which("pwsh")
SSH_KEYGEN = shutil.which("ssh-keygen")


def _setup(tmp_path, monkeypatch):
    payload = tmp_path / "agent-ssh"
    script = payload / "transports" / "dtssh" / "scripts" / "install-host.ps1"
    script.parent.mkdir(parents=True)
    script.write_text("", encoding="utf-8")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", str(payload))
    monkeypatch.setattr(host_restore.platform, "system", lambda: "Windows")
    monkeypatch.setattr(host_restore.shutil, "which", lambda _name: "pwsh")
    monkeypatch.setattr(host_restore.time, "sleep", lambda _seconds: None)
    return script


def test_dtssh_dry_run_uses_status(tmp_path, monkeypatch):
    script = _setup(tmp_path, monkeypatch)
    captured = {}

    def run(command, **kwargs):
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout="WARNING: host not running",
            stderr="",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is True
    assert result["healthy"] is False
    assert result["would_change"] is True
    assert result["applied"] is False
    assert str(script) in captured["command"]
    assert "status" in captured["command"]
    assert "-SkipLogin" not in captured["command"]


def test_dtssh_apply_updates_existing_binary_without_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    captured = {}
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        captured["command"] = command
        if "status" in command:
            return SimpleNamespace(
                returncode=0,
                stdout=(
                    "dtssh host healthy\nwatchdog running\n"
                    "tunnel example: 1 host connection(s)"
                ),
                stderr="",
            )
        return SimpleNamespace(returncode=0, stdout="installed", stderr="")

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is True
    assert result["applied"] is True
    assert any("update" in command for command in calls)
    assert any("-SkipLogin" in command for command in calls)
    assert result["healthy"] is True


def test_dtssh_apply_over_ssh_launches_detached_and_requires_verification(
    tmp_path, monkeypatch
):
    script = _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    captured = {}

    def run(command, **kwargs):
        assert "user" in command
        return SimpleNamespace(
            returncode=0,
            stdout='{"status": "Logged in"}',
            stderr="",
        )

    def spawn(command):
        captured["command"] = command
        return True, "C:\\logs\\restore-host-detached.log"

    monkeypatch.setattr(host_restore.subprocess, "run", run)
    monkeypatch.setattr(host_restore, "_spawn_dtssh_update_via_wmi", spawn)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is True
    assert result["detached"] is True
    assert result["verification_required"] is True
    assert result["applied"] is False
    assert result["healthy"] is False
    assert str(script) in captured["command"]
    assert "update" in captured["command"]
    assert "-Alias" in captured["command"]
    assert "example-host" in captured["command"]
    assert "-SkipLogin" in captured["command"]


def test_dtssh_apply_over_ssh_reports_detached_launch_failure(
    tmp_path, monkeypatch
):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setenv("SSH_CLIENT", "192.0.2.1 50000 22")
    monkeypatch.setattr(
        host_restore.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=0,
            stdout='{"status": "Logged in"}',
            stderr="",
        ),
    )
    monkeypatch.setattr(
        host_restore,
        "_spawn_dtssh_update_via_wmi",
        lambda command: (False, "broker failed"),
    )

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is False
    assert result["detached"] is False
    assert result["verification_required"] is False
    assert result["applied"] is False
    assert result["error"] == "broker failed"


def test_dtssh_restore_launches_helpers_without_console_windows(
    tmp_path, monkeypatch
):
    _setup(tmp_path, monkeypatch)
    calls = []

    def run(command, **kwargs):
        calls.append(kwargs)
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "dtssh host healthy\nwatchdog running\n"
                "tunnel example: 1 host connection(s)"
            ),
            stderr="",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)
    monkeypatch.setattr(
        host_restore,
        "no_window_kwargs",
        lambda: {"creationflags": 0x08000000},
    )

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is True
    assert calls
    assert all(call["creationflags"] == 0x08000000 for call in calls)


def test_dtssh_restore_rejects_unsupported_platform(monkeypatch):
    monkeypatch.setattr(host_restore.platform, "system", lambda: "Linux")

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is False
    assert "only on Windows" in result["error"]


def test_unknown_transport_is_reported_before_platform(monkeypatch):
    monkeypatch.setattr(host_restore.platform, "system", lambda: "Linux")

    result = host_restore.restore_host(
        "unknown",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is False
    assert result["error"] == "unsupported host transport: unknown"


def test_dtssh_restore_blocks_without_login(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        host_restore.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(
            returncode=1,
            stdout="Not logged in",
            stderr="",
        ),
    )

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=True,
    )

    assert result["ok"] is False
    assert result["blocked"] == "authentication"


def test_nonzero_status_is_never_healthy(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def run(command, **kwargs):
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        return SimpleNamespace(
            returncode=1,
            stdout="host running\nwatchdog running\n"
            "tunnel example: 1 host connection(s)",
            stderr="status failed",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is False
    assert result["healthy"] is False
    assert result["would_change"] is True


def test_pending_durable_identity_is_not_healthy(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)

    def run(command, **kwargs):
        if "user" in command:
            return SimpleNamespace(
                returncode=0,
                stdout='{"status": "Logged in"}',
                stderr="",
            )
        return SimpleNamespace(
            returncode=0,
            stdout=(
                "host running\n"
                "watchdog running\n"
                "tunnel example: 1 host connection(s)\n"
                "durable host identity: pending first successful host launch"
            ),
            stderr="",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    result = host_restore.restore_host(
        "dtssh",
        "example-host",
        2222,
        apply=False,
    )

    assert result["ok"] is True
    assert result["healthy"] is False
    assert result["would_change"] is True


def test_payload_root_uses_runtime_marker(tmp_path, monkeypatch):
    home = tmp_path / "home"
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "plugin.json").write_text(
        '{"name": "agent-ssh"}',
        encoding="utf-8",
    )
    marker = home / ".agent-ssh" / "payload-dir"
    marker.parent.mkdir(parents=True)
    marker.write_text(str(payload), encoding="utf-8")
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(host_restore.Path, "home", lambda: home)

    assert host_restore._payload_root() == payload.resolve()


def test_payload_root_prefers_current_marketplace_over_stale_marker(
    tmp_path, monkeypatch
):
    home = tmp_path / "home"
    stale = tmp_path / "stale"
    current = (
        home
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
        / "agent-ssh"
    )
    for payload, version in ((stale, "0.1.0-dev1"), (current, "0.1.0-dev2")):
        payload.mkdir(parents=True)
        (payload / "plugin.json").write_text(
            json.dumps(
                {
                    "name": "agent-ssh",
                    "version": version,
                }
            ),
            encoding="utf-8",
        )
    state = home / ".agent-ssh"
    state.mkdir(parents=True)
    (state / "payload-dir").write_text(str(stale), encoding="utf-8")
    (state / "deploy-manifest.json").write_text(
        json.dumps(
            {
                "source": {
                    "kind": "marketplace",
                    "repo": "copilot-extensions",
                    "plugin": "agent-ssh",
                    "version": "0.1.0-dev2",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(host_restore.Path, "home", lambda: home)

    assert host_restore._payload_root() == current.resolve()


def test_payload_root_keeps_local_deployment_marker(tmp_path, monkeypatch):
    home = tmp_path / "home"
    local = tmp_path / "local"
    marketplace = (
        home
        / ".copilot"
        / "installed-plugins"
        / "copilot-extensions"
        / "agent-ssh"
    )
    for payload, version in ((local, "0.1.0-dev2"), (marketplace, "0.1.0-dev1")):
        payload.mkdir(parents=True)
        (payload / "plugin.json").write_text(
            json.dumps({"name": "agent-ssh", "version": version}),
            encoding="utf-8",
        )
    state = home / ".agent-ssh"
    state.mkdir(parents=True)
    (state / "payload-dir").write_text(str(local), encoding="utf-8")
    (state / "deploy-manifest.json").write_text(
        json.dumps(
            {
                "source": {
                    "kind": "local",
                    "repo": "copilot-extensions",
                    "plugin": "agent-ssh",
                    "version": "0.1.0-dev2",
                }
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.setattr(host_restore.Path, "home", lambda: home)

    assert host_restore._payload_root() == local.resolve()


def test_zero_tunnel_connections_are_unhealthy():
    assert not host_restore._healthy_status(
        "host running\nwatchdog running\n"
        "tunnel example: 0 host connection(s)"
    )


def test_login_preflight_uses_dtssh_sibling_devtunnel(tmp_path, monkeypatch):
    local = tmp_path / "local"
    devtunnel = local / "dtssh" / "bin" / "devtunnel.exe"
    devtunnel.parent.mkdir(parents=True)
    devtunnel.write_text("", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    monkeypatch.setattr(host_restore.shutil, "which", lambda _name: None)
    captured = {}

    def run(command, **kwargs):
        captured["command"] = command
        return SimpleNamespace(
            returncode=0,
            stdout='{"status": "Logged in"}',
            stderr="",
        )

    monkeypatch.setattr(host_restore.subprocess, "run", run)

    logged_in, error = host_restore._login_status()

    assert logged_in is True
    assert error == ""
    assert captured["command"][0] == str(devtunnel)


def test_dtssh_launcher_uses_stable_install_working_directory():
    script = INSTALL_HOST.read_text(encoding="utf-8")

    assert "-WorkingDirectory $InstallDir" in script


@pytest.mark.guard
def test_dtssh_host_identity_is_synced_before_stop_and_after_start():
    script = INSTALL_HOST.read_text(encoding="utf-8")
    switch = script[script.index("switch ($Action)") :]
    update = switch[switch.index("{ $_ -in @('install', 'update') }") :]

    first_sync = update.index("Sync-DtsshHostIdentity")
    stop_launcher = update.index("Stop-Launcher")
    start_launcher = update.index("Start-HostLauncher")
    wait_identity = update.index("Wait-DtsshHostIdentity")

    assert "OneDriveCommercial" in script
    assert "AGENT_SSH_DTSSH_HOST_KEY_BACKUP_ROOT" in script
    assert first_sync < stop_launcher < start_launcher < wait_identity
    assert "Assert-MatchingHostIdentity" in script
    assert "Protect-PrivateKey" in script
    assert "'start'" in script
    assert '"`"$InstallerDst`""' in script


@pytest.mark.skipif(
    os.name != "nt" or PWSH is None or SSH_KEYGEN is None,
    reason="Windows PowerShell and ssh-keygen are required",
)
@pytest.mark.guard
def test_dtssh_host_identity_round_trips_through_onedrive(tmp_path):
    local_app_data = tmp_path / "local"
    host_dir = local_app_data / "dtssh" / "host"
    host_dir.mkdir(parents=True)
    private_key = host_dir / "ssh_host_ed25519_key"
    subprocess.run(
        [
            SSH_KEYGEN,
            "-q",
            "-t",
            "ed25519",
            "-N",
            "",
            "-f",
            str(private_key),
        ],
        check=True,
        timeout=30,
    )
    expected_hash = private_key.read_bytes()

    text = INSTALL_HOST.read_text(encoding="utf-8")
    functions = text[
        text.index("function Resolve-DurableHostIdentityRoot") :
        text.index("function Add-UserPath")
    ]
    script = (
        "$ErrorActionPreference='Stop';"
        "$InstallDir=Join-Path $env:LOCALAPPDATA 'agent-ssh-dtssh';"
        "$OpenSSHDir=Join-Path $env:LOCALAPPDATA 'OpenSSH-Win64';"
        "$ValidationOpenSSHDir=Join-Path $InstallDir 'validation-OpenSSH';"
        "$HostStateDir=Join-Path $env:LOCALAPPDATA 'dtssh\\host';"
        "$HostKeyBackupRoot=$null;"
        "$Alias='example-host';"
        + functions
        + "\nSync-DtsshHostIdentity;"
        + "$backup=Get-HostIdentityDirectory;"
        + "$backupCount=@(Get-ChildItem -LiteralPath $backup -File).Count;"
        + "Remove-Item -LiteralPath "
        + "(Join-Path $HostStateDir 'ssh_host_ed25519_key') -Force;"
        + "Remove-Item -LiteralPath "
        + "(Join-Path $HostStateDir 'ssh_host_ed25519_key.pub') -Force;"
        + "Sync-DtsshHostIdentity;"
        + "[pscustomobject]@{Backup=$backup;BackupCount=$backupCount;"
        + "Restored=(Test-Path -LiteralPath "
        + "(Join-Path $HostStateDir 'ssh_host_ed25519_key'))}"
        + "|ConvertTo-Json -Compress"
    )
    env = os.environ.copy()
    env["LOCALAPPDATA"] = str(local_app_data)
    env["OneDriveCommercial"] = str(tmp_path / "OneDrive - Example")
    os.makedirs(env["OneDriveCommercial"])
    env.pop("AGENT_SSH_DTSSH_HOST_KEY_BACKUP_ROOT", None)

    result = subprocess.run(
        [PWSH, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.splitlines()[-1])
    assert payload["BackupCount"] == 2
    assert payload["Restored"] is True
    assert ".agent-ssh" in payload["Backup"]
    assert private_key.read_bytes() == expected_hash
