"""Payload-local invocation and explicit management-boundary tests."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_vault_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-vault",
        "module": "agent_vault",
        "legacyRuntimeRoot": ".agent-vault",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_VAULT_NO_SELFPROVISION",
        "purpose": "Fetch and manage machine-local vault credentials",
        "installer": "install",
        "windowsCatalogShim": "cmd",
        "provisionMode": "direct",
        "payloadRootEnv": "AGENT_VAULT_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }

    posix = (PLUGIN / "bin" / "agent-vault").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-vault.ps1").read_text(encoding="utf-8")
    assert "runtime-gate.sh" in posix
    assert "payload-dir" not in posix
    assert r"runtime-gate.ps1" in powershell
    assert "payload-dir" not in powershell


def test_runtime_gates_scope_namespaced_service_environment() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(encoding="utf-8")

    for text in (posix, powershell):
        assert "AGENT_VAULT_INSTALLATION_ID" in text
        assert "AGENT_VAULT_RUN_DIR" in text
        assert "AGENT_VAULT_CORE_RUN_DIR" in text
        assert "AGENT_VAULT_CACHE_DIR" in text
        assert "AGENT_VAULT_SOCKET" in text
        assert "AGENT_VAULT_PIPE" in text
        assert "AGENT_VAULT_PID" in text
        assert "AGENT_VAULT_LOG" in text
        assert "AGENT_VAULT_PORT" in text
        assert "AGENT_VAULT_SYSTEMD_UNIT" in text
        assert "AGENT_VAULT_TASK_NAME" in text


def test_installers_scope_service_identity_by_install_root() -> None:
    install_sh = (PLUGIN / "scripts" / "install.sh").read_text(encoding="utf-8")
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")

    assert "SERVICE_SUFFIX" in install_sh
    assert "agent-vault-${SERVICE_SUFFIX}.service" in install_sh
    assert "AGENT_VAULT_INSTALLATION_ID" in install_sh
    assert "AGENT_VAULT_PORT=0" in install_sh
    assert "TaskLauncher" in install_ps1
    assert "AgentVault-$serviceSuffix" in install_ps1
    assert "AGENT_VAULT_INSTALLATION_ID" in install_ps1
    assert "AGENT_VAULT_PORT = '0'" in install_ps1


def test_scheduled_task_launcher_resolves_slot_at_every_run() -> None:
    """#1836: the Windows Scheduled Task launcher must resolve the active
    versioned slot itself at process start (the same resolve-runtime.ps1 chain
    the binstub uses), never a concrete versions/<v> interpreter path baked in
    at registration time -- so an activated version cutover takes effect on
    the daemon's next restart without needing a task re-registration."""
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    idx = install_ps1.index("function Register-AgentVaultTask")
    body = install_ps1[idx : install_ps1.index("\nfunction ", idx + 1)]

    # The launcher body written to $TaskLauncher must dot-source the resolver
    # and resolve $_py fresh every time it runs, not at registration time.
    launcher_start = body.index("[System.IO.File]::WriteAllText($TaskLauncher")
    launcher_body = body[launcher_start : body.index('"@, $utf8NoBom)', launcher_start)]
    assert "resolve-runtime.ps1" in launcher_body
    assert "$_py = $null" in launcher_body.replace("`$", "$")
    assert "& `$_py -m agent_vault.service --foreground --persistent" in launcher_body

    # The registration-time code path must NOT resolve/bake a concrete slot
    # path itself (that responsibility moved into the launcher body above).
    pre_launcher = body[:launcher_start]
    assert "AgentRtPy" not in pre_launcher
    assert "taskPy" not in pre_launcher


def test_scheduled_task_registration_skips_reregister_when_already_correct() -> None:
    """#1836: re-registering the Scheduled Task (Set-ScheduledTask) is reserved
    for a genuine Action drift, not every install/update -- the launcher path
    and working directory are stable, so a match means already-correct."""
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    idx = install_ps1.index("function Register-AgentVaultTask")
    body = install_ps1[idx : install_ps1.index("\nfunction ", idx + 1)]
    assert "$matchesDesired = $existingAction -and" in body
    assert "if ($matchesDesired) {" in body
    matches_idx = body.index("if ($matchesDesired) {")
    match_block = body[matches_idx : body.index("} else {", matches_idx)]
    assert "Set-ScheduledTask" not in match_block
    assert "Register-ScheduledTask" not in match_block


