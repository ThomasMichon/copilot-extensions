"""Direct coverage for plugin-owned Picker pivot visibility."""

from __future__ import annotations

import json
import os
from pathlib import Path

from dropin_registry import EntryDecision, ScanAuthority, ScanSnapshot
from plugin_activation import ActivationReport, ActivePlugin

from agent_worktrees.picker_support import pivot_manifest, pivots


def _write(directory, name, data):
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _command(root, name: str = "sample"):
    suffix = ".cmd" if os.name == "nt" else ""
    command = root / "bin" / f"{name}{suffix}"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n")
    if os.name != "nt":
        command.chmod(0o755)
    return command


def _template(
    root,
    *,
    filename: str = "sample.json",
    label: str = "Sample",
    command: str = "sample",
):
    path = root / "pivots" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"schema_version": 1, "label": label, "list": [command, "list"]}),
        encoding="utf-8",
    )
    return path


def _active_report(source: str, root):
    active = ActivePlugin(
        source=source,
        name=source.split("@", 1)[0],
        marketplace=source.split("@", 1)[1],
        root=root.resolve(),
        scopes=("global",),
    )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={source: EntryDecision.active(active)},
    )


def test_materialize_publishes_a_pointer_not_baked_content(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"

    report = pivots.scan_pivot_registry(
        registry, activation_report=_active_report(source, root)
    )

    entry = registry / "sample.json"
    assert [pivot.label for pivot in report.pivots] == ["Sample"]
    payload = json.loads(entry.read_text(encoding="utf-8"))
    assert payload == {
        "schema_version": pivot_manifest.MANAGED_SCHEMA_VERSION,
        "plugin": source,
        "plugin_root": str(root.resolve()),
        "template": "sample.json",
    }


def test_template_content_edit_needs_no_pointer_rewrite(tmp_path):
    """An ordinary template edit (no root/version change) resolves fresh at
    read time and never touches the on-disk pointer -- no duplicate file, no
    findings, byte-identical pointer across scans."""
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    template = _template(root)
    registry = tmp_path / "pivots"
    activation = _active_report(source, root)

    first = pivots.scan_pivot_registry(registry, activation_report=activation)
    assert not first.findings
    entry = registry / "sample.json"
    before = entry.read_text(encoding="utf-8")

    template.write_text(
        json.dumps({"schema_version": 1, "label": "Updated", "list": ["sample", "list"]}),
        encoding="utf-8",
    )
    updated = pivots.scan_pivot_registry(registry, activation_report=activation)

    assert [pivot.label for pivot in updated.pivots] == ["Updated"]
    assert not updated.findings
    assert sorted(p.name for p in registry.glob("*.json")) == ["sample.json"]
    assert entry.read_text(encoding="utf-8") == before  # pointer itself unchanged


def test_plugin_root_change_refreshes_the_same_pointer_in_place(tmp_path):
    """A reinstall/version bump (new root) is the one case that DOES need a
    rewrite -- and it overwrites the same canonical file rather than forking
    a fingerprinted duplicate."""
    source = "sample@example-marketplace"
    old_root = tmp_path / "plugin-v1"
    new_root = tmp_path / "plugin-v2"
    _command(old_root)
    _template(old_root)
    _command(new_root)
    _template(new_root, label="V2")
    registry = tmp_path / "pivots"

    pivots.scan_pivot_registry(
        registry, activation_report=_active_report(source, old_root)
    )
    second = pivots.scan_pivot_registry(
        registry, activation_report=_active_report(source, new_root)
    )

    assert sorted(p.name for p in registry.glob("*.json")) == ["sample.json"]
    assert [pivot.label for pivot in second.pivots] == ["V2"]
    payload = json.loads((registry / "sample.json").read_text(encoding="utf-8"))
    assert Path(payload["plugin_root"]) == new_root.resolve()


def test_tampered_plugin_root_is_identity_mismatch(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"
    activation = _active_report(source, root)
    pivots.scan_pivot_registry(registry, activation_report=activation)

    entry = registry / "sample.json"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["plugin_root"] = str(tmp_path / "somewhere-else")
    entry.write_text(json.dumps(payload), encoding="utf-8")

    tampered = pivots.scan_pivot_registry(
        registry, materialize=False, activation_report=activation
    )

    assert tampered.active_entries == {}
    assert tampered.findings[0].reason == "identity-mismatch"


def test_operator_file_is_never_overwritten_by_materialize(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root, label="Same")
    registry = tmp_path / "pivots"
    registry.mkdir()
    operator_command = _command(tmp_path / "operator")
    operator = registry / "sample.json"
    operator.write_text(
        json.dumps({"label": "Same", "list": [str(operator_command)]}),
        encoding="utf-8",
    )

    report = pivots.scan_pivot_registry(
        registry, activation_report=_active_report(source, root)
    )

    assert operator.read_text(encoding="utf-8") == json.dumps(
        {"label": "Same", "list": [str(operator_command)]}
    )
    assert len(report.contributions) == 1
    assert report.contributions[0].entry_class == "operator"


def test_same_named_template_from_two_plugins_publishes_neither(tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    _command(first_root, "first")
    _command(second_root, "second")
    _template(first_root, filename="shared.json", command="first")
    _template(second_root, filename="shared.json", command="second")
    registry = tmp_path / "pivots"

    report = pivots.scan_pivot_registry(
        registry,
        activation_report=ActivationReport(
            authority=ScanAuthority.COMPLETE,
            decisions={
                "first@example-marketplace": EntryDecision.active(
                    ActivePlugin(
                        source="first@example-marketplace",
                        name="first",
                        marketplace="example-marketplace",
                        root=first_root.resolve(),
                        scopes=("global",),
                    )
                ),
                "second@example-marketplace": EntryDecision.active(
                    ActivePlugin(
                        source="second@example-marketplace",
                        name="second",
                        marketplace="example-marketplace",
                        root=second_root.resolve(),
                        scopes=("global",),
                    )
                ),
            },
        ),
    )

    assert report.pivots == []
    assert not (registry / "shared.json").exists()


def test_superseded_v2_baked_manifest_still_contributes_advisory(tmp_path):
    """A pre-existing on-disk file from before the pointer redesign (full
    baked content, schema_version 2) keeps working -- advisory, prunable,
    and resolved fresh from the live template (not its own stale baked
    label) -- rather than breaking outright; the materializer never
    republishes at the old schema version."""
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    command = _command(root)
    _template(root)  # the live template identity-verification now requires
    registry = tmp_path / "pivots"
    registry.mkdir()
    legacy = registry / "sample.json"
    legacy.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "plugin": source,
                "plugin_root": str(root.resolve()),
                "template": "sample.json",
                "label": "Legacy",
                "list": [str(command.resolve()), "list"],
            }
        ),
        encoding="utf-8",
    )

    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=_active_report(source, root),
    )

    # Resolved from the live template, not the stale baked "Legacy" label --
    # a v2 entry gets the exact same fresh-resolution treatment as a v3
    # pointer, it is just additionally flagged for migration.
    assert [pivot.label for pivot in report.pivots] == ["Sample"]
    assert report.entry_classes[str(legacy)] == "unknown-legacy"
    assert any(
        finding.reason == "legacy-unattributed" for finding in report.findings
    )


