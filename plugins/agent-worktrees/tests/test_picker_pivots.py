"""Direct coverage for plugin-owned Picker pivot visibility."""

from __future__ import annotations

import json

from dropin_registry import ScanAuthority, ScanSnapshot

from agent_worktrees.picker_tui import pivots


def _write(directory, name, data):
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


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
