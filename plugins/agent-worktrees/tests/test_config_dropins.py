"""Tests for per-project ``config.d`` hygiene."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from dropin_registry import (
    EntryDecision,
    EntryStatus,
    Finding,
    ScanAuthority,
    WarningTracker,
)
from plugin_activation import ActivationReport, ActivePlugin, ActivePluginRoot

from agent_worktrees import config_dropins as dropins


def test_session_backend_timeout_rejects_boolean():
    assert dropins._validate_config({
        "session_backend": {
            "kind": "ahp",
            "endpoint_url": "ws://127.0.0.1:8765",
            "connect_timeout_seconds": True,
        }
    }) == "session_backend.connect_timeout_seconds must be a number"


def _active_report(
    source: str,
    root: Path,
    *,
    scopes: tuple[str, ...] = ("global",),
) -> ActivationReport:
    active = ActivePlugin(
        source=source,
        name=source.split("@", 1)[0],
        marketplace=source.split("@", 1)[1],
        root=root.resolve(),
        scopes=scopes,
    )
    return ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={source: EntryDecision.active(active)},
    )


def _target(root: Path, body: str = "repos:\n  sample:\n    remote: origin\n") -> Path:
    target = root / "config" / "fragment.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body, encoding="utf-8")
    return target


def _pointer(
    directory: Path,
    source: str,
    root: Path,
    target: Path,
    *,
    name: str = "managed.json",
) -> Path:
    entry = directory / name
    entry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": source,
                "plugin_root": str(root.resolve()),
                "target": str(target.resolve()),
            }
        ),
        encoding="utf-8",
    )
    return entry


def test_operator_yaml_fault_isolated_from_valid_peer(tmp_path):
    directory = tmp_path / "config.d"
    directory.mkdir()
    valid = directory / "valid.yaml"
    valid.write_text("repos:\n  sample:\n    remote: valid\n", encoding="utf-8")
    (directory / "bad.yaml").write_text("repos: [", encoding="utf-8")

    report = dropins.scan_config_dropin_registry(directory)

    assert [item.entry for item in report.active_configs] == [valid.resolve()]
    assert report.entry_classes[str(valid)] == "operator"
    assert [finding.reason for finding in report.findings] == ["invalid-entry"]
    assert report.findings[0].remedy


def test_pr_required_body_sections_rejects_non_string_shape():
    error = dropins._validate_pr(
        {"required_body_sections": 7},
        location="repos.sample.pr",
    )

    assert error == "repos.sample.pr.required_body_sections must be a list"


def test_managed_pointer_requires_current_project_scope_and_root(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    target = _target(root)
    directory = tmp_path / "config.d"
    directory.mkdir()
    entry = _pointer(directory, source, root, target)

    report = dropins.scan_config_dropin_registry(
        directory,
        project_name="sample",
        activation_report=_active_report(
            source, root, scopes=("project:sample",)
        ),
    )
    assert [item.entry for item in report.active_configs] == [entry]
    assert report.active_configs[0].entry_class == "managed-plugin"

    wrong_project = dropins.scan_config_dropin_registry(
        directory,
        project_name="other",
        activation_report=_active_report(
            source, root, scopes=("project:sample",)
        ),
    )
    assert not wrong_project.active_configs
    assert wrong_project.findings[0].reason == "not-enabled"

    payload = json.loads(entry.read_text(encoding="utf-8"))
    payload["plugin_root"] = str(tmp_path / "stale-root")
    entry.write_text(json.dumps(payload), encoding="utf-8")
    mismatch = dropins.scan_config_dropin_registry(
        directory,
        project_name="sample",
        activation_report=_active_report(source, root),
    )
    assert not mismatch.active_configs
    assert mismatch.findings[0].reason == "identity-mismatch"


def test_managed_pointer_uses_root_selected_for_project_scope(tmp_path):
    source = "sample@example-marketplace"
    installed = tmp_path / "installed"
    installed.mkdir()
    local = tmp_path / "local"
    target = _target(local)
    directory = tmp_path / "config.d"
    directory.mkdir()
    entry = _pointer(directory, source, local, target)
    active = ActivePlugin(
        source=source,
        name="sample",
        marketplace="example-marketplace",
        root=local.resolve(),
        scopes=("global", "project:sample"),
        roots=(
            ActivePluginRoot(local.resolve(), ("project:sample",), "directory"),
            ActivePluginRoot(installed.resolve(), ("global",), "installed"),
        ),
    )

    report = dropins.scan_config_dropin_registry(
        directory,
        project_name="sample",
        activation_report=ActivationReport(
            ScanAuthority.COMPLETE,
            {source: EntryDecision.active(active)},
        ),
    )

    assert [item.entry for item in report.active_configs] == [entry]


def test_managed_target_escape_and_invalid_shape_are_isolated(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    root.mkdir()
    escaped = _target(tmp_path / "outside")
    invalid = _target(root / "invalid", "repos:\n  sample:\n    session_env: []\n")
    valid = _target(root / "valid")
    directory = tmp_path / "config.d"
    directory.mkdir()
    _pointer(directory, source, root, escaped, name="a-escape.json")
    _pointer(directory, source, root, invalid, name="b-invalid.json")
    good = _pointer(directory, source, root, valid, name="c-valid.json")

    report = dropins.scan_config_dropin_registry(
        directory,
        activation_report=_active_report(source, root),
    )

    assert [item.entry for item in report.active_configs] == [good]
    assert {
        finding.reason for finding in report.findings
    } == {"identity-mismatch", "invalid-entry"}


def test_operator_filename_and_provenance_do_not_grant_managed_ownership(tmp_path):
    directory = tmp_path / "config.d"
    directory.mkdir()
    operator = directory / "agent-example.yaml"
    operator.write_text(
        "plugin: sample@example-marketplace\n"
        "plugin_root: /not/authority\n"
        "repos:\n  sample:\n    remote: origin\n",
        encoding="utf-8",
    )

    report = dropins.scan_config_dropin_registry(directory)

    assert [item.entry for item in report.active_configs] == [operator]
    assert report.active_configs[0].entry_class == "operator"
    assert report.active_configs[0].owner is None


def test_profile_and_project_identity_shapes_are_fault_isolated(tmp_path):
    directory = tmp_path / "config.d"
    directory.mkdir()
    (directory / "bad-profile.yaml").write_text(
        "copilot_profiles:\n  - name: [not, hashable]\n",
        encoding="utf-8",
    )
    (directory / "bad-project.yaml").write_text(
        "repo_name: [not, a, string]\n",
        encoding="utf-8",
    )
    valid = directory / "valid.yaml"
    valid.write_text(
        "copilot_profiles:\n  - name: cloud\n    label: Cloud\n",
        encoding="utf-8",
    )

    report = dropins.scan_config_dropin_registry(directory)

    assert [item.entry for item in report.active_configs] == [valid]
    assert [finding.reason for finding in report.findings] == [
        "invalid-entry",
        "invalid-entry",
    ]


def test_invalid_anchor_fragment_does_not_abort_load_config(tmp_path, monkeypatch):
    from agent_worktrees import config as cfg

    anchor = tmp_path / "repo"
    anchor.mkdir()
    machine = tmp_path / "config.yaml"
    machine.write_text(
        "repo_name: sample\n"
        "repos:\n"
        "  sample:\n"
        f"    anchor: {anchor}\n",
        encoding="utf-8",
    )
    directory = tmp_path / "config.d"
    directory.mkdir()
    (directory / "bad.yaml").write_text(
        "repos:\n  sample:\n    anchor: [bad]\n",
        encoding="utf-8",
    )
    (directory / "valid.yaml").write_text(
        "repos:\n  sample:\n    remote: upstream\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cfg, "global_config_path", lambda: tmp_path / "missing-global.yaml"
    )

    loaded = cfg.load_config(machine)

    assert loaded.default_repo.anchor == str(anchor)
    assert loaded.default_repo.remote == "upstream"


def test_invalid_nested_pr_types_do_not_change_behavior(tmp_path, monkeypatch):
    from agent_worktrees import config as cfg

    anchor = tmp_path / "repo"
    anchor.mkdir()
    machine = tmp_path / "config.yaml"
    machine.write_text(
        "repo_name: sample\n"
        "repos:\n"
        "  sample:\n"
        f"    anchor: {anchor}\n",
        encoding="utf-8",
    )
    directory = tmp_path / "config.d"
    directory.mkdir()
    (directory / "bad.yaml").write_text(
        "repos:\n  sample:\n    pr:\n      enabled: 'false'\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        cfg, "global_config_path", lambda: tmp_path / "missing-global.yaml"
    )

    loaded = cfg.load_config(machine)
    report = dropins.scan_config_dropin_registry(
        directory, project_name="sample"
    )

    assert loaded.default_repo.pr.enabled is False
    assert report.active_entries == {}
    assert report.findings[0].reason == "invalid-entry"
    assert "enabled must be a boolean" in report.findings[0].detail


def test_disabled_or_uninstalled_managed_plugin_withdraws_prior(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    target = _target(root)
    directory = tmp_path / "config.d"
    directory.mkdir()
    _pointer(directory, source, root, target)
    active = dropins.scan_config_dropin_registry(
        directory,
        activation_report=_active_report(source, root),
    )

    disabled = dropins.scan_config_dropin_registry(
        directory,
        previous=active.active_entries,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    assert disabled.active_entries == {}
    assert disabled.findings[0].reason == "not-enabled"

    unavailable = ActivationReport(
        authority=ScanAuthority.COMPLETE,
        decisions={
            source: EntryDecision.inactive(
                Finding(
                    registry="plugin-activation",
                    entry="installed-plugin",
                    status="inactive",
                    reason="missing-target",
                    owner=source,
                )
            )
        },
    )
    uninstalled = dropins.scan_config_dropin_registry(
        directory,
        previous=active.active_entries,
        activation_report=unavailable,
    )
    assert uninstalled.active_entries == {}
    assert uninstalled.findings[0].reason == "missing-target"


def test_absence_withdraws_and_registry_uncertainty_retains(tmp_path, monkeypatch):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    target = _target(root)
    directory = tmp_path / "config.d"
    directory.mkdir()
    _pointer(directory, source, root, target)
    activation = _active_report(source, root)
    first = dropins.scan_config_dropin_registry(
        directory, activation_report=activation
    )

    original_iterdir = Path.iterdir

    def unreadable(path: Path):
        if path == directory:
            raise OSError("permission denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", unreadable)
    uncertain = dropins.scan_config_dropin_registry(
        directory,
        previous=first.active_entries,
        activation_report=activation,
    )
    assert uncertain.authority is ScanAuthority.INDETERMINATE
    assert uncertain.active_entries == first.active_entries
    assert uncertain.findings[0].reason == "registry-indeterminate"
    monkeypatch.undo()

    absent = dropins.scan_config_dropin_registry(
        tmp_path / "absent",
        previous=first.active_entries,
        activation_report=activation,
    )
    assert absent.authority is ScanAuthority.ABSENT
    assert absent.active_entries == {}


def test_entry_uncertainty_retains_only_prior_and_not_fresh(tmp_path, monkeypatch):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    target = _target(root)
    directory = tmp_path / "config.d"
    directory.mkdir()
    old = _pointer(directory, source, root, target, name="old.json")
    activation = _active_report(source, root)
    first = dropins.scan_config_dropin_registry(
        directory, activation_report=activation
    )
    fresh = _pointer(
        directory,
        source,
        root,
        _target(root / "fresh"),
        name="fresh.json",
    )
    original_read = Path.read_text

    def intermittent(path: Path, *args, **kwargs):
        if path in {old, fresh}:
            raise OSError("sharing violation")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", intermittent)
    retained = dropins.scan_config_dropin_registry(
        directory,
        previous=first.active_entries,
        activation_report=activation,
    )
    assert list(retained.active_entries) == [str(old)]
    assert all(
        decision.status is EntryStatus.INDETERMINATE
        for decision in retained.snapshot.decisions.values()
    )


def test_duplicate_target_loser_is_inactive(tmp_path):
    source = "sample@example-marketplace"
    root = tmp_path / "plugin"
    target = _target(root)
    directory = tmp_path / "config.d"
    directory.mkdir()
    first = _pointer(directory, source, root, target, name="a.json")
    second = _pointer(directory, source, root, target, name="b.json")

    report = dropins.scan_config_dropin_registry(
        directory,
        activation_report=_active_report(source, root),
    )

    assert [item.entry for item in report.active_configs] == [first]
    assert report.snapshot.decisions[str(second)].status is EntryStatus.INACTIVE
    assert [finding.reason for finding in report.findings] == ["duplicate"]


def test_warning_cap_dedup_and_report_json(tmp_path, monkeypatch, caplog):
    directory = tmp_path / "config.d"
    directory.mkdir()
    for index in range(3):
        (directory / f"{index}.yaml").write_text("repos: [", encoding="utf-8")
    report = dropins.scan_config_dropin_registry(directory)
    monkeypatch.setattr(
        dropins,
        "_WARNING_TRACKER",
        WarningTracker(limit=1, repeat_after_seconds=3600),
    )

    with caplog.at_level(logging.WARNING, logger="agent-worktrees"):
        dropins.warn_config_dropin_findings(report)
    assert sum("reason=" in record.message for record in caplog.records) == 1
    assert any(
        "additional findings suppressed" in record.message
        for record in caplog.records
    )
    caplog.clear()
    dropins.warn_config_dropin_findings(report)
    assert not caplog.records

    payload = report.to_dict()
    assert payload["authority"] == "complete"
    assert len(payload["findings"]) == 3
    assert all(finding["remedy"] for finding in payload["findings"])


def test_doctor_human_render_matches_exhaustive_json(tmp_path, capsys):
    from agent_worktrees import __main__ as main

    directory = tmp_path / "config.d"
    directory.mkdir()
    (directory / "bad.yaml").write_text("repos: [", encoding="utf-8")
    report = dropins.scan_config_dropin_registry(directory)
    payload = report.to_dict()

    main._render_dropin_registry_report("Project config.d", payload)
    rendered = capsys.readouterr().out
    for finding in payload["findings"]:
        assert finding["entry"] in rendered
        assert finding["reason"] in rendered
        assert finding["remedy"] in rendered


def test_scanner_generated_entry_finding_and_no_project_report(tmp_path):
    directory = tmp_path / "config.d"
    directory.mkdir()
    invalid = directory / "not-a-file.yaml"
    invalid.mkdir()

    report = dropins.scan_config_dropin_registry(directory)
    assert report.findings[0].entry == str(invalid)
    assert report.findings[0].reason == "invalid-entry"
    assert str(invalid) in report.findings[0].remedy

    unresolved = dropins.empty_config_dropin_report()
    assert unresolved.authority is ScanAuthority.ABSENT
    assert unresolved.active_entries == {}


def test_doctor_json_runs_without_project_context(tmp_path, monkeypatch, capfd):
    from agent_worktrees import __main__ as main
    from agent_worktrees.picker_tui import pivots

    pivot_report = pivots.scan_pivot_registry(
        tmp_path / "absent-pivots",
        materialize=False,
        activation_report=ActivationReport(ScanAuthority.COMPLETE, {}),
    )
    monkeypatch.setattr(
        main.cfg,
        "project_name",
        lambda: (_ for _ in ()).throw(RuntimeError("no project")),
    )
    monkeypatch.setattr(main, "_find_repo_dir", lambda: None)
    monkeypatch.setattr(main.reclaim, "find_bare_orphans", lambda: [])
    monkeypatch.setattr(
        pivots, "scan_pivot_registry", lambda **_: pivot_report
    )

    assert main._is_no_project_invocation(["doctor", "--json"])
    assert main.main(["doctor", "--json"]) == 0
    payload = json.loads(capfd.readouterr().out)
    assert payload["project"] == ""
    assert payload["project_health_available"] is False
    assert payload["config_d"]["authority"] == "absent"
    assert payload["pivots"]["authority"] == "absent"
