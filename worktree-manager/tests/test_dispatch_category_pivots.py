"""Dispatch category contributions through the real attributed Picker registry."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from agent_procutil import no_window_kwargs
from dropin_registry import EntryDecision, ScanAuthority
from plugin_activation import ActivationReport, ActivePlugin
from worktree_manager.agent_worktrees_runtime import ensure_engine_runtime

ensure_engine_runtime()

from worktree_manager.production_picker.picker_tui import pivots  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "agent-dispatch"
SOURCE = "agent-dispatch@copilot-extensions"
NAMES = ("agent-dispatch.json", "agent-dispatch-maintenance.json")


def _payload(tmp_path, name="payload"):
    root = tmp_path / name
    (root / "pivots").mkdir(parents=True)
    (root / "bin").mkdir()
    for template in NAMES:
        shutil.copyfile(PLUGIN / "pivots" / template, root / "pivots" / template)
    for name in ("agent-dispatch", "agent-dispatch-board", "agent-bridge"):
        suffix = ".cmd" if os.name == "nt" else ""
        command = root / "bin" / (name + suffix)
        command.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n")
        if os.name != "nt":
            command.chmod(0o755)
    return root


def _activation(root=None):
    decisions = {}
    if root is not None:
        decisions[SOURCE] = EntryDecision.active(
            ActivePlugin(
                source=SOURCE,
                name="agent-dispatch",
                marketplace="copilot-extensions",
                root=root.resolve(),
                scopes=("global",),
            )
        )
    return ActivationReport(ScanAuthority.COMPLETE, decisions)


def _register_windows(root, home):
    powershell = shutil.which("pwsh") or shutil.which("powershell")
    assert powershell

    def quote(value):
        return "'" + str(value).removeprefix("\\\\?\\").replace("'", "''") + "'"

    script = (
        "$ErrorActionPreference='Stop'; $tokens=$null; $errors=$null; "
        f"$ast=[System.Management.Automation.Language.Parser]::ParseFile({quote(PLUGIN / 'scripts' / 'install.ps1')},"
        "[ref]$tokens,[ref]$errors); if($errors.Count){throw 'Installer parse failure'}; "
        "$definition=$ast.Find({param($node) "
        "$node -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $node.Name -eq 'Register-PickerPivot'},$true); "
        "Invoke-Expression $definition.Extent.Text; "
        "function Write-Ok {param($message)}; function Write-Skip {param($message) throw $message}; "
        f"$PluginDir={quote(root)}; $env:USERPROFILE={quote(home)}; "
        "Register-PickerPivot; Register-PickerPivot"
    )
    subprocess.run(
        [powershell, "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
        **no_window_kwargs(),
    )


@pytest.mark.guard
def test_dispatch_category_materialization_uses_one_provider_and_two_verified_templates(tmp_path):
    root = _payload(tmp_path)
    registry = tmp_path / "registry"
    report = pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert {pivot.label for pivot in report.pivots} == {"Tasks", "Maintenance tasks"}
    assert {item.owner for item in report.contributions} == {SOURCE}
    assert {item.entry_class for item in report.contributions} == {"managed-plugin"}
    for name in NAMES:
        pointer = json.loads((registry / name).read_text())
        assert pointer == {
            "schema_version": 3,
            "plugin": SOURCE,
            "plugin_root": str(root.resolve()),
            "template": name,
        }
    before = {name: (registry / name).read_bytes() for name in NAMES}
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert {name: (registry / name).read_bytes() for name in NAMES} == before


@pytest.mark.guard
def test_dispatch_category_disabled_provider_withdraws_both_without_retention_changes(tmp_path):
    root = _payload(tmp_path)
    registry = tmp_path / "registry"
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    report = pivots.scan_pivot_registry(registry, activation_report=_activation())
    assert not report.pivots
    assert {finding.reason for finding in report.findings} == {"not-enabled"}
    assert all((registry / name).exists() for name in NAMES)


@pytest.mark.guard
@pytest.mark.parametrize("name", NAMES)
def test_dispatch_category_stale_root_rejected_by_actual_discovery(tmp_path, name):
    root = _payload(tmp_path)
    replacement = _payload(tmp_path, "replacement")
    registry = tmp_path / "registry"
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_activation(replacement),
        materialize=False,
    )
    assert not report.pivots
    assert any(
        finding.entry == str(registry / name) and finding.reason == "identity-mismatch"
        for finding in report.findings
    )


@pytest.mark.guard
@pytest.mark.parametrize("name", NAMES)
def test_dispatch_category_baked_pointer_tampering_rejected_by_actual_discovery(tmp_path, name):
    root = _payload(tmp_path)
    registry = tmp_path / "registry"
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    pointer = json.loads((registry / name).read_text())
    pointer["list"] = ["untrusted-command"]
    (registry / name).write_text(json.dumps(pointer))
    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_activation(root),
        materialize=False,
    )
    assert name[:-5] not in {pivot.name for pivot in report.pivots}
    assert any(
        finding.entry == str(registry / name) and finding.reason == "invalid-entry"
        for finding in report.findings
    )


@pytest.mark.skipif(os.name != "nt", reason="native Windows contributor registration")
def test_dispatch_category_windows_registration_then_actual_discovery_is_attributed(tmp_path):
    root = _payload(tmp_path)
    home = tmp_path / "home"
    _register_windows(root, home)
    registry = home / ".agent-worktrees" / "pivots"
    report = pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert {item.entry_class for item in report.contributions} == {"managed-plugin"}
    assert {item.owner for item in report.contributions} == {SOURCE}
    assert {pivot.label for pivot in report.pivots} == {"Tasks", "Maintenance tasks"}
    for name in NAMES:
        assert json.loads((registry / name).read_text())["schema_version"] == 3
    before = {name: (registry / name).read_bytes() for name in NAMES}
    _register_windows(root, home)
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert {name: (registry / name).read_bytes() for name in NAMES} == before


@pytest.mark.skipif(os.name != "nt", reason="native Windows contributor registration")
def test_dispatch_category_windows_registration_cannot_activate_a_disabled_plugin(
    tmp_path, monkeypatch
):
    root = _payload(tmp_path)
    monkeypatch.setenv("PATH", str(root / "bin") + os.pathsep + os.environ.get("PATH", ""))
    home = tmp_path / "home"
    _register_windows(root, home)
    report = pivots.scan_pivot_registry(
        home / ".agent-worktrees" / "pivots",
        activation_report=_activation(),
    )
    assert not report.pivots
