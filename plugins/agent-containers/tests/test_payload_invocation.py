"""Payload-local invocation and installation-context gates for agent-containers."""

from __future__ import annotations

import json
from pathlib import Path

PLUGIN = Path(__file__).resolve().parents[1]


def test_payload_manifest_describes_agent_containers_runtime() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )
    assert manifest == {
        "schema": "copilot-extensions.payload-invocation",
        "version": 2,
        "command": "agent-containers",
        "module": "agent_containers",
        "legacyRuntimeRoot": ".agent-containers",
        "installationContext": "required",
        "noSelfProvisionEnv": "AGENT_CONTAINERS_NO_SELFPROVISION",
        "purpose": "Manage local container fleets leases and dispatch",
        "installer": "init",
        "payloadRootEnv": "AGENT_CONTAINERS_PAYLOAD_ROOT",
        "payloadDispatcher": {
            "posix": "scripts/runtime-gate.sh",
            "windows": "scripts/runtime-gate.ps1",
        },
    }
    posix = (PLUGIN / "bin" / "agent-containers").read_text(encoding="utf-8")
    powershell = (PLUGIN / "bin" / "agent-containers.ps1").read_text(encoding="utf-8")
    assert "runtime-gate.sh" in posix
    assert r"runtime-gate.ps1" in powershell
    assert "payload-dir" not in posix
    assert "payload-dir" not in powershell


def test_runtime_gates_keep_first_use_provisioning_lock() -> None:
    posix = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(encoding="utf-8")
    assert ".provision.lock" in posix
    assert ".provision.lock" in powershell
    assert "probe-plugin-publication" in posix
    assert "probe-plugin-publication" in powershell
    assert "probe-legacy" in posix
    assert "probe-legacy" in powershell
    assert posix.index("\nassert_runtime_publication\n") < posix.index('mkdir -p "$RUNTIME_ROOT"')
    assert powershell.index("\nAssert-RuntimePublication\n") < powershell.index(
        "[void][IO.Directory]::CreateDirectory($runtimeRoot)"
    )
    assert posix.index("\nassert_runtime_publication\n", posix.index('.provision.lock')) < posix.index(
        'bash "$INSTALLER" provision --install-dir "$RUNTIME_ROOT"'
    )
    assert powershell.index("        Assert-RuntimePublication", powershell.index('.provision.lock')) < powershell.index(
        "-File $installer provision -InstallDir $runtimeRoot"
    )


def test_global_emitters_bind_the_publisher_and_delegate_the_runtime_gate() -> None:
    posix = (PLUGIN / "scripts" / "init.sh").read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts" / "init.ps1").read_text(encoding="utf-8")
    for text in (posix, powershell):
        assert "retained runtime gate is unavailable" in text
        assert 'export AGENT_CONTAINERS_HOME="$_root"' in text
        assert 'exec bash "$_payload/scripts/runtime-gate.sh" "$@"' in text
        assert 'installed-plugins/*/' not in text
    assert "$_root = '__ROOT__'" in powershell
    assert "$_source = '__SOURCE__'" in powershell
    assert '& $_gate @args' in powershell
    assert 'set "_PS1=%~dp0agent-containers.ps1"' in powershell
