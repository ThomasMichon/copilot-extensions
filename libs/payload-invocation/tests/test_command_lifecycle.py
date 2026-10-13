"""Ownership admission precedes native runtime writes and activation."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from test_command_binding import (
    PLUGIN_ID, POWERSHELL, ROOT, _catalog_environment, _load, _prepare_catalog,
    _resolve, _snapshot, _write, binding_fixture as binding_fixture,
)


def _lifecycle(fixture: dict):
    return _load(ROOT / "libs/payload-invocation/command_lifecycle.py", "lifecycle_reference")


def test_command_binding_pending_failure_keeps_owner_and_never_selects(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    lifecycle = _lifecycle(binding_fixture)
    with pytest.raises(RuntimeError, match="fixture failure"):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            (binding_fixture["legacy"] / "runtime-slot").write_text("partial", encoding="utf-8")
            assert _resolve(binding_fixture)["reason"] == "legacy-lifecycle-incomplete"
            raise RuntimeError("fixture failure")
    assert not (binding_fixture["legacy"] / ".command-binding-publication.lock").exists()
    assert _resolve(binding_fixture)["reason"] == "legacy-lifecycle-incomplete"
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    })
    before = _snapshot(binding_fixture["legacy"])
    with pytest.raises(binding.CommandOwnershipConflict, match="reservation"):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            pytest.fail("Foreign installer reached runtime mutation")
    after = _snapshot(binding_fixture["legacy"])
    assert after.keys() == before.keys()
    assert all(after[path] == value for path, value in before.items() if path != ".")


def test_command_binding_same_owner_retries_pending_and_completes(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    # Use the binding's loader so publication and admission share their context.
    lifecycle = binding._load_lifecycle()
    for _ in range(2):
        with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]):
            assert _resolve(binding_fixture)["status"] == "blocked"
            binding.write_legacy_manifest(
                binding_fixture["primitive"], {"schema_version": 3, "service": PLUGIN_ID},
                **binding_fixture["arguments"],
            )
        assert _resolve(binding_fixture)["status"] == "selected"


def test_command_binding_ticket_presence_is_not_admission(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = binding_fixture["binding"]
    monkeypatch.setenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", "{}")
    before = _snapshot(binding_fixture["home"])
    with pytest.raises(binding.CommandOwnershipConflict, match="ticket"):
        binding._load_lifecycle().borrowed_lease(binding, binding_fixture["primitive"], **binding_fixture["arguments"])
    assert _snapshot(binding_fixture["home"]) == before


def test_command_binding_unattributed_default_preserves_legacy_without_selection(
    binding_fixture: dict, capsys: pytest.CaptureFixture,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]):
        result = binding.write_legacy_manifest(
            primitive, {"schema_version": 3, "service": PLUGIN_ID},
            **binding_fixture["arguments"],
        )
        assert result["status"] == "unresolved"
    manifest = json.loads((binding_fixture["legacy"] / "deploy-manifest.json").read_text())
    assert "commandBinding" not in manifest
    assert manifest["commandLifecycle"]["owner"]["unattributed"] is True
    assert _resolve(binding_fixture)["status"] == "unresolved"
    assert "command ownership remains unresolved" in capsys.readouterr().err


def test_command_binding_unattributed_explicit_policy_never_admits_mutation(binding_fixture: dict) -> None:
    binding = binding_fixture["binding"]
    _write(binding_fixture["home"] / ".copilot/settings.json", {})
    _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    })
    with pytest.raises(binding.CommandOwnershipConflict, match="mutation refused"):
        with binding._load_lifecycle().legacy_lifecycle(
            binding, binding_fixture["primitive"], **binding_fixture["arguments"],
        ):
            pytest.fail("A policy-enabled unattributed install reached runtime mutation")
    assert not (binding_fixture["legacy"] / "deploy-manifest.json").exists()


@pytest.mark.parametrize("field,value", [
    ("token", "0" * 32), ("pid", 0), ("host", "another-host"),
    ("root", "/another-runtime"), ("owner", {}), ("version", 1),
])
def test_command_binding_inherited_ticket_is_bound_to_its_owner(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    binding = binding_fixture["binding"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, binding_fixture["primitive"], **binding_fixture["arguments"]) as lease:
        ticket = {**lease.ticket, field: value}
        token = lifecycle._CURRENT.set(None)
        try:
            monkeypatch.setenv(lifecycle.TICKET_ENV, json.dumps(ticket))
            with pytest.raises((binding.CommandOwnershipConflict, binding_fixture["primitive"].InstallationContextError)):
                lifecycle.borrowed_lease(binding, binding_fixture["primitive"], **binding_fixture["arguments"])
        finally:
            lifecycle._CURRENT.reset(token)


def test_command_binding_inherited_ticket_requires_live_parent(
    binding_fixture: dict, monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = binding_fixture["binding"]
    primitive = binding_fixture["primitive"]
    lifecycle = binding._load_lifecycle()
    with lifecycle.legacy_lifecycle(binding, primitive, **binding_fixture["arguments"]) as lease:
        token = lifecycle._CURRENT.set(None)
        try:
            with monkeypatch.context() as patch:
                patch.setenv(lifecycle.TICKET_ENV, json.dumps(lease.ticket))
                patch.setattr(primitive, "_pid_is_live", lambda pid: False)
                with pytest.raises(binding.CommandOwnershipConflict, match="admission changed"):
                    lifecycle.borrowed_lease(binding, primitive, **binding_fixture["arguments"])
        finally:
            lifecycle._CURRENT.reset(token)


def _native_fixture(fixture: dict, shell: str, action: str = "provision") -> list[str]:
    payload = fixture["payload"]
    _prepare_catalog(fixture)
    if shell == "bash":
        source = (ROOT / "plugins/agent-worktrees/scripts/install.sh").read_text()
        writer = "write_deploy_manifest() {" + source.split(
            "write_deploy_manifest() {", 1
        )[1].split("show_deploy_status() {", 1)[0]
        admission = "if ! $CONTEXTUAL_INSTALL; then\n    case \"$ACTION\" in" + source.split(
            "if ! $CONTEXTUAL_INSTALL; then\n    case \"$ACTION\" in", 1
        )[1].split('case "$ACTION" in\n    stamp)', 1)[0]
        dispatch = "case \"$ACTION\" in\n    stamp)" + source.split(
            'case "$ACTION" in\n    stamp)', 1
        )[1].split("    install)\n", 1)[0] + "esac\n"
        program = (
            "#!/usr/bin/env bash\nset -euo pipefail\n"
            "__aw_args=(\"$@\")\nACTION=\"${1:-provision}\"\nCONTEXTUAL_INSTALL=false\n"
            "SCRIPT_DIR=\"$(cd \"$(dirname \"${BASH_SOURCE[0]}\")\" && pwd)\"\n"
            "PLUGIN_DIR=\"$(dirname \"$SCRIPT_DIR\")\"\n"
            "INSTALL_DIR=\"$TEST_RUNTIME\"\nBIN_DIR=\"$INSTALL_DIR/bin\"\n"
            "LOCAL_BIN=\"$INSTALL_DIR/local-bin\"\nLINK_DIR=\"$INSTALL_DIR/.venv\"\n"
            "VENV_DIR=\"$INSTALL_DIR/versions/fixture\"\nVENV_PYTHON=\"$TEST_PYTHON\"\n"
            "SRC_VERSION=fixture\nSERVICE_NAME=fixture\n"
            "_bootstrap_python() { printf '%s\\n' \"$TEST_PYTHON\"; }\n"
            "resolve_executable_command_path() { type -P \"$1\"; }\n"
            "resolve_machine() { printf 'example-host'; }\n"
            "detect_platform() { printf 'linux'; }\n_source_kind() { printf 'marketplace'; }\n"
            "header() { :; }\nok() { :; }\nerr() { printf '%s\\n' \"$*\" >&2; }\n"
            "_ensure_uv() { :; }\n_ensure_uv_index() { :; }\n"
            "deploy_runtime_resolvers() { :; }\ndeploy_tool_binstub() { :; }\n"
            "_deploy_venv_and_package() {\n"
            "  printf '%s' \"$TEST_OWNER\" > \"$INSTALL_DIR/runtime-slot\"\n"
            "  sleep 0.3\n}\n"
            "_versioned_activate() { cp \"$INSTALL_DIR/runtime-slot\" \"$INSTALL_DIR/current-version\"; }\n"
            + writer + "\n" + admission + "\n" + dispatch
        )
        script = payload / "scripts/install.sh"
        script.write_text(program, encoding="utf-8")
        return [str(Path(subprocess.check_output(["which", "bash"], text=True).strip())), str(script), action]
    source = (ROOT / "plugins/agent-worktrees/scripts/install.ps1").read_text()
    writer = "function Write-V3Manifest {" + source.split(
        "function Write-V3Manifest {", 1
    )[1].split("function Test-UvConfiguredIndex {", 1)[0]
    admission = "if (-not $ContextualInstall -and $Action -in" + source.split(
        "if (-not $ContextualInstall -and $Action -in", 1
    )[1].split("switch ($Action) {", 1)[0]
    dispatch = "switch ($Action) {\n    'provision' {" + source.split(
        "    'provision' {", 1
    )[1].split("    'install' {", 1)[0] + "}\n"
    stamp = "function Invoke-Stamp {" + source.split(
        "function Invoke-Stamp {", 1
    )[1].split("if (-not $ContextualInstall -and $Action -in", 1)[0]
    dispatch = dispatch.replace("switch ($Action) {", "switch ($Action) {\n'stamp' { Invoke-Stamp }")
    program = (
        "param([string]$Action='provision')\n$ErrorActionPreference='Stop'\n"
        "$ContextualInstall=$false\n$PluginDir=Split-Path -Parent $PSScriptRoot\n"
        "$InstallDir=$env:TEST_RUNTIME\n$BinDir=Join-Path $InstallDir 'bin'\n"
        "$LocalBin=Join-Path $InstallDir 'local-bin'\n"
        "$LinkDir=Join-Path $InstallDir '.venv'\n$VenvPython=$env:TEST_PYTHON\n"
        "$SrcVersion='fixture'\n$ServiceName='fixture'\n"
        "function Get-BootstrapPython { param([switch]$ExcludeVenvDir); $env:TEST_PYTHON }\n"
        "function Get-SourceKind { param($PluginPath); 'marketplace' }\n"
        "function Write-ServiceHeader {}\nfunction Write-ServiceOk {}\n"
        "function Write-ServiceSkipped {}\nfunction Write-ServiceChanged {}\n"
        "function Ensure-Uv { $true }\nfunction Ensure-UvIndex {}\n"
        "function Deploy-RuntimeResolvers { $true }\nfunction Deploy-GlobalBinstub {}\n"
        "function Ensure-InstallDir { param($Dir); [void][IO.Directory]::CreateDirectory($Dir) }\n"
        "function Invoke-NativeCapture { param($Action); $text=& $Action;"
        "@{ExitCode=$LASTEXITCODE;Output=($text -join \"`n\")} }\n"
        "function Deploy-VenvAndPackage {\n"
        "  [IO.File]::WriteAllText((Join-Path $InstallDir 'runtime-slot'),$env:TEST_OWNER)\n"
        "  Start-Sleep -Milliseconds 300\n}\n"
        "function Invoke-VersionedActivate {\n"
        "  Copy-Item (Join-Path $InstallDir 'runtime-slot') (Join-Path $InstallDir 'current-version')\n"
        "  $true\n}\n"
        + writer + "\n" + stamp + "\n" + admission + "\n" + dispatch
    )
    script = payload / "scripts/install.ps1"
    script.write_text(program, encoding="utf-8")
    return [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(script), "-Action", action]


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_native_stamp_defers_without_stuck_reservation(
    binding_fixture: dict, shell: str,
) -> None:
    command = _native_fixture(binding_fixture, shell, "stamp")
    environment = {
        **_catalog_environment(binding_fixture),
        "TEST_RUNTIME": str(binding_fixture["legacy"]), "TEST_OWNER": "example",
        "TEST_PYTHON": sys.executable if os.name == "nt" else
        str(binding_fixture["home"] / "test-bin/python3"),
        "COMPUTERNAME": "example-host",
    }
    stamped = subprocess.run(
        command, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    runtime = binding_fixture["legacy"]
    assert not (runtime / "deploy-manifest.json").exists()
    assert not (runtime / "current-version").exists()
    assert _resolve(binding_fixture)["status"] == "unresolved"
    deferred = Path((runtime / "payload-dir").read_text().strip())
    if shell == "bash":
        provision = [command[0], str(deferred / "scripts/install.sh"), "provision"]
    else:
        provision = [
            command[0], "-NoLogo", "-NoProfile", "-File",
            str(deferred / "scripts/install.ps1"), "-Action", "provision",
        ]
    installed = subprocess.run(
        provision, env=environment, cwd=binding_fixture["home"],
        capture_output=True, text=True, timeout=45,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert (runtime / "current-version").read_text() == "example"
    manifest = json.loads((runtime / "deploy-manifest.json").read_text())
    assert manifest["commandLifecycle"]["state"] == "complete"
    assert not (runtime / ".command-binding-publication.lock").exists()


@pytest.mark.parametrize("shell", [
    pytest.param("bash", marks=pytest.mark.skipif(os.name == "nt", reason="POSIX installer")),
    pytest.param("powershell", marks=pytest.mark.skipif(POWERSHELL is None, reason="PowerShell unavailable")),
])
def test_command_binding_native_lifecycle_admits_one_runtime_owner(
    binding_fixture: dict, shell: str,
) -> None:
    other = binding_fixture["home"] / ".copilot/installed-plugins/other" / PLUGIN_ID
    other.mkdir(parents=True)
    _write(binding_fixture["home"] / ".copilot/settings.json", {
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
            "other": {"source": {"source": "github", "repo": "Other/Plugins"}},
        },
    })
    environment = _catalog_environment(binding_fixture)
    commands = [_native_fixture({**binding_fixture, "payload": payload}, shell)
                for payload in (binding_fixture["payload"], other)]
    processes = []
    for index, command in enumerate(commands):
        processes.append(subprocess.Popen(
            command, env={
                **environment, "TEST_OWNER": str(index), "COMPUTERNAME": "example-host",
                "TEST_RUNTIME": str(binding_fixture["legacy"]),
                "TEST_PYTHON": sys.executable if os.name == "nt" else
                str(binding_fixture["home"] / "test-bin/python3"),
                "COPILOT_PLUGIN_ROOT": str((binding_fixture["payload"], other)[index]),
            },
            cwd=binding_fixture["home"], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        ))
    outputs = [process.communicate(timeout=45) for process in processes]
    assert sum(process.returncode == 0 for process in processes) == 1, outputs
    winner = next(index for index, process in enumerate(processes) if process.returncode == 0)
    runtime = binding_fixture["legacy"]
    assert (runtime / "runtime-slot").read_text() == str(winner)
    assert (runtime / "current-version").read_text() == str(winner)
    assert json.loads((runtime / "deploy-manifest.json").read_text())["commandLifecycle"]["state"] == "complete"
    # A later foreign invocation must also refuse before any runtime file changes.
    before = _snapshot(runtime)
    refused = subprocess.run(
        processes[1 - winner].args,
        env={**environment, "TEST_RUNTIME": str(runtime), "TEST_OWNER": "foreign",
             "COMPUTERNAME": "example-host", "TEST_PYTHON": sys.executable if os.name == "nt" else
             str(binding_fixture["home"] / "test-bin/python3"),
             "COPILOT_PLUGIN_ROOT": str((binding_fixture["payload"], other)[1 - winner])},
        cwd=binding_fixture["home"], capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert refused.returncode != 0
    after = _snapshot(runtime)
    assert after.keys() == before.keys()
    assert all(after[path] == value for path, value in before.items() if path != ".")
