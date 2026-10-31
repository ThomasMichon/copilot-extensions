"""Opt-in actual default-service command adoption on disposable hosted runners."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs
from update_driver import foundation_status, status

from test_first_use_vault import (
    cold_environment, materialized_vault as materialized_vault,
    native_genesis, stamp_vault, vault_command,
)


def uninstall_child_diagnostic(result, *, shell, env, project):
    match = re.search(
        r"Vault still owns a live child \(pid=([1-9][0-9]*)\); refusing uninstall",
        result.stdout + result.stderr,
    )
    if os.name != "nt" or result.returncode == 0 or match is None:
        return ""
    script = (
        "$ErrorActionPreference='Stop'; "
        "function Describe($row) { if ($null -eq $row) { return $null }; "
        "$row | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,"
        "@{Name='CreationDateUtc';Expression={$_.CreationDate.ToUniversalTime().ToString('o')}} }; "
        f"$child=Get-CimInstance Win32_Process -Filter 'ProcessId={int(match[1])}'; "
        "$parent=$null; if ($child -and $child.ParentProcessId -gt 0) { "
        "$parent=Get-CimInstance Win32_Process -Filter "
        "('ProcessId='+[string]$child.ParentProcessId) }; "
        "[ordered]@{observedAtUtc=[DateTime]::UtcNow.ToString('o');"
        "child=(Describe $child);parent=(Describe $parent)} | ConvertTo-Json -Depth 3 -Compress"
    )
    try:
        observed = subprocess.run(
            [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
            env=env, cwd=project, stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=15, check=False, **no_window_kwargs(),
        )
        detail = f"probe exit={observed.returncode}\n{observed.stdout}{observed.stderr}"
    except (OSError, subprocess.SubprocessError) as error:
        detail = f"probe unavailable: {type(error).__name__}: {error}"
    private_root = str(project.parent)
    for root in dict.fromkeys((json.dumps(private_root)[1:-1], private_root)):
        detail = detail.replace(root, "<fixture-root>")
    if len(detail) > 16000:
        detail = detail[:16000] + "\n[diagnostic truncated at 16000 characters]"
    return "\nUninstall child attribution (post-refusal snapshot, not ownership proof):\n" + detail


@pytest.mark.skipif(os.name != "nt", reason="Windows CIM failure diagnostics")
@pytest.mark.parametrize(("code", "error"), [
    (0, "Vault still owns a live child (pid=4216); refusing uninstall"),
    (1, "Runtime uninstall requires an existing external Python"),
])
def test_uninstall_child_diagnostic_does_not_probe_other_results(monkeypatch, code, error):
    def forbidden(*args, **kwargs):
        pytest.fail("unrelated cleanup result must not query processes")

    monkeypatch.setattr(subprocess, "run", forbidden)
    assert uninstall_child_diagnostic(
        subprocess.CompletedProcess([], code, "", error),
        shell="unused", env={}, project=Path("fixture") / "project",
    ) == ""


@pytest.mark.skipif(os.name != "nt", reason="Windows CIM failure diagnostics")
@pytest.mark.parametrize("unavailable", [False, True])
def test_uninstall_child_diagnostic_is_bounded_and_preserves_refusal(monkeypatch, unavailable):
    project = Path("fixture") / "project"
    result = subprocess.CompletedProcess(
        [], 1, "", "Vault still owns a live child (pid=4216); refusing uninstall",
    )
    calls = []

    def query(command, **kwargs):
        calls.append(command)
        assert kwargs["timeout"] == 15 and kwargs["check"] is False
        assert "Win32_Process -Filter 'ProcessId=4216'" in command[-1]
        assert "('ProcessId='+[string]$child.ParentProcessId)" in command[-1]
        if unavailable:
            raise subprocess.TimeoutExpired(command, 15)
        return subprocess.CompletedProcess(
            command, 0, json.dumps({"ExecutablePath": str(project.parent / "python.exe")}), "",
        )

    monkeypatch.setattr(subprocess, "run", query)
    detail = uninstall_child_diagnostic(result, shell="powershell", env={}, project=project)
    assert len(calls) == 1 and result.returncode == 1
    assert "post-refusal snapshot, not ownership proof" in detail
    assert ("TimeoutExpired" if unavailable else "<fixture-root>") in detail
    assert "fixture" not in detail.replace("<fixture-root>", "")


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
    installer = payload / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
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
        # Cold construction is complete; uninstall needs its existing external interpreter.
        cleanup_env["PATH"] = str(Path(sys._base_executable).parent) + os.pathsep + cleanup_env["PATH"]
        removed = subprocess.run(
            cleanup, env=cleanup_env, cwd=project, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=90, **no_window_kwargs(),
        )
        assert removed.returncode == 0, removed.stdout + removed.stderr + uninstall_child_diagnostic(
            removed, shell=cleanup[0], env=cleanup_env, project=project,
        )


@pytest.mark.skipif(os.name != "nt", reason="Windows snapshot cleanup command")
@pytest.mark.parametrize("entry", ["payload", "primary"])
def test_command_fixture_cleanup_keeps_external_installer_and_exact_target(tmp_path, monkeypatch, entry):
    payload = tmp_path / "market" / "plugins" / "agent-vault"
    original = tmp_path / "profile" / ".agent-vault"
    project = tmp_path / "project"
    target = original.parent / "command-vault"
    snapshot = target / "snapshots" / "1.0"
    command = lambda root: ["powershell", "-File", str(root / "command.ps1"), "--help"]
    monkeypatch.setattr(sys.modules[__name__], "stamp_vault", lambda *_args: (snapshot, command(snapshot)))
    monkeypatch.setattr(sys.modules[__name__], "vault_command", command)
    monkeypatch.setattr(sys.modules[__name__], "cold_environment", lambda *_args: {"PATH": "fixture-path"})
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        if len(calls) == 1:
            raise RuntimeError("controlled foreground failure")
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(RuntimeError, match="controlled foreground failure"):
        test_actual_cold_command_keeps_default_service_recipe_and_healthy_dispatch(
            (payload, original, project), tmp_path, None, monkeypatch, entry,
        )
    assert len(calls) == 2
    selected = snapshot if entry == "primary" else payload
    assert str(selected / "command.ps1") in calls[0][-1]
    assert Path(calls[1][calls[1].index("-File") + 1]) == payload / "scripts" / "install.ps1"
    assert Path(calls[1][calls[1].index("-InstallDir") + 1]) == target
