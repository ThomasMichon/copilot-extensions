"""Dispatch category contributions through the real attributed Picker registry."""

from __future__ import annotations

import json
import hashlib
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
from worktree_manager.production_picker.picker_tui.pivot_actions import format_form_template  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PLUGIN = REPO / "plugins" / "agent-dispatch"
SOURCE = "agent-dispatch@copilot-extensions"
NAMES = ("agent-dispatch.json", "agent-dispatch-maintenance.json")
PRIOR_TASKS_HASH = "ef0c01ef40c70500ec4ce4910abfea0927cb37ccdc6f69b8fec8f7ac18d527ef"


def _prior_tasks():
    prior = json.loads((PLUGIN / "pivots" / NAMES[0]).read_text(encoding="utf-8"))
    prior.pop("view_actions", None)
    prior["list"] = prior["list"][:-2]
    prior["actions"] = [
        action for action in prior["actions"] if action["key"] != "inspect-evidence"
    ]
    steer = next(action for action in prior["actions"] if action["key"] == "steer")
    steer.pop("result_view", None)
    steer.pop("draft", None)
    steer.pop("request", None)
    steer["run"] = [
        "agent-dispatch", "steer", "submit", "{task_id}", "{fields}", "--expected-status", "{status}",
    ]
    assert (
        hashlib.sha256(
            json.dumps(prior, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        == PRIOR_TASKS_HASH
    )
    return prior


@pytest.mark.parametrize("native", [pytest.param(False, marks=pytest.mark.guard), True])
def test_dispatch_category_known_prior_v1_upgrades_without_losing_tasks(tmp_path, native):
    if native and os.name != "nt":
        pytest.skip("native Windows upgrade")
    root = _payload(tmp_path)
    home = tmp_path / "home"
    registry = home / ".agent-worktrees" / "pivots"
    registry.mkdir(parents=True)
    legacy = registry / NAMES[0]
    legacy.write_text(json.dumps(_prior_tasks()), encoding="utf-8")
    if native:
        _register_windows(root, home)
    report = pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert {pivot.label for pivot in report.pivots} == {"Tasks", "Maintenance tasks"}
    assert {item.entry_class for item in report.contributions} == {"managed-plugin"}
    pointer = json.loads(legacy.read_text())
    assert pointer["schema_version"] == 3 and pointer["plugin"] == SOURCE
    before = legacy.read_bytes()
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert legacy.read_bytes() == before
    assert not pivots.scan_pivot_registry(registry, activation_report=_activation()).pivots


@pytest.mark.guard
@pytest.mark.parametrize(
    "case", ["custom", "foreign", "unknown", "future", "malformed", "invalid-utf8"]
)
def test_dispatch_category_legacy_migration_preserves_unowned_artifacts(tmp_path, case):
    root = _payload(tmp_path)
    registry = tmp_path / "registry"
    registry.mkdir()
    name = "unknown.json" if case == "unknown" else NAMES[0]
    data = _prior_tasks()
    if case == "custom":
        data["label"] = "Custom tasks"
    elif case == "foreign":
        data.update(schema_version=3, plugin="other@example", plugin_root=str(root), template=name)
    elif case == "future":
        data.update(schema_version=9)
    artifact = registry / name
    artifact.write_text(json.dumps(data), encoding="utf-8")
    if case == "malformed":
        artifact.write_bytes(b"{")
    elif case == "invalid-utf8":
        artifact.write_bytes(b"\xff")
    before = artifact.read_bytes()
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert artifact.read_bytes() == before


@pytest.mark.guard
def test_dispatch_category_legacy_upgrade_rechecks_ownership_before_replace(tmp_path, monkeypatch):
    from worktree_manager.production_picker.picker_tui import (
        pivot_registry_materialization as materializer,
    )

    root = _payload(tmp_path)
    registry = tmp_path / "registry"
    registry.mkdir()
    artifact = registry / NAMES[0]
    artifact.write_text(json.dumps(_prior_tasks()), encoding="utf-8")
    custom = {**_prior_tasks(), "label": "Operator changed tasks"}
    original = materializer.tempfile.mkstemp
    calls = 0

    def prepare(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        if kwargs.get("prefix") == f".{NAMES[0]}.":
            calls += 1
            if calls == 2:
                artifact.write_text(json.dumps(custom), encoding="utf-8")
        return result

    monkeypatch.setattr(materializer.tempfile, "mkstemp", prepare)
    pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert calls == 2
    assert json.loads(artifact.read_text()) == custom


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


@pytest.mark.guard
@pytest.mark.parametrize("existing", ["fresh", "prior-v1", "owned-v3"])
def test_dispatch_category_actual_total_order_preserves_builtin_owner_and_ids(tmp_path, existing):
    root = _payload(tmp_path)
    registry = tmp_path / "registry"
    if existing == "prior-v1":
        registry.mkdir()
        (registry / NAMES[0]).write_text(json.dumps(_prior_tasks()), encoding="utf-8")
    elif existing == "owned-v3":
        pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    pointers_before = (
        {name: (registry / name).read_bytes() for name in NAMES}
        if existing == "owned-v3"
        else None
    )
    report = pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    ordered = pivots.order_pivots(("Worktrees", "Maintenance", "Profiles"), report.pivots)
    assert [item["label"] for item in ordered] == [
        "Worktrees",
        "Tasks",
        "Maintenance tasks",
        "Maintenance",
        "Profiles",
    ]
    builtin = next(item for item in ordered if item["label"] == "Maintenance")
    assert builtin["kind"] == "maintenance" and builtin["pivot"] is None
    assert {item["pivot"].name for item in ordered if item["kind"] == "registered"} == {
        name[:-5] for name in NAMES
    }
    assert len(report.contributions) == 2
    for pivot in report.pivots:
        action = next(action for action in pivot.actions if action.key == "steer")
        assert action.form is not None
        scope = {"queue": "original-queue", "installation": "original-install", "machine": "host"}
        ctx = {
            "task_id": "task-one", "status": "suspended", "steering_revision": "owner-snapshot",
            "draft_scope": scope, "request_id": "original-request",
        }
        fields = {"answer": "first\nsecond {steering_revision}"}
        argv = format_form_template(action.form["request"]["prepare"], ctx, fields)
        assert argv[argv.index("--expected-revision") + 1] == "owner-snapshot"
        assert argv[argv.index("--field") + 1] == "answer=first\nsecond {steering_revision}"
        assert argv[argv.index("--request-id") + 1] == "original-request"
        assert json.loads(argv[argv.index("--scope-json") + 1]) == scope
        prepared = {
            "scope": scope, "request_id": "original-request",
            "fields": fields, "sender": None, "wake": False,
        }
        ctx["prepared_request"] = prepared
        for template in (action.run, action.form["request"]["receipt"]):
            resolved = format_form_template(template, ctx, fields)
            assert "--field" not in resolved and "--expected-revision" not in resolved
            assert json.loads(resolved[resolved.index("--prepared-json") + 1]) == prepared
    if pointers_before is not None:
        assert {name: (registry / name).read_bytes() for name in NAMES} == pointers_before
    again = pivots.scan_pivot_registry(registry, activation_report=_activation(root))
    assert [pivot.name for pivot in again.pivots] == [pivot.name for pivot in report.pivots]


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
