"""The common bootstrap family must admit publication before dispatch."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import time

import pytest

from test_adopter_publication import ROOT, SHELLS, _adopter, _command
from test_command_binding import (
    _snapshot,
    _write,
    binding_fixture as binding_fixture,
    powershell_host as powershell_host,
)
from test_installer_publication import _policy
from test_native_publication import _run

PLUGINS = ("agent-codespaces", "agent-logger", "agent-pull-requests", "agent-vault")
pytestmark = [pytest.mark.portfolio_tier("T3"), pytest.mark.timeout(60)]


def _bootstrap_fixture(fixture: dict, shell: str, plugin: str) -> dict:
    environment = _adopter(fixture, plugin)
    environment["BOOTSTRAP_WITNESS"] = str(fixture["legacy"] / "installer-dispatched")
    scripts = fixture["payload"] / "scripts"
    for name in ("init", "install"):
        if shell == "powershell":
            content = (
                "param([ValidateSet('stamp', 'install')][string]$Action)\n"
                "[IO.File]::WriteAllText($env:BOOTSTRAP_WITNESS, $Action)\n"
            )
            suffix = "ps1"
        else:
            content = (
                "#!/usr/bin/env bash\n"
                'case "${1:-}" in\n'
                'stamp) printf "%s\\n" "$1" > "$BOOTSTRAP_WITNESS";;\n'
                "*) exit 2;;\n"
                "esac\n"
            )
            suffix = "sh"
        (scripts / f"{name}.{suffix}").write_text(content, encoding="utf-8")
    return environment


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
def test_bootstrap_family_denies_stamp_before_dispatch(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])

    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )

    assert not (binding_fixture["legacy"] / "installer-dispatched").exists()
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "Runtime publication refused" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone"])
def test_bootstrap_family_still_stamps_standalone(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert (binding_fixture["legacy"] / "installer-dispatched").read_text().strip() == "stamp"


def _installed(fixture: dict, *, healthy: bool) -> None:
    (fixture["payload"] / "pyproject.toml").write_text('version = "2.0.0"\n', encoding="utf-8")
    _write(fixture["legacy"] / "deploy-manifest.json", {
        "source": {"version": "2.0.0" if healthy else "1.0.0"},
    })
    (fixture["legacy"] / ".venv").mkdir()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid"])
def test_bootstrap_family_healthy_runtime_remains_read_only(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    _installed(binding_fixture, healthy=True)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert not result.stderr
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["missing", "standalone", "managed"])
def test_bootstrap_family_helperless_compatibility(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    suffix = "ps1" if shell == "powershell" else "sh"
    helper = binding_fixture["payload"] / f"scripts/installation-context/plugin-publication-probe.{suffix}"
    helper.rename(helper.with_suffix(".unavailable"))
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == (0 if state == "missing" else 3), result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    witness = binding_fixture["legacy"] / "installer-dispatched"
    if state == "missing":
        assert witness.read_text().strip() == "stamp"
    else:
        assert "Runtime publication refused" in result.stderr
        assert _snapshot(binding_fixture["home"]) == before


def _recording_processes(fixture: dict, shell: str, environment: dict) -> list[str]:
    witness = fixture["legacy"] / "process-dispatched"
    environment["BOOTSTRAP_PROCESS_WITNESS"] = str(witness)
    scripts = fixture["payload"] / "scripts"
    if shell == "powershell":
        body = (
            "function Get-Process { param($Id) [pscustomobject]@{ Id = $Id } }\n"
            "function Stop-Process { param($Id, [switch]$Force) "
            "[IO.File]::WriteAllText($env:BOOTSTRAP_PROCESS_WITNESS, 'kill') }\n"
            "function Start-Process { param($FilePath, $WorkingDirectory, [switch]$PassThru, "
            "$WindowStyle, $ArgumentList) "
            "[IO.File]::WriteAllText($env:BOOTSTRAP_PROCESS_WITNESS, 'spawn'); "
            "[pscustomobject]@{ Id = 424242 } }\n"
            "& (Join-Path $PSScriptRoot 'bootstrap-check.ps1')\nexit $LASTEXITCODE\n"
        )
        suffix = "ps1"
    else:
        body = (
            "#!/usr/bin/env bash\n"
            'kill() { printf "%s" kill > "$BOOTSTRAP_PROCESS_WITNESS"; return 0; }\n'
            'nohup() { printf "%s" spawn > "$BOOTSTRAP_PROCESS_WITNESS"; }\n'
            'source "$(dirname "${BASH_SOURCE[0]}")/bootstrap-check.sh"\n'
        )
        suffix = "sh"
    (scripts / f"bootstrap-recording.{suffix}").write_text(body, encoding="utf-8")
    return _command(fixture, shell, "bootstrap-recording")


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("state", ["managed", "invalid", "foreign", "future"])
def test_bootstrap_family_denies_drift_before_reap_lock_or_spawn(
    binding_fixture: dict, plugin: str, shell: str, state: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    _installed(binding_fixture, healthy=False)
    command = _recording_processes(binding_fixture, shell, environment)
    lock = binding_fixture["legacy"] / "reconcile.lock"
    lock.write_text("424242", encoding="utf-8")
    os.utime(lock, (1_000_000_000, 1_000_000_000))
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], state)
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(binding_fixture, environment, command, host)
    assert not Path(environment["BOOTSTRAP_PROCESS_WITNESS"]).exists()
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "Runtime publication refused" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("shell", SHELLS)
@pytest.mark.parametrize("failure", ["mode-invalid", "resolver-missing", "adapter-broken"])
def test_bootstrap_family_other_admission_failures_are_read_only(
    binding_fixture: dict, shell: str, failure: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, "agent-logger")
    suffix = "ps1" if shell == "powershell" else "sh"
    helpers = binding_fixture["payload"] / "scripts/installation-context"
    if failure == "mode-invalid":
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "invalid", "version": 1,
            "installationMode": {"enabled": True, "marketplaces": {}},
        })
    elif failure == "resolver-missing":
        helper = helpers / f"installation-context.{suffix}"
        helper.rename(helper.with_suffix(".unavailable"))
    else:
        (helpers / f"plugin-publication-probe.{suffix}").write_text(
            "throw 'broken admission fixture'\n" if shell == "powershell" else "exit 7\n",
            encoding="utf-8",
        )
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    before = _snapshot(binding_fixture["home"])
    result = _run(
        binding_fixture, environment, _command(binding_fixture, shell, "bootstrap-check"), host,
    )
    assert result.returncode == 3, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    assert "Runtime publication refused" in result.stderr
    assert _snapshot(binding_fixture["home"]) == before
    if host:
        host.close()
        assert _snapshot(binding_fixture["home"]) == before


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
def test_bootstrap_family_standalone_drift_still_dispatches(
    binding_fixture: dict, plugin: str, shell: str, powershell_host,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    _installed(binding_fixture, healthy=False)
    command = _recording_processes(binding_fixture, shell, environment)
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], "standalone")
    host = powershell_host(binding_fixture, environment) if shell == "powershell" else None
    result = _run(binding_fixture, environment, command, host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {}
    witness = Path(environment["BOOTSTRAP_PROCESS_WITNESS"])
    deadline = time.monotonic() + 2
    while not witness.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert witness.read_text() == "spawn"
    assert (binding_fixture["legacy"] / "reconcile.lock").read_text().strip().isdigit()


@pytest.mark.parametrize("plugin", PLUGINS)
@pytest.mark.parametrize("shell", SHELLS)
def test_bootstrap_refusal_does_not_gate_independent_hook_commands(
    binding_fixture: dict, plugin: str, shell: str,
) -> None:
    environment = _bootstrap_fixture(binding_fixture, shell, plugin)
    environment["COPILOT_PLUGIN_ROOT"] = str(binding_fixture["payload"])
    shutil.copyfile(
        ROOT / "plugins" / plugin / "payload-invocation.json",
        binding_fixture["payload"] / "payload-invocation.json",
    )
    _policy(binding_fixture, binding_fixture["legacy"], binding_fixture["arguments"], "managed")
    hooks = json.loads((ROOT / "plugins" / plugin / "hooks.json").read_text())["hooks"]["sessionStart"]
    suffix = "ps1" if shell == "powershell" else "sh"
    scripts = binding_fixture["payload"] / "scripts"
    for name in ("write-session-guidance", "register-cold-store-provider", "register-bridge-provider"):
        target = scripts / f"{name}.{suffix}"
        if target.exists():
            target.write_text(
                f"[IO.File]::WriteAllText((Join-Path $env:USERPROFILE '{name}'), 'ran')\n"
                "[Console]::Out.Write('{}')\n"
                if shell == "powershell" else
                f'printf ran > "$HOME/{name}"\nprintf "{{}}"\n',
                encoding="utf-8",
            )
    for index, hook in enumerate(hooks):
        (scripts / f"hook-{index}.{suffix}").write_text(hook[shell], encoding="utf-8")
    for index in range(len(hooks)):
        result = _run(
            binding_fixture, environment, _command(binding_fixture, shell, f"hook-{index}"),
        )
        # The existing PowerShell hook expression does not forward the nested
        # script's exit status; the bootstrap diagnostic and no-write gate do.
        expected = 3 if index == 0 and shell == "bash" else 0
        assert result.returncode == expected, result.stdout + result.stderr
        if index == 0:
            assert "Runtime publication refused" in result.stderr
        assert json.loads(result.stdout) == {}
    assert not (binding_fixture["legacy"] / "installer-dispatched").exists()
    assert (binding_fixture["home"] / "write-session-guidance").read_text() == "ran"
    if plugin in {"agent-codespaces", "agent-logger"}:
        name = "register-bridge-provider" if plugin == "agent-codespaces" else "register-cold-store-provider"
        assert (binding_fixture["home"] / name).read_text() == "ran"
