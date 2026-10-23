"""Opt-in actual default-service command adoption on disposable hosted runners."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs
from update_driver import foundation_status, status

from test_first_use_vault import (
    cold_environment, materialized_vault as materialized_vault,
    native_genesis, stamp_vault, vault_command,
)


@native_genesis
@pytest.mark.skipif(
    os.environ.get("GITHUB_ACTIONS") != "true",
    reason="actual default service integration is limited to disposable hosted qualification",
)
@pytest.mark.parametrize("entry", ["payload", "primary"])
def test_actual_cold_command_keeps_default_service_recipe_and_healthy_dispatch(
    materialized_vault, area, native_window_observation,
    monkeypatch, entry,
):
    payload, original_root, project = materialized_vault
    profile = original_root.parent
    installation = profile / "command-vault"
    monkeypatch.setenv("AGENT_VAULT_HOME", str(installation))
    if entry == "primary":
        payload, command = stamp_vault(payload, installation, project)
    else:
        command = vault_command(payload)
    env = cold_environment(installation, project, area)
    env["AGENT_VAULT_INSTALLATION_ID"] = "fixture-foreign-installation"
    if os.name != "nt":
        systemctl = shutil.which("systemctl")
        assert systemctl, "hosted qualification requires the real platform service manager client"
        (Path(env["PATH"]) / "systemctl").symlink_to(systemctl)
        env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    root = profile / ".copilot-extensions" / "update-driver"
    assert not root.exists()
    selected_payload = payload
    installer = selected_payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    if os.name == "nt":
        shell = str(Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe")
        quote = lambda path: "'" + str(path).replace("'", "''") + "'"
        script = command[command.index("-File") + 1]
        command = [
            shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
            f"Set-Location -LiteralPath {quote(project)}; "
            f"[IO.Directory]::SetCurrentDirectory({quote(profile)}); "
            f"& {quote(script)} --help; exit $LASTEXITCODE",
        ]
        cleanup = [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
                   str(installer), "-Action", "uninstall", "-InstallDir", str(installation)]
    else:
        link = area / "project-link"
        link.symlink_to(project, target_is_directory=True)
        command = [str(Path(env["PATH"]) / "bash"), "-c",
                   'cd -- "$1"; shift; exec "$@"', "bash", str(link), *command]
        cleanup = [str(Path(env["PATH"]) / "bash"), str(installer),
                   "uninstall", "--install-dir", str(installation)]
    try:
        result = subprocess.run(
            command, env=env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, encoding="utf-8", timeout=850,
            **no_window_kwargs(),
        )
        requests = status(root=root)
        diagnostic = result.stdout + result.stderr + "\n".join(
            path.read_text(errors="replace")[-16000:] for path in root.rglob("*.log")
        )
        assert result.returncode == 0 and "usage:" in result.stdout, diagnostic
        assert len(requests) == 1, diagnostic
        request = requests[0]
        assert request.state == "completed" and request.exit_code == 0, diagnostic
        assert request.spec.argv == (), "ordinary commands must not select client-only"
        assert "AGENT_VAULT_INSTALLATION_ID" not in dict(request.spec.context)
        admission = json.loads(request.spec.source)
        assert Path(admission["payload"]) == selected_payload
        assert Path(admission["runtime_root"]) == installation
        assert Path(admission["project"]) == project
        assert foundation_status(root=root).state == "completed"
        assert not (installation / ".provision.lock").exists()
        selector = installation / "current-version"
        slot = installation / "versions" / selector.read_text().strip()
        completion = (slot / ".install-complete.json").read_bytes()
        assert "service skipped" not in request.log_path.read_text(errors="replace")
        if os.name == "nt":
            assert (installation / "service.ps1").is_file(), diagnostic
            assert "fixture-foreign-installation" not in (installation / "service.ps1").read_text()
            suffix = hashlib.sha256(str(installation).lower().encode()).hexdigest()[:12]
            task = f"AgentVault-{suffix}"
            probe = subprocess.run(
                [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command",
                 f"$ErrorActionPreference='Stop'; $task=Get-ScheduledTask -TaskName '{task}'; "
                 f"if ($task.Actions.Arguments -notlike '*{installation}*') {{ exit 91 }}"],
                env=env, cwd=project, capture_output=True, text=True, timeout=30,
                **no_window_kwargs(),
            )
            assert probe.returncode == 0, probe.stdout + probe.stderr + diagnostic
        else:
            units = list((profile / ".config" / "systemd" / "user").glob("agent-vault-*.service"))
            assert len(units) == 1 and str(installation) in units[0].read_text(), diagnostic
            assert "fixture-foreign-installation" not in units[0].read_text()
        healthy = subprocess.run(
            command, env={**env, "AGENT_VAULT_NO_SELFPROVISION": "1"}, cwd=project,
            stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8",
            timeout=30, **no_window_kwargs(),
        )
        assert healthy.returncode == 0 and "usage:" in healthy.stdout, healthy.stdout + healthy.stderr
        assert len(status(root=root)) == 1
        assert (slot / ".install-complete.json").read_bytes() == completion
    finally:
        cleanup_env = dict(env)
        cleanup_env.pop("AGENT_VAULT_INSTALLATION_ID", None)
        removed = subprocess.run(
            cleanup, env=cleanup_env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90, **no_window_kwargs(),
        )
        assert removed.returncode == 0, removed.stdout + removed.stderr
