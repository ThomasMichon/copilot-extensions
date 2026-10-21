"""Exercise the actual shared-template adopters before cold first-use writes."""
from __future__ import annotations

import os
import json
import re
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from test_adopter_publication import _adopter, _command
from test_command_binding import (
    POWERSHELL, ROOT, _snapshot, _write, binding_fixture as binding_fixture,
)
from test_installer_publication import _policy
from test_native_publication import _completed_slot, _run


def _release_payload(destination: Path, plugin: str) -> Path:
    source = ROOT / "plugins" / plugin
    payload = destination / plugin
    shutil.copytree(
        source, payload,
        ignore=shutil.ignore_patterns(
            ".git", ".venv", "__pycache__", ".pytest_cache", "tests", "build", "dist",
        ),
    )
    previous_path = sys.path[:]
    try:
        materializer = runpy.run_path(str(ROOT / "tools/materialize_main.py"))
        messages = materializer["materialize_uv_editable_ref_into"](
            source_consumer_dir=source, dest_consumer_dir=payload, canonical_root=ROOT,
        )
        messages += materializer["mie"].materialize_ref_into(
            source_consumer_dir=source, dest_consumer_dir=payload, canonical_root=ROOT,
        )
    finally:
        sys.path[:] = previous_path
    assert not any(message.startswith("SKIP ") for message in messages), messages
    return payload


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("older_source", [False, True])
def test_ssh_restamp_preserves_selected_snapshot_identity(
    binding_fixture: dict, tmp_path: Path, older_source: bool,
) -> None:
    released = _release_payload(tmp_path / "release", "agent-ssh")
    environment = _adopter(binding_fixture, "agent-ssh", released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    root = binding_fixture["legacy"]
    environment["AGENT_SSH_HOME"] = str(root)
    shell = "powershell" if os.name == "nt" else "bash"
    command = [*_command(binding_fixture, shell, "install"), "stamp"]
    first = _run(binding_fixture, environment, command)
    assert first.returncode == 0, first.stdout + first.stderr
    selected = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    previous = {name: (root / name).read_bytes() for name in ("payload-dir", "stamped-version")}
    provenance = (selected / "snapshot-provenance.json").read_bytes()
    if older_source:
        project = binding_fixture["payload"] / "pyproject.toml"
        project.write_text(re.sub(
            r'(?m)^version\s*=\s*"[^"]+"', 'version = "0.0.0"',
            project.read_text(encoding="utf-8"), count=1,
        ), encoding="utf-8")
    second = _run(binding_fixture, environment, command)
    assert second.returncode == 0, second.stdout + second.stderr
    assert all((root / name).read_bytes() == value for name, value in previous.items())
    assert (selected / "snapshot-provenance.json").read_bytes() == provenance


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("plugin", ["agent-pull-requests", "agent-vault", "agent-ssh"])
@pytest.mark.parametrize("state", [
    "managed", "standalone", "healthy", "old-resolver", "missing-admission",
    "invalid", "foreign", "future",
])
def test_shared_actual_stamp_first_use_admission(
    binding_fixture: dict, tmp_path: Path, plugin: str, state: str,
) -> None:
    released = _release_payload(tmp_path / "release", plugin)
    environment = _adopter(binding_fixture, plugin, released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    environment[plugin.upper().replace("-", "_") + "_HOME"] = str(binding_fixture["legacy"])
    shell = "powershell" if os.name == "nt" else "bash"
    stamped = _run(
        binding_fixture, environment, [*_command(binding_fixture, shell, "install"), "stamp"],
    )
    assert stamped.returncode == 0, stamped.stdout + stamped.stderr
    root = binding_fixture["legacy"]
    policy_state = state if state in ("standalone", "invalid", "foreign", "future") else "managed"
    _policy(binding_fixture, root, binding_fixture["arguments"], policy_state)
    snapshot = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    if snapshot.is_relative_to(root):
        assert (snapshot / "snapshot-provenance.json").is_file()
        if plugin == "agent-ssh":
            provenance = json.loads((snapshot / "snapshot-provenance.json").read_text(encoding="utf-8"))
            assert provenance["version"] == 2
            assert provenance["snapshotId"] == snapshot.name
            assert provenance["payloadVersion"] == (root / "stamped-version").read_text().strip()
            assert snapshot.name != provenance["payloadVersion"]
        binding_fixture["payload"].rename(binding_fixture["payload"].with_name("publisher-unavailable"))
    installer = snapshot / "scripts" / ("install.ps1" if os.name == "nt" else "install.sh")
    installer.write_text(
        "[IO.File]::WriteAllText((Join-Path $PSScriptRoot '..\\installer-ran'), 'yes'); exit 37\n"
        if os.name == "nt" else
        '#!/usr/bin/env bash\nprintf yes > "$(dirname "$0")/../installer-ran"\nexit 37\n',
        encoding="utf-8",
    )
    if state == "old-resolver":
        resolver = root / "bin" / ("resolve-runtime.ps1" if os.name == "nt" else "resolve-runtime.sh")
        resolver.write_text(resolver.read_text(encoding="utf-8").replace(
            "# Trace contract: normal/read-only/ready-v1", "# Older resolver without deferred tracing",
        ), encoding="utf-8")
    elif state == "missing-admission":
        suffix = "ps1" if os.name == "nt" else "sh"
        (snapshot / f"scripts/installation-context/plugin-publication-probe.{suffix}").unlink()
    elif state == "healthy":
        python = _completed_slot(binding_fixture)
        site = python.parent.parent / (
            "Lib/site-packages" if os.name == "nt" else
            f"lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        )
        (site / (plugin.replace("-", "_") + ".py")).write_text(
            'print("healthy selected runtime")\n', encoding="utf-8",
        )
    for key in (
        "COPILOT_PLUGIN_ROOT", "COPILOT_EXTENSIONS_COMMAND_LIFECYCLE",
        "COPILOT_PLUGIN_INSTALL_STAGED", "COPILOT_PLUGIN_STAGED_FROM",
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT",
    ):
        environment.pop(key, None)
    wrapper = binding_fixture["home"] / ".local/bin" / plugin
    command = (
        [POWERSHELL, "-NoLogo", "-NoProfile", "-File", str(wrapper.with_suffix(".ps1")), "--help"]
        if os.name == "nt" else [str(wrapper), "--help"]
    )
    before = _snapshot(root)
    result = subprocess.run(
        command, env=environment, cwd=snapshot, capture_output=True, text=True,
        timeout=60, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    marker = snapshot / "installer-ran"
    if state == "standalone":
        assert result.returncode == 37, result.stdout + result.stderr
        assert marker.read_text(encoding="utf-8") == "yes"
        assert (root / "logs/boot-trace.jsonl").is_file()
        assert (root / ".provision.lock").is_file()
        return
    if state == "healthy":
        assert result.returncode == 0, result.stdout + result.stderr
        assert "healthy selected runtime" in result.stdout
        assert not marker.exists()
        assert (root / "logs/boot-trace.jsonl").is_file()
        assert not (root / ".provision.lock").exists()
        return
    after = _snapshot(root)
    changed = [name for name in before.keys() | after.keys() if before.get(name) != after.get(name)]
    assert not changed, f"Runtime tree changed: {changed[:12]}\n{result.stdout}{result.stderr}"
    assert result.returncode != 0
    diagnostic = {
        "old-resolver": "read-only runtime resolver is unavailable",
        "missing-admission": "snapshot admission helper is unavailable",
        "invalid": "deployment-policy-invalid",
        "foreign": "deployment-policy-invalid",
        "future": "deployment-policy-version-unsupported",
    }.get(state, "deployment-managed")
    assert diagnostic in result.stdout + result.stderr, result.stdout + result.stderr


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("plugin", ["agent-pull-requests", "agent-vault", "agent-ssh"])
@pytest.mark.parametrize("failure", ["missing-resolver", "command-directory", "legacy-command-directory"])
def test_actual_restamp_failure_preserves_previous_selection(
    binding_fixture: dict, tmp_path: Path, plugin: str, failure: str,
) -> None:
    released = _release_payload(tmp_path / "release", plugin)
    environment = _adopter(binding_fixture, plugin, released)
    environment.pop("COPILOT_PLUGIN_INSTALL_SMOKE", None)
    temporary = binding_fixture["home"] / "tmp"
    temporary.mkdir()
    environment.update({key: str(temporary) for key in ("TEMP", "TMP", "TMPDIR")})
    environment[plugin.upper().replace("-", "_") + "_HOME"] = str(binding_fixture["legacy"])
    shell = "powershell" if os.name == "nt" else "bash"
    command = [*_command(binding_fixture, shell, "install"), "stamp"]
    first = _run(binding_fixture, environment, command)
    assert first.returncode == 0, first.stdout + first.stderr
    root = binding_fixture["legacy"]
    selected = Path((root / "payload-dir").read_text(encoding="utf-8-sig").strip())
    (selected / "previous-selection-sentinel").write_text("previous complete snapshot\n", encoding="utf-8")
    if failure == "legacy-command-directory" and (selected / "snapshot-provenance.json").is_file():
        (selected / "snapshot-provenance.json").unlink()
    if failure == "missing-resolver":
        (binding_fixture["payload"] / "scripts/resolve-runtime.sh").unlink()
    else:
        wrapper = binding_fixture["home"] / ".local/bin" / plugin
        if os.name == "nt":
            wrapper = wrapper.with_suffix(".ps1")
        wrapper.unlink()
        wrapper.mkdir()
        (wrapper / "sentinel").write_text("not a command\n", encoding="utf-8")
    markers = {
        name: (root / name).read_bytes() for name in (
            "payload-dir", "stamped-version", "current-version", "last-known-good",
        ) if (root / name).exists()
    }
    before = _snapshot(selected)
    second = _run(binding_fixture, environment, command)
    assert second.returncode != 0, second.stdout + second.stderr
    assert "Stamped:" not in second.stdout
    assert all((root / name).read_bytes() == value for name, value in markers.items())
    assert _snapshot(selected) == before


@pytest.mark.portfolio_tier("T3")
@pytest.mark.parametrize("namespaced", [False, True])
@pytest.mark.parametrize("managed", [False, True])
def test_vault_cold_payload_uses_selected_publication_policy(
    binding_fixture: dict, namespaced: bool, managed: bool,
) -> None:
    environment = _adopter(binding_fixture, "agent-vault")
    root = binding_fixture["legacy"]
    arguments = binding_fixture["arguments"]
    if namespaced:
        primitive = binding_fixture["primitive"]
        cell = primitive.stamp_context(
            payload_version="1.0.0", payload_origin="installed",
            expected_namespace_generation=0, expected_install_generation=0,
            **{key: arguments[key] for key in ("payload_root", "plugin_id", "environment")},
        )
        primitive.compare_and_swap_activation(
            context=cell["installReceipt"], expected_marketplace_id=cell["marketplaceId"],
            expected_plugin_id="agent-vault", expected_namespace_generation=cell["namespaceGeneration"],
            expected_install_generation=cell["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active", legacy_disposition="absent",
            legacy_probe={"declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z"},
            legacy_root=root, environment=binding_fixture["environment"],
        )
        _write(binding_fixture["home"] / ".copilot-extensions/installation-mode.json", {
            "schema": "copilot-extensions.installation-mode", "version": 1,
            "installationMode": {"enabled": True, "marketplaces": {}},
        })
        root = Path(cell["pluginRoot"])
        arguments = {**arguments, "context": cell["installReceipt"]}
        environment["COPILOT_EXTENSIONS_CONTEXT"] = cell["installReceipt"]
        _write(binding_fixture["legacy"] / "runtime-deployment.json", {"managed": not managed})
    _policy(binding_fixture, root, arguments, "managed" if managed else "standalone")
    environment.update(
        AGENT_VAULT_HOME=str(binding_fixture["legacy"]) + os.sep,
        AGENT_VAULT_PAYLOAD_ROOT=str(binding_fixture["payload"]),
    )
    shell = "powershell" if os.name == "nt" else "bash"
    installer = binding_fixture["payload"] / "scripts" / (
        "install.ps1" if os.name == "nt" else "install.sh"
    )
    installer.write_text(
        "[Console]::Error.WriteLine('installer diagnostic'); "
        "[IO.File]::WriteAllText((Join-Path $env:AGENT_VAULT_HOME 'installer-ran'), 'yes'); exit 37\n"
        if os.name == "nt" else
        '#!/usr/bin/env bash\nprintf "installer diagnostic\\n" >&2\n'
        'printf yes > "$AGENT_VAULT_HOME/installer-ran"\nexit 37\n',
        encoding="utf-8",
    )
    before = _snapshot(root)
    legacy_before = _snapshot(binding_fixture["legacy"])
    result = _run(binding_fixture, environment, _command(binding_fixture, shell, "runtime-gate"))
    if namespaced:
        assert _snapshot(binding_fixture["legacy"]) == legacy_before
    if managed:
        assert result.returncode == 3, result.stdout + result.stderr
        assert "deployment-managed" in result.stdout + result.stderr
        assert _snapshot(root) == before
    else:
        assert result.returncode == 37, result.stdout + result.stderr
        assert (root / "installer-ran").read_text(encoding="utf-8") == "yes"
        assert "installer diagnostic" in result.stderr
        assert "failed rc=37" in (root / ".provision-status").read_text(encoding="utf-8-sig")
        assert (root / "logs/boot-trace.jsonl").is_file()
