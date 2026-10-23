"""Native early refusal never starts acquisition or creates driver/plugin state."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from agent_procutil import no_window_kwargs

from test_first_use import primitive


def native_command(root, payload, installation, project):
    source = Path(__file__).resolve().parents[1]
    if os.name == "nt":
        shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        return [
            str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
            str(source / "scripts" / "first-use.ps1"), "-Root", str(root),
            "-Payload", str(payload), "-RuntimeRoot", str(installation), "-Project", str(project),
        ]
    if sys.platform != "linux":
        pytest.skip("native first-use bootstrap is not qualified here")
    return [
        shutil.which("bash"), str(source / "scripts" / "first-use.sh"),
        str(root), str(payload), str(installation), str(project),
    ]


@pytest.mark.parametrize("reason", ["no-self", "managed", "malformed", "wrong-root"])
def test_native_refusal_is_before_foundation_or_plugin_mutation(
    owning_payload, area, monkeypatch, reason,
):
    payload, installation, project = owning_payload
    root = area / "cold"
    if reason == "no-self":
        monkeypatch.setenv("AGENT_VAULT_NO_SELFPROVISION", "1")
    elif reason in {"managed", "malformed"}:
        module = primitive(payload)
        arguments = dict(
            payload_root=payload, plugin_id="agent-vault", legacy_root=installation,
            project_root=project,
        )
        resolution = module.resolve_installation_mode(**arguments)
        installation.mkdir()
        policy = {
            "schema": module.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1,
            "owner": module._deployment_owner(resolution, arguments), "managed": True,
        } if reason == "managed" else {"version": 999}
        (installation / "runtime-deployment.json").write_text(json.dumps(policy), encoding="utf-8")
    else:
        root = payload / "wrong-root"
    before = {str(p.relative_to(installation)): p.read_bytes()
              for p in installation.rglob("*") if p.is_file()}
    result = subprocess.run(
        native_command(root, payload, installation, project),
        env=dict(os.environ), capture_output=True, text=True, timeout=60,
        **no_window_kwargs(),
    )
    assert result.returncode != 0, result.stdout + result.stderr
    assert not root.exists(), result.stdout + result.stderr
    assert {str(p.relative_to(installation)): p.read_bytes()
            for p in installation.rglob("*") if p.is_file()} == before


def test_native_preflight_reaches_exact_source_check_without_writes(owning_payload, area):
    payload, installation, project = owning_payload
    root = area / "cold"
    command = native_command(root, payload, installation, project)
    if os.name == "nt":
        command += ["-RunOwner", "-ExpectedSource", "sha256:" + "0" * 64]
    else:
        command += ["--run-owner", "sha256:" + "0" * 64]
    result = subprocess.run(
        command, env=dict(os.environ), capture_output=True, text=True, timeout=60,
        **no_window_kwargs(),
    )
    assert result.returncode != 0
    assert "Owning source changed after native first-use admission" in result.stderr, (
        result.stdout, result.stderr,
    )
    assert not root.exists() and not installation.exists()