def test_start_does_not_require_a_registered_scheduled_task() -> None:
    """#1836: `install.ps1 start` must converge on the same user-mode ensure
    path the CLI's own `agent-vault start` uses (ensure_service/start_service
    in cli.py) when no Scheduled Task is registered -- a client-only
    (-NoService) host, or one where the ScheduledTasks module is unavailable,
    must still be able to start the daemon directly rather than failing."""
    install_ps1 = (PLUGIN / "scripts" / "install.ps1").read_text(encoding="utf-8")
    idx = install_ps1.index("function Invoke-Start")
    body = install_ps1[idx : install_ps1.index("\nfunction ", idx + 1)]
    assert "No AgentVault task installed" not in body
    assert "& $LinkPython -m agent_vault start" in body


def test_session_catalog_producer_is_not_registered_as_a_hook() -> None:
    hooks = json.loads((PLUGIN / "hooks.json").read_text(encoding="utf-8"))
    session_hooks = hooks["hooks"]["sessionStart"]
    assert len(session_hooks) == 2
    assert "bootstrap-check" in session_hooks[0]["bash"]
    assert "bootstrap-check" in session_hooks[0]["powershell"]
    assert "write-session-guidance" in session_hooks[1]["bash"]
    assert "write-session-guidance" in session_hooks[1]["powershell"]
    assert "emit-command-catalog" not in str(session_hooks)

    powershell_catalog = (
        PLUGIN / "scripts" / "emit-command-catalog.ps1"
    ).read_text(encoding="utf-8")
    assert r"bin\agent-vault.cmd" in powershell_catalog
    assert "shell = 'cmd'" in powershell_catalog


def test_out_of_session_boundaries_remain_explicit() -> None:
    skill = (PLUGIN / "skills" / "agent-vault" / "SKILL.md").read_text(
        encoding="utf-8"
    )
    assert (
        "'!agent-vault git-credential' "
        "# marketplace-isolation: allow git-credential-management"
    ) in skill
    setup = (
        PLUGIN / "skills" / "agent-vault-setup" / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert "scripts\\install.ps1 -Action start" in setup
    assert "scripts/install.sh start" in setup


@pytest.mark.skipif(os.name == "nt", reason="POSIX payload command test")
def test_posix_payload_command_ignores_shadow_path_and_preserves_stdin(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    runtime = home / ".agent-vault"
    python = runtime / "versions" / "test" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_text(
        '#!/bin/sh\nprintf "%s|" "$*"\ncat\n',
        encoding="utf-8",
    )
    python.chmod(0o755)
    (python.parents[1] / ".install-complete.json").write_text(
        (
            '{"version": "test", '
            '"completed_at": "2026-08-27T00:00:00Z", "pid": 1}'
        ),
        encoding="utf-8",
    )
    (runtime / "current-version").write_text("test\n", encoding="utf-8")

    shadow_bin = tmp_path / "shadow-bin"
    shadow_bin.mkdir()
    shadow_marker = tmp_path / "shadow-called"
    shadow = shadow_bin / "agent-vault"
    shadow.write_text(
        f'#!/bin/sh\nprintf called > "{shadow_marker}"\nexit 99\n',
        encoding="utf-8",
    )
    shadow.chmod(0o755)

    env = os.environ.copy()
    env.update(
        {
            "HOME": str(home),
            "COPILOT_PLUGIN_ROOT": str(PLUGIN),
            "PATH": f"{shadow_bin}{os.pathsep}{env['PATH']}",
        }
    )
    result = subprocess.run(
        [str(PLUGIN / "bin" / "agent-vault"), "seal", "example"],
        input="secret",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "-m agent_vault seal example|secret"
    assert not shadow_marker.exists()


@pytest.mark.skipif(os.name != "nt", reason="native Windows CMD test")
def test_windows_catalog_cmd_preserves_native_stdin(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    cmd = bin_dir / "agent-vault.cmd"
    shutil.copy2(PLUGIN / "bin" / "agent-vault.cmd", cmd)
    (bin_dir / "agent-vault.ps1").write_text(
        "$body = [Console]::In.ReadToEnd()\n"
        "[Console]::Out.Write(($args -join '|') + '::' + $body)\n",
        encoding="utf-8",
    )
    comspec = os.environ.get("COMSPEC", "cmd.exe")
    env = {
        **os.environ,
        "PATH": str(Path(comspec).parent),
    }
    result = subprocess.run(
        [comspec, "/d", "/s", "/c", str(cmd), "probe", "two words"],
        input="secret",
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout == "probe|two words::secret"