def test_superseded_v2_manifest_deactivates_with_its_plugin(tmp_path):
    """Unlike an unattributed manifest, a v2 legacy entry is NOT a bypass of
    activation/root identity verification: a disabled plugin (or a root that
    no longer matches) deactivates it exactly like a current pointer."""
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"
    registry.mkdir()
    legacy = registry / "sample.json"
    legacy.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "plugin": source,
                "plugin_root": str(root.resolve()),
                "template": "sample.json",
                "label": "Legacy",
                "list": ["stale-command"],
            }
        ),
        encoding="utf-8",
    )

    disabled = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(
            authority=ScanAuthority.COMPLETE, decisions={}
        ),
    )

    assert disabled.active_entries == {}
    assert disabled.findings[0].entry == str(legacy)
    assert disabled.findings[0].reason == "not-enabled"


def test_configured_pivot_requires_state_root_file(tmp_path, monkeypatch):
    manifests = tmp_path / "pivots"
    manifests.mkdir()
    state_root = tmp_path / "state"
    state_root.mkdir()
    _write(
        manifests,
        "configured",
        {
            "label": "Configured",
            "list": ["configured"],
            "visible_when": {"state_root_file": "feature/config.json"},
        },
    )
    calls = []
    monkeypatch.setattr(
        pivots,
        "_resolve_state_root_path",
        lambda: calls.append(True) or state_root,
    )

    assert pivots.discover_pivots(manifests) == []
    assert len(calls) == 1

    config = state_root / "feature" / "config.json"
    config.parent.mkdir()
    config.write_text("{}", encoding="utf-8")
    assert [pivot.label for pivot in pivots.discover_pivots(manifests)] == [
        "Configured"
    ]
    assert len(calls) == 2


def test_unbound_state_root_is_resolved_once(tmp_path, monkeypatch):
    for name in ("first", "second"):
        _write(
            tmp_path,
            name,
            {
                "label": name.title(),
                "list": [name],
                "visible_when": {"state_root_file": f"{name}.json"},
            },
        )
    calls = []
    monkeypatch.setattr(
        pivots,
        "_resolve_state_root_path",
        lambda: calls.append(True) or None,
    )

    assert pivots.discover_pivots(tmp_path) == []
    assert len(calls) == 1


