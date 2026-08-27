"""Lifecycle and diagnostic tests for the strict Picker pivot registry."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest
from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    WarningTracker,
)
from plugin_activation import ActivationReport, ActivePlugin

from agent_worktrees.picker_tui import pivots


def _command(root: Path, name: str = "sample") -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    command = root / "bin" / f"{name}{suffix}"
    command.parent.mkdir(parents=True, exist_ok=True)
    command.write_text("@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n")
    if os.name != "nt":
        command.chmod(0o755)
    return command


def _template(
    root: Path,
    *,
    filename: str = "sample.json",
    label: str = "Sample",
    command: str = "sample",
) -> Path:
    path = root / "pivots" / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "label": label,
                "list": [command, "list"],
            }
        ),
        encoding="utf-8",
    )
    return path


def _active_report(source: str, root: Path) -> ActivationReport:
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


def _active_reports(*items: tuple[str, Path]) -> ActivationReport:
    decisions = {}
    for source, root in items:
        active = ActivePlugin(
            source=source,
            name=source.split("@", 1)[0],
            marketplace=source.split("@", 1)[1],
            root=root.resolve(),
            scopes=("global",),
        )
        decisions[source] = EntryDecision.active(active)
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions=decisions,
    )


def test_active_plugin_materializes_attributed_absolute_command(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    command = _command(root)
    _template(root)
    registry = tmp_path / "pivots"

    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )

    entry = registry / "sample.json"
    assert [pivot.label for pivot in report.pivots] == ["Sample"]
    assert report.contributions[0].entry_class == "managed-plugin"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    assert payload["schema_version"] == pivots.MANAGED_SCHEMA_VERSION
    assert payload["plugin"] == source
    assert Path(payload["plugin_root"]) == root.resolve()
    assert payload["template"] == "sample.json"
    assert Path(payload["list"][0]) == command.resolve()


def test_invalid_plugin_template_does_not_block_valid_peer(tmp_path):
    bad_source = "bad@example-marketplace"
    bad_root = tmp_path / "bad"
    _command(bad_root, "bad")
    _template(bad_root, filename="bad.json", label="Bad", command="bad")
    bad_template = bad_root / "pivots" / "bad.json"
    bad_payload = json.loads(bad_template.read_text(encoding="utf-8"))
    bad_payload["list"] = []
    bad_template.write_text(json.dumps(bad_payload), encoding="utf-8")

    good_source = "good@example-marketplace"
    good_root = tmp_path / "good"
    _command(good_root, "good")
    _template(good_root, filename="good.json", label="Good", command="good")
    registry = tmp_path / "pivots"

    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_reports(
            (bad_source, bad_root),
            (good_source, good_root),
        ),
    )

    assert [pivot.label for pivot in report.pivots] == ["Good"]
    assert not (registry / "bad.json").exists()


def test_materializer_refreshes_append_only_without_overwriting(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    template = _template(root)
    registry = tmp_path / "pivots"
    activation = _active_report(source, root)
    first = pivots.scan_pivot_registry(registry, activation_report=activation)
    assert first.active_entries
    assert not first.findings
    assert not (registry / "pivot-receipts.json").exists()

    template.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "label": "Updated",
                "list": ["sample", "list"],
            }
        ),
        encoding="utf-8",
    )
    updated = pivots.scan_pivot_registry(registry, activation_report=activation)
    assert [pivot.label for pivot in updated.pivots] == ["Updated"]

    unreceipted = tmp_path / "unreceipted"
    unreceipted.mkdir()
    operator = unreceipted / "sample.json"
    operator.write_text(
        json.dumps(
            {
                "schema_version": pivots.MANAGED_SCHEMA_VERSION,
                "plugin": source,
                "plugin_root": str(root.resolve()),
                "template": "sample.json",
                "label": "Operator",
                "list": [str(_command(tmp_path / "operator"))],
            }
        ),
        encoding="utf-8",
    )
    before = operator.read_text(encoding="utf-8")
    report = pivots.scan_pivot_registry(
        unreceipted,
        activation_report=activation,
    )
    assert operator.read_text(encoding="utf-8") == before
    assert [pivot.label for pivot in report.pivots] == ["Updated"]
    assert any(
        finding.entry == str(operator)
        and finding.reason == "identity-mismatch"
        for finding in report.findings
    )


def test_operator_identity_wins_over_append_only_managed_sibling(tmp_path):
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
        registry,
        activation_report=_active_report(source, root),
    )

    assert len(report.contributions) == 1
    assert report.contributions[0].entry == operator
    assert report.contributions[0].entry_class == "operator"
    managed_duplicate = next(
        finding
        for finding in report.findings
        if finding.reason == "duplicate"
    )
    assert managed_duplicate.entry != str(operator)


def test_absent_entry_publication_race_never_overwrites_operator(
    tmp_path, monkeypatch
):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"
    operator_command = _command(tmp_path / "operator")
    target = registry / "sample.json"
    original_link = os.link
    raced = {"done": False}

    def race_link(source_path, destination_path):
        if Path(destination_path) == target and not raced["done"]:
            raced["done"] = True
            target.write_text(
                json.dumps(
                    {"label": "Operator", "list": [str(operator_command)]}
                ),
                encoding="utf-8",
            )
        return original_link(source_path, destination_path)

    monkeypatch.setattr(pivots.os, "link", race_link)
    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )

    assert json.loads(target.read_text(encoding="utf-8"))["label"] == "Operator"
    assert [pivot.label for pivot in report.pivots] == ["Operator"]
    assert report.entry_classes[str(target)] == "operator"


def test_disabled_plugin_is_not_restored_and_withdraws_prior(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"
    first = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )

    disabled = pivots.scan_pivot_registry(
        registry,
        previous=first.active_entries,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert disabled.active_entries == {}
    assert disabled.findings[0].reason == "not-enabled"

    empty_registry = tmp_path / "empty"
    not_restored = pivots.scan_pivot_registry(
        empty_registry,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert not empty_registry.exists()
    assert not_restored.authority is ScanAuthority.ABSENT


def test_missing_command_and_root_mismatch_are_inactive(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _template(root)
    registry = tmp_path / "pivots"
    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )
    assert not report.active_entries
    assert report.findings[0].reason == "missing-target"

    command = _command(root)
    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )
    assert report.active_entries
    active_entry = report.contributions[0].entry
    payload = json.loads(active_entry.read_text(encoding="utf-8"))
    payload["plugin_root"] = str(tmp_path / "other")
    active_entry.write_text(json.dumps(payload), encoding="utf-8")
    mismatch = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=_active_report(source, root),
    )
    assert mismatch.active_entries == {}
    assert mismatch.findings[0].reason == "identity-mismatch"
    assert command.exists()


def test_managed_command_tamper_is_identity_mismatch(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"
    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )
    assert report.active_entries
    entry = registry / "sample.json"
    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["list"][0] = str(_command(tmp_path / "other", "other"))
    entry.write_text(json.dumps(payload), encoding="utf-8")

    tampered = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=_active_report(source, root),
    )
    assert tampered.active_entries == {}
    assert tampered.findings[0].reason == "identity-mismatch"


def test_managed_relative_command_cannot_escape_plugin_root(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    outside = _command(tmp_path / "outside", "tool")
    relative = os.path.relpath(outside, root)
    _template(root, command=relative)
    registry = tmp_path / "pivots"

    report = pivots.scan_pivot_registry(
        registry,
        activation_report=_active_report(source, root),
    )

    assert not report.active_entries
    assert report.findings[0].reason == "target-unusable"
    assert "escapes" in report.findings[0].detail


def test_dot_relative_commands_resolve_in_their_authority_root(
    tmp_path, monkeypatch
):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    root.mkdir()
    command_name = "tool.cmd" if os.name == "nt" else "tool"
    relative_command = f"./{command_name}"
    managed_command = root / command_name
    managed_command.write_text(
        "@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n"
    )
    if os.name != "nt":
        managed_command.chmod(0o755)
    _template(root, command=relative_command)
    managed = pivots.scan_pivot_registry(
        tmp_path / "managed",
        activation_report=_active_report(source, root),
    )
    assert Path(managed.pivots[0].list_cmd[0]) == managed_command.resolve()

    operator_root = tmp_path / "operator"
    operator_root.mkdir()
    operator_command = operator_root / command_name
    operator_command.write_text(
        "@exit /b 0\n" if os.name == "nt" else "#!/bin/sh\nexit 0\n"
    )
    if os.name != "nt":
        operator_command.chmod(0o755)
    registry = operator_root / "pivots"
    registry.mkdir()
    (registry / "operator.json").write_text(
        json.dumps({"label": "Operator", "list": [relative_command]}),
        encoding="utf-8",
    )
    monkeypatch.chdir(operator_root)
    operator = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert Path(operator.pivots[0].list_cmd[0]) == operator_command.resolve()


def test_symlinked_executable_resolves_to_regular_target(tmp_path):
    root = tmp_path / "commands"
    real = _command(root, "real")
    suffix = ".cmd" if os.name == "nt" else ""
    linked = root / "bin" / f"linked{suffix}"
    try:
        linked.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"symlink creation unavailable: {exc}")
    registry = tmp_path / "pivots"
    registry.mkdir()
    (registry / "operator.json").write_text(
        json.dumps({"label": "Operator", "list": [str(linked)]}),
        encoding="utf-8",
    )

    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )

    assert Path(report.pivots[0].list_cmd[0]) == real.resolve()


def test_malformed_operator_peer_does_not_block_valid_operator(tmp_path):
    registry = tmp_path / "pivots"
    registry.mkdir()
    command = _command(tmp_path / "commands")
    valid = registry / "valid.json"
    valid.write_text(
        json.dumps({"label": "Valid", "list": [str(command)]}),
        encoding="utf-8",
    )
    (registry / "bad.json").write_text("{", encoding="utf-8")

    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )

    assert [pivot.label for pivot in report.pivots] == ["Valid"]
    assert report.entry_classes[str(valid)] == "operator"
    assert [finding.reason for finding in report.findings] == ["invalid-entry"]


def test_unattributed_action_target_is_validated(tmp_path):
    registry = tmp_path / "pivots"
    registry.mkdir()
    command = _command(tmp_path / "commands")
    entry = registry / "operator.json"
    entry.write_text(
        json.dumps(
            {
                "label": "Operator",
                "list": [str(command)],
                "actions": [
                    {"label": "Broken", "run": ["definitely-missing-command"]}
                ],
            }
        ),
        encoding="utf-8",
    )

    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )

    assert report.active_entries == {}
    assert report.findings[0].reason == "missing-target"


def test_known_legacy_is_activation_gated_and_advisory(tmp_path):
    source = "agent-bridge@copilot-extensions"
    root = tmp_path / "agent-bridge"
    _command(root, "agent-bridge")
    template = _template(
        root,
        filename="agent-bridge.json",
        label="Bridges",
        command="agent-bridge",
    )
    registry = tmp_path / "pivots"
    registry.mkdir()
    entry = registry / template.name
    entry.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")

    active = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=_active_report(source, root),
    )
    assert [pivot.label for pivot in active.pivots] == ["Bridges"]
    assert active.findings[0].reason == "legacy-unattributed"
    assert active.entry_classes[str(entry)] == "legacy-plugin"

    disabled = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        previous=active.active_entries,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert disabled.active_entries == {}
    assert disabled.findings[0].reason == "not-enabled"


def test_unknown_legacy_remains_advisory_and_report_only(tmp_path):
    registry = tmp_path / "pivots"
    registry.mkdir()
    command = _command(tmp_path / "commands")
    entry = registry / "third-party.json"
    entry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "label": "Third Party",
                "list": [str(command)],
            }
        ),
        encoding="utf-8",
    )

    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert [pivot.label for pivot in report.pivots] == ["Third Party"]
    assert report.entry_classes[str(entry)] == "unknown-legacy"
    assert report.findings[0].reason == "legacy-unattributed"
    assert "will not remove" in report.findings[0].remedy


def test_absence_withdraws_and_registry_uncertainty_retains(tmp_path, monkeypatch):
    registry = tmp_path / "pivots"
    registry.mkdir()
    command = _command(tmp_path / "commands")
    entry = registry / "operator.json"
    entry.write_text(
        json.dumps({"label": "Operator", "list": [str(command)]}),
        encoding="utf-8",
    )
    activation = ActivationReport(ScanAuthority.COMPLETE, {})
    first = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=activation,
    )
    original_iterdir = Path.iterdir

    def unreadable(path: Path):
        if path == registry:
            raise OSError("permission denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    uncertain = pivots.scan_pivot_registry(
        registry,
        previous=first.active_entries,
        materialize=False,
        activation_report=activation,
    )
    assert uncertain.authority is ScanAuthority.INDETERMINATE
    assert uncertain.active_entries == first.active_entries
    monkeypatch.undo()

    entry.unlink()
    absent_entry = pivots.scan_pivot_registry(
        registry,
        previous=first.active_entries,
        materialize=False,
        activation_report=activation,
    )
    assert absent_entry.authority is ScanAuthority.COMPLETE
    assert absent_entry.active_entries == {}

    absent_registry = pivots.scan_pivot_registry(
        tmp_path / "absent",
        previous=first.active_entries,
        materialize=False,
        activation_report=activation,
    )
    assert absent_registry.authority is ScanAuthority.ABSENT
    assert absent_registry.active_entries == {}


def test_activation_uncertainty_retains_prior_but_not_fresh(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    registry = tmp_path / "pivots"
    active = _active_report(source, root)
    first = pivots.scan_pivot_registry(registry, activation_report=active)

    uncertain = ActivationReport(
        authority=ScanAuthority.INDETERMINATE,
        decisions=active.decisions,
    )
    retained = pivots.scan_pivot_registry(
        registry,
        previous=first.active_entries,
        materialize=False,
        activation_report=uncertain,
    )
    assert retained.active_entries == first.active_entries
    assert retained.findings[0].reason == "entry-indeterminate"

    fresh_registry = tmp_path / "fresh"
    fresh = pivots.scan_pivot_registry(
        fresh_registry,
        previous={},
        activation_report=uncertain,
    )
    assert not fresh_registry.exists()
    assert fresh.active_entries == {}


def test_duplicate_identity_isolated_and_activation_ambiguity_propagates(tmp_path):
    registry = tmp_path / "pivots"
    registry.mkdir()
    command = _command(tmp_path / "commands")
    for name in ("a", "b"):
        (registry / f"{name}.json").write_text(
            json.dumps({"label": "Same", "list": [str(command)]}),
            encoding="utf-8",
        )
    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert [pivot.label for pivot in report.pivots] == ["Same"]
    assert report.snapshot.decisions[str(registry / "b.json")].status is EntryStatus.INACTIVE
    assert report.findings[0].reason == "duplicate"

    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    _command(root)
    _template(root)
    managed_registry = tmp_path / "managed"
    first = pivots.scan_pivot_registry(
        managed_registry,
        activation_report=_active_report(source, root),
    )
    ambiguous = ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={
            source: EntryDecision.inactive(
                Finding(
                    registry="plugin-activation",
                    entry="settings",
                    status="inactive",
                    reason="root-ambiguous",
                    owner=source,
                )
            )
        },
    )
    withdrawn = pivots.scan_pivot_registry(
        managed_registry,
        previous=first.active_entries,
        materialize=False,
        activation_report=ambiguous,
    )
    assert withdrawn.active_entries == {}
    assert withdrawn.findings[0].reason == "root-ambiguous"


def test_warning_cap_dedup_and_exhaustive_json(tmp_path, monkeypatch, caplog):
    registry = tmp_path / "pivots"
    registry.mkdir()
    for index in range(3):
        (registry / f"{index}.json").write_text("{", encoding="utf-8")
    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    monkeypatch.setattr(
        pivots,
        "_WARNING_TRACKER",
        WarningTracker(limit=1, repeat_after_seconds=3600),
    )

    with caplog.at_level(logging.WARNING, logger="agent-worktrees"):
        pivots.warn_pivot_findings(report)
    assert sum("reason=" in record.message for record in caplog.records) == 1
    assert any(
        "additional findings suppressed" in record.message
        for record in caplog.records
    )
    caplog.clear()
    pivots.warn_pivot_findings(report)
    assert not caplog.records

    payload = report.to_dict()
    assert payload["authority"] == "complete"
    assert len(payload["findings"]) == 3
    assert all(finding["remedy"] for finding in payload["findings"])


def test_doctor_human_render_matches_exhaustive_json(tmp_path, capsys):
    from agent_worktrees import __main__ as main

    registry = tmp_path / "pivots"
    registry.mkdir()
    (registry / "bad.json").write_text("{", encoding="utf-8")
    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    payload = report.to_dict()

    main._render_dropin_registry_report("Picker pivots", payload)
    rendered = capsys.readouterr().out
    for finding in payload["findings"]:
        assert finding["entry"] in rendered
        assert finding["reason"] in rendered
        assert finding["remedy"] in rendered


def test_scanner_generated_entry_finding_gets_exact_remedy(tmp_path):
    registry = tmp_path / "pivots"
    registry.mkdir()
    directory_entry = registry / "not-a-file.json"
    directory_entry.mkdir()

    report = pivots.scan_pivot_registry(
        registry,
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )

    assert report.findings[0].entry == str(directory_entry)
    assert report.findings[0].reason == "invalid-entry"
    assert str(directory_entry) in report.findings[0].remedy
