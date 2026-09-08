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