def _finding(entry, *, reason, status="inactive", target=None, owner=None):
    return pivots.Finding(
        registry="pivots",
        entry=str(entry),
        status=status,
        reason=reason,
        target=str(target) if target is not None else None,
        owner=owner,
    )


def _report(findings, entry_classes):
    snapshot = ScanSnapshot(
        registry="pivots",
        authority=ScanAuthority.COMPLETE,
        decisions={},
        findings=tuple(findings),
    )
    return pivots.PivotRegistryReport(
        snapshot=snapshot, active_entries={}, entry_classes=entry_classes
    )


def test_prunable_findings_excludes_operator_and_unparseable_entries(tmp_path):
    """Only plugin-generated, provably-superseded findings are prunable --
    never an operator-authored manifest (schema_version absent) or one whose
    JSON couldn't even be parsed (may be a hand-edit gone wrong), and never an
    indeterminate/advisory finding."""
    stale_managed = _write(tmp_path, "agent-bridge.deadbeef0000", {})
    stale_legacy = _write(tmp_path, "agent-dispatch", {})
    operator_broken = _write(tmp_path, "my-manifest", {})
    corrupt = _write(tmp_path, "corrupt", {})
    indeterminate = _write(tmp_path, "locked", {})

    findings = [
        _finding(stale_managed, reason="identity-mismatch"),
        _finding(stale_legacy, reason="duplicate", target="agent-dispatch.abc123"),
        _finding(operator_broken, reason="missing-target"),
        _finding(corrupt, reason="invalid-entry"),
        _finding(indeterminate, reason="entry-indeterminate", status="indeterminate"),
    ]
    entry_classes = {
        str(stale_managed): "managed-plugin",
        str(stale_legacy): "legacy-plugin",
        str(operator_broken): "operator",
        str(corrupt): "unknown",
        str(indeterminate): "managed-plugin",
    }
    report = _report(findings, entry_classes)

    prunable = pivots.prunable_findings(report)
    assert {finding.entry for finding in prunable} == {
        str(stale_managed),
        str(stale_legacy),
    }


def test_prune_stale_entries_dry_run_does_not_touch_disk(tmp_path):
    stale = _write(tmp_path, "agent-bridge.deadbeef0000", {})
    report = _report(
        [_finding(stale, reason="identity-mismatch")],
        {str(stale): "managed-plugin"},
    )

    plan = pivots.prune_stale_entries(report, base=tmp_path, apply=False)

    assert stale.exists()
    assert len(plan) == 1
    assert plan[0]["removed"] is False
    assert plan[0]["entry"] == str(stale)


def test_prune_stale_entries_apply_removes_only_prunable_files(tmp_path):
    stale = _write(tmp_path, "agent-bridge.deadbeef0000", {})
    operator_broken = _write(tmp_path, "my-manifest", {})
    report = _report(
        [
            _finding(stale, reason="identity-mismatch"),
            _finding(operator_broken, reason="missing-target"),
        ],
        {str(stale): "managed-plugin", str(operator_broken): "operator"},
    )

    results = pivots.prune_stale_entries(report, base=tmp_path, apply=True)

    assert not stale.exists()  # the prunable, plugin-owned copy is gone
    assert operator_broken.exists()  # never touched -- not in the prune plan
    assert len(results) == 1
    assert results[0]["removed"] is True


def test_prune_stale_entries_is_idempotent_on_already_removed_file(tmp_path):
    stale = _write(tmp_path, "agent-bridge.deadbeef0000", {})
    report = _report(
        [_finding(stale, reason="identity-mismatch")],
        {str(stale): "managed-plugin"},
    )
    stale.unlink()  # simulate a concurrent/prior removal

    results = pivots.prune_stale_entries(report, base=tmp_path, apply=True)

    assert results[0]["removed"] is True  # already gone counts as success
    assert "error" not in results[0]


def test_prune_stale_entries_refuses_path_outside_registry_directory(tmp_path):
    manifests = tmp_path / "pivots"
    manifests.mkdir()
    outside = _write(tmp_path, "escaped", {})  # sibling of the pivots dir, not in it
    report = _report(
        [_finding(outside, reason="identity-mismatch")],
        {str(outside): "managed-plugin"},
    )

    results = pivots.prune_stale_entries(report, base=manifests, apply=True)

    assert outside.exists()
    assert results[0]["removed"] is False
    assert "outside the pivots registry directory" in results[0]["error"]
