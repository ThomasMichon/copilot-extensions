"""Tests for identity-gated plugin contributions from registrar.d."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from dropin_registry import EntryDecision, Finding, ScanAuthority, WarningTracker
from plugin_activation import ActivationReport, ActivePlugin, ActivePluginRoot

from agent_dispatch.__main__ import main
from agent_dispatch.registrar import load_declaration
from agent_dispatch.registrar_discovery import (
    REGISTRAR_DIR_ENV,
    RegistrarSources,
    add_pointer,
    discover_trusted,
)
from agent_dispatch.registrar_registry import (
    REGISTRAR_DROPINS_DIR_ENV,
    ManifestError,
    combine_registrar_sources,
    parse_manifest,
    scan_registrar_registry,
)

SOURCE = "example-producer@example-marketplace"


def _activation(
    roots: dict[str, Path] | None = None,
    *,
    authority: ScanAuthority = ScanAuthority.COMPLETE,
    decisions: dict[str, EntryDecision[ActivePlugin]] | None = None,
) -> ActivationReport:
    if decisions is None:
        decisions = {}
        for source, root in (roots or {}).items():
            name, marketplace = source.split("@", 1)
            decisions[source] = EntryDecision.active(
                ActivePlugin(
                    source=source,
                    name=name,
                    marketplace=marketplace,
                    root=root.resolve(),
                    scopes=("global",),
                )
            )
    return ActivationReport(authority=authority, decisions=decisions)


def _plugin_root(tmp_path: Path, name: str = "plugin") -> Path:
    root = tmp_path / name
    root.mkdir()
    (root / "plugin.json").write_text(
        json.dumps({"name": name, "version": "1.2.3"}),
        encoding="utf-8",
    )
    return root


def _write_declaration(
    root: Path,
    name: str,
    *,
    directory: str = "references/agent-dispatch/registrar",
    filename: str | None = None,
) -> Path:
    target = root / Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    path = target / (filename or f"{name}.json")
    path.write_text(json.dumps({"name": name}), encoding="utf-8")
    return path


def _write_companion(root: Path, name: str = "companion") -> Path:
    target = root / "references/agent-dispatch/registrar"
    target.mkdir(parents=True, exist_ok=True)
    path = target / f"{name}.json"
    path.write_text(
        json.dumps(
            {
                "name": name,
                "kind": "plugin-companion",
                "spec": {
                    "command": ["bin/serve"],
                    "stop_command": ["bin/stop"],
                    "health_probe": ["bin/health"],
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def _write_manifest(
    registry: Path,
    root: Path,
    *,
    source: str = SOURCE,
    registrar: str = "references/agent-dispatch/registrar",
    filename: str = "producer.json",
) -> Path:
    registry.mkdir(parents=True, exist_ok=True)
    path = registry / filename
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "plugin": source,
                "plugin_root": str(root),
                "registrar": registrar,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_parse_manifest_requires_attributed_v1_and_root_containment(tmp_path):
    root = _plugin_root(tmp_path)
    manifest = parse_manifest(
        {
            "schema_version": 1,
            "plugin": SOURCE,
            "plugin_root": str(root),
            "registrar": "references/registrar",
        }
    )
    assert manifest.plugin == SOURCE

    with pytest.raises(ManifestError, match="schema_version"):
        parse_manifest(
            {
                "plugin": SOURCE,
                "plugin_root": str(root),
                "registrar": "references/registrar",
            }
        )
    with pytest.raises(ManifestError, match="plugin"):
        parse_manifest(
            {
                "schema_version": 1,
                "plugin_root": str(root),
                "registrar": "references/registrar",
            }
        )
    for escaped in ("../outside", "/outside", r"C:\outside"):
        with pytest.raises(ManifestError, match="root-contained"):
            parse_manifest(
                {
                    "schema_version": 1,
                    "plugin": SOURCE,
                    "plugin_root": str(root),
                    "registrar": escaped,
                }
            )


def test_malformed_manifest_does_not_block_valid_peer(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "valid")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root, filename="valid.json")
    (registry / "broken.json").write_text("{not json", encoding="utf-8")

    report = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    assert [entry.declaration.name for entry in report.declarations] == ["valid"]
    assert any(finding.reason == "invalid-entry" for finding in report.findings)


def test_registrar_accepts_any_authoritative_live_root(tmp_path):
    installed = _plugin_root(tmp_path, "installed")
    local = _plugin_root(tmp_path, "local")
    _write_declaration(installed, "valid")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, installed)
    active = ActivePlugin(
        source=SOURCE,
        name=SOURCE.split("@", 1)[0],
        marketplace=SOURCE.split("@", 1)[1],
        root=local.resolve(),
        scopes=("global", "project:demo"),
        roots=(
            ActivePluginRoot(local.resolve(), ("project:demo",), "directory"),
            ActivePluginRoot(installed.resolve(), ("global",), "installed"),
        ),
    )

    report = scan_registrar_registry(
        registry,
        activation_report=ActivationReport(
            ScanAuthority.COMPLETE,
            {SOURCE: EntryDecision.active(active)},
        ),
    )

    assert [entry.declaration.name for entry in report.declarations] == ["valid"]


def test_attributed_plugin_companion_carries_authoritative_provenance(tmp_path):
    root = _plugin_root(tmp_path)
    declaration_path = _write_companion(root)
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)

    report = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    declaration = report.declarations[0].declaration
    assert declaration.kind == "plugin-companion"
    assert declaration.owner == SOURCE
    assert declaration.plugin_root == str(root.resolve())
    assert declaration.source_path == str(declaration_path.resolve())
    assert declaration.plugin_version == "1.2.3"
    assert declaration.activation_scopes == ("global",)


def test_plugin_companion_activation_uncertainty_retains_prior_only(tmp_path):
    root = _plugin_root(tmp_path)
    _write_companion(root)
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    retained = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(authority=ScanAuthority.INDETERMINATE),
    )
    fresh = scan_registrar_registry(
        registry,
        activation_report=_activation(authority=ScanAuthority.INDETERMINATE),
    )

    assert retained.declarations == first.declarations
    assert fresh.declarations == ()


def test_disabled_plugin_withdraws_previous_declarations(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)

    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(),
    )

    assert [entry.declaration.name for entry in first.declarations] == ["general"]
    assert second.declarations == ()
    assert second.entries == {}
    assert second.findings[0].reason == "not-enabled"


def test_confirmed_manifest_deletion_withdraws_previous_declarations(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    manifest = _write_manifest(registry, root)
    active = _activation({SOURCE: root})
    first = scan_registrar_registry(registry, activation_report=active)

    manifest.unlink()
    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=active,
    )

    assert second.snapshot.authority is ScanAuthority.COMPLETE
    assert second.entries == {}
    assert second.declarations == ()


def test_absent_registry_is_authoritative_empty(tmp_path):
    report = scan_registrar_registry(
        tmp_path / "absent",
        previous={"old": object()},
        activation_report=_activation(),
    )
    assert report.snapshot.authority is ScanAuthority.ABSENT
    assert report.entries == {}


def test_indeterminate_registry_retains_previous_entries(monkeypatch, tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    active = _activation({SOURCE: root})
    first = scan_registrar_registry(registry, activation_report=active)
    original_iterdir = Path.iterdir

    def deny_registry(path):
        if path == registry:
            raise PermissionError("temporarily denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_registry)
    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=active,
    )

    assert second.snapshot.authority is ScanAuthority.INDETERMINATE
    assert second.declarations == first.declarations
    assert second.findings[0].reason == "registry-indeterminate"


def test_indeterminate_manifest_retains_prior_but_never_activates_fresh(
    monkeypatch,
    tmp_path,
):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    manifest = _write_manifest(registry, root)
    active = _activation({SOURCE: root})
    first = scan_registrar_registry(registry, activation_report=active)
    original_read_text = Path.read_text

    def deny_manifest(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_manifest)
    retained = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=active,
    )
    fresh = scan_registrar_registry(registry, activation_report=active)

    assert retained.declarations == first.declarations
    assert fresh.declarations == ()
    assert fresh.findings[0].reason == "entry-indeterminate"


def test_confirmed_disablement_withdraws_unreadable_manifest(
    monkeypatch,
    tmp_path,
):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    manifest = _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    original_read_text = Path.read_text

    def deny_manifest(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_manifest)
    disabled = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(),
    )

    assert disabled.entries == {}
    assert disabled.declarations == ()
    assert {finding.reason for finding in disabled.findings} == {
        "entry-indeterminate",
        "not-enabled",
    }


def test_confirmed_disablement_withdraws_indeterminate_registry(
    monkeypatch,
    tmp_path,
):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    original_iterdir = Path.iterdir

    def deny_registry(path):
        if path == registry:
            raise PermissionError("temporarily denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_registry)
    disabled = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(),
    )

    assert disabled.entries == {}
    assert disabled.declarations == ()
    assert {finding.reason for finding in disabled.findings} == {
        "registry-indeterminate",
        "not-enabled",
    }


def test_current_root_change_withdraws_unreadable_manifest(
    monkeypatch,
    tmp_path,
):
    old_root = _plugin_root(tmp_path, "old")
    new_root = _plugin_root(tmp_path, "new")
    _write_declaration(old_root, "general")
    registry = tmp_path / "registrar.d"
    manifest = _write_manifest(registry, old_root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: old_root}),
    )
    original_read_text = Path.read_text

    def deny_manifest(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_manifest)
    moved = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation({SOURCE: new_root}),
    )

    assert moved.entries == {}
    assert moved.declarations == ()
    assert any(
        finding.reason == "identity-mismatch"
        for finding in moved.findings
    )


def test_indeterminate_declaration_retains_only_that_document(
    monkeypatch,
    tmp_path,
):
    root = _plugin_root(tmp_path)
    declaration = _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    active = _activation({SOURCE: root})
    first = scan_registrar_registry(registry, activation_report=active)
    original_read_text = Path.read_text

    def deny_declaration(path, *args, **kwargs):
        if path == declaration:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_declaration)
    retained = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=active,
    )
    fresh = scan_registrar_registry(registry, activation_report=active)

    assert retained.declarations == first.declarations
    assert fresh.declarations == ()
    assert fresh.findings[0].reason == "entry-indeterminate"


def test_indeterminate_activation_retains_prior_but_never_activates_fresh(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    uncertain = _activation(authority=ScanAuthority.INDETERMINATE)

    retained = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=uncertain,
    )
    fresh = scan_registrar_registry(
        registry,
        activation_report=uncertain,
    )

    assert retained.declarations == first.declarations
    assert fresh.declarations == ()
    assert fresh.findings[0].reason == "entry-indeterminate"


def test_activation_uncertainty_retains_only_unchanged_documents(tmp_path):
    root = _plugin_root(tmp_path)
    kept = _write_declaration(root, "kept")
    removed = _write_declaration(root, "removed")
    changed = _write_declaration(root, "changed")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    removed.unlink()
    changed.write_text(json.dumps({"name": "changed-now"}), encoding="utf-8")
    fresh = _write_declaration(root, "fresh")
    uncertain = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(authority=ScanAuthority.INDETERMINATE),
    )

    assert kept.exists()
    assert fresh.exists()
    assert [entry.declaration.name for entry in uncertain.declarations] == ["kept"]
    assert uncertain.findings[0].reason == "entry-indeterminate"


@pytest.mark.parametrize("change", ["plugin", "root", "registrar"])
def test_activation_uncertainty_never_retains_changed_manifest_identity(
    tmp_path,
    change,
):
    root = _plugin_root(tmp_path, "original")
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    manifest = _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    source = SOURCE
    current_root = root
    registrar = "references/agent-dispatch/registrar"
    if change == "plugin":
        source = "replacement@example-marketplace"
    elif change == "root":
        current_root = _plugin_root(tmp_path, "replacement")
        _write_declaration(current_root, "general")
    else:
        registrar = "references/alternate"
        _write_declaration(root, "general", directory=registrar)
    _write_manifest(
        registry,
        current_root,
        source=source,
        registrar=registrar,
    )
    assert manifest.exists()

    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(authority=ScanAuthority.INDETERMINATE),
    )

    assert second.declarations == ()
    assert second.findings[0].reason == "entry-indeterminate"


def test_activation_uncertainty_does_not_retain_deleted_target(tmp_path):
    root = _plugin_root(tmp_path)
    declaration = _write_declaration(root, "general")
    target = declaration.parent
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    declaration.unlink()
    target.rmdir()
    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(authority=ScanAuthority.INDETERMINATE),
    )

    assert second.entries == {}
    assert second.declarations == ()
    assert second.findings[0].reason == "missing-target"


def test_confirmed_disablement_beats_registrar_io_uncertainty(
    monkeypatch,
    tmp_path,
):
    root = _plugin_root(tmp_path)
    declaration = _write_declaration(root, "general")
    target = declaration.parent
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    original_iterdir = Path.iterdir

    def deny_target(path):
        if path == target:
            raise PermissionError("temporarily denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_target)
    disabled = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=_activation(),
    )

    assert disabled.entries == {}
    assert disabled.declarations == ()
    assert disabled.findings[0].reason == "not-enabled"


def test_source_indeterminate_retains_prior(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    first = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    activation_finding = Finding(
        registry="plugin-activation",
        entry="settings.json",
        status="indeterminate",
        reason="entry-indeterminate",
        detail="temporarily unreadable",
    )
    uncertain = _activation(
        decisions={SOURCE: EntryDecision.indeterminate(activation_finding)}
    )

    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=uncertain,
    )

    assert second.declarations == first.declarations
    assert second.findings[0].reason == "entry-indeterminate"


def test_root_mismatch_and_missing_target_are_inactive(tmp_path):
    root = _plugin_root(tmp_path, "declared")
    other = _plugin_root(tmp_path, "active")
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)

    mismatch = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: other}),
    )
    assert mismatch.declarations == ()
    assert mismatch.findings[0].reason == "identity-mismatch"

    missing_root = tmp_path / "missing"
    _write_manifest(registry, missing_root)
    missing = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: missing_root}),
    )
    assert missing.declarations == ()
    assert missing.findings[0].reason == "missing-target"


def test_missing_and_unusable_registrar_targets_are_inactive(tmp_path):
    root = _plugin_root(tmp_path)
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    active = _activation({SOURCE: root})

    missing = scan_registrar_registry(registry, activation_report=active)
    assert missing.findings[0].reason == "missing-target"

    target = root / "references/agent-dispatch/registrar"
    target.parent.mkdir(parents=True)
    target.write_text("not a directory", encoding="utf-8")
    unusable = scan_registrar_registry(registry, activation_report=active)
    assert unusable.findings[0].reason == "target-unusable"


def test_symlinked_plugin_root_and_escaping_target_are_rejected(tmp_path):
    real_root = _plugin_root(tmp_path, "real")
    _write_declaration(real_root, "general")
    root_link = tmp_path / "root-link"
    try:
        root_link.symlink_to(real_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root_link)
    root_report = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: real_root}),
    )
    assert root_report.findings[0].reason == "target-unusable"

    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "general.json").write_text(
        json.dumps({"name": "general"}),
        encoding="utf-8",
    )
    registrar_link = real_root / "linked-registrar"
    registrar_link.symlink_to(outside, target_is_directory=True)
    _write_manifest(
        registry,
        real_root,
        registrar="linked-registrar",
    )
    target_report = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: real_root}),
    )
    assert target_report.findings[0].reason == "identity-mismatch"


def test_plugin_declaration_owner_must_match_manifest_source(tmp_path):
    root = _plugin_root(tmp_path)
    target = root / "references/agent-dispatch/registrar"
    target.mkdir(parents=True)
    declaration = target / "general.json"
    declaration.write_text(
        json.dumps({"name": "general", "owner": "spoofed@example-marketplace"}),
        encoding="utf-8",
    )
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)

    report = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )

    assert report.declarations == ()
    assert report.findings[0].reason == "identity-mismatch"
    assert report.findings[0].entry == str(declaration)


def test_plugin_duplicate_quarantines_only_conflicting_name(tmp_path):
    source_a = "producer-a@example-marketplace"
    source_b = "producer-b@example-marketplace"
    root_a = _plugin_root(tmp_path, "a")
    root_b = _plugin_root(tmp_path, "b")
    _write_declaration(root_a, "shared")
    _write_declaration(root_a, "only-a")
    _write_declaration(root_b, "shared")
    _write_declaration(root_b, "only-b")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root_a, source=source_a, filename="a.json")
    _write_manifest(registry, root_b, source=source_b, filename="b.json")

    report = scan_registrar_registry(
        registry,
        activation_report=_activation({source_a: root_a, source_b: root_b}),
    )

    assert {entry.declaration.name for entry in report.declarations} == {
        "only-a",
        "only-b",
    }
    duplicates = [
        finding for finding in report.findings if finding.reason == "duplicate"
    ]
    assert len(duplicates) == 2
    assert {finding.target for finding in duplicates} == {"shared"}


def test_duplicate_manifest_source_quarantines_both_candidates(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "current", directory="references/current")
    _write_declaration(root, "obsolete", directory="references/obsolete")
    other_source = "other@example-marketplace"
    other_root = _plugin_root(tmp_path, "other")
    _write_declaration(other_root, "unrelated")
    registry = tmp_path / "registrar.d"
    _write_manifest(
        registry,
        root,
        registrar="references/current",
        filename="current.json",
    )
    _write_manifest(
        registry,
        root,
        registrar="references/obsolete",
        filename="obsolete.json",
    )
    _write_manifest(
        registry,
        other_root,
        source=other_source,
        filename="other.json",
    )

    report = scan_registrar_registry(
        registry,
        activation_report=_activation(
            {SOURCE: root, other_source: other_root}
        ),
    )

    assert [entry.declaration.name for entry in report.declarations] == [
        "unrelated"
    ]
    duplicates = [
        finding for finding in report.findings
        if finding.reason == "duplicate" and finding.target == SOURCE
    ]
    assert len(duplicates) == 2


def test_trusted_declaration_wins_plugin_name_with_separate_finding(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "shared")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    plugins = scan_registrar_registry(
        registry,
        activation_report=_activation({SOURCE: root}),
    )
    trusted = load_declaration({"name": "shared", "owner": "operator"})

    combined = combine_registrar_sources([trusted], plugins)

    assert combined.declarations == (trusted,)
    assert combined.findings[-1].reason == "duplicate"
    assert combined.findings[-1].target == "shared"


def test_trusted_pointer_error_does_not_freeze_plugin_deactivation(tmp_path):
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    (trusted_dir / "trusted.json").write_text(
        json.dumps({"name": "trusted"}),
        encoding="utf-8",
    )
    pointer_base = tmp_path / "registrar"
    add_pointer("trusted", trusted_dir, base=pointer_base)

    root = _plugin_root(tmp_path)
    _write_declaration(root, "plugin")
    dropins = tmp_path / "registrar.d"
    _write_manifest(dropins, root)
    current = [_activation({SOURCE: root})]
    sources = RegistrarSources(
        base=pointer_base,
        dropins=dropins,
        activation_source=lambda: current[0],
    )
    first = sources.refresh(emit_warnings=False)
    assert {declaration.name for declaration in first.combined.declarations} == {
        "plugin",
        "trusted",
    }

    (pointer_base / "pointers.json").write_text("{not json", encoding="utf-8")
    current[0] = _activation()
    second = sources.refresh(emit_warnings=False)

    assert second.trusted_authority is ScanAuthority.INDETERMINATE
    assert second.trusted_error is not None
    assert [declaration.name for declaration in second.combined.declarations] == [
        "trusted"
    ]
    assert second.combined.plugins.findings[0].reason == "not-enabled"


def test_trusted_pointer_read_failure_retains_last_known_set(monkeypatch, tmp_path):
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    (trusted_dir / "trusted.json").write_text(
        json.dumps({"name": "trusted"}),
        encoding="utf-8",
    )
    pointer_base = tmp_path / "registrar"
    add_pointer("trusted", trusted_dir, base=pointer_base)
    sources = RegistrarSources(
        base=pointer_base,
        dropins=tmp_path / "registrar.d",
        activation_source=_activation,
    )
    first = sources.refresh(emit_warnings=False)
    pointer_file = pointer_base / "pointers.json"
    original_read_text = Path.read_text

    def deny_pointer(path, *args, **kwargs):
        if path == pointer_file:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_pointer)
    second = sources.refresh(emit_warnings=False)

    assert first.trusted_authority is ScanAuthority.COMPLETE
    assert second.trusted_authority is ScanAuthority.INDETERMINATE
    assert [declaration.name for declaration in second.combined.trusted] == [
        "trusted"
    ]


def test_trusted_target_enumeration_failure_retains_last_known_set(
    monkeypatch,
    tmp_path,
):
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    (trusted_dir / "trusted.json").write_text(
        json.dumps({"name": "trusted"}),
        encoding="utf-8",
    )
    pointer_base = tmp_path / "registrar"
    add_pointer("trusted", trusted_dir, base=pointer_base)
    sources = RegistrarSources(
        base=pointer_base,
        dropins=tmp_path / "registrar.d",
        activation_source=_activation,
    )
    sources.refresh(emit_warnings=False)
    original_iterdir = Path.iterdir

    def deny_target(path):
        if path == trusted_dir:
            raise PermissionError("temporarily denied")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", deny_target)
    second = sources.refresh(emit_warnings=False)

    assert second.trusted_authority is ScanAuthority.INDETERMINATE
    assert [declaration.name for declaration in second.combined.trusted] == [
        "trusted"
    ]


def test_invalid_trusted_encoding_retains_only_trusted_tier(tmp_path):
    trusted_dir = tmp_path / "trusted"
    trusted_dir.mkdir()
    trusted_declaration = trusted_dir / "trusted.json"
    trusted_declaration.write_text(
        json.dumps({"name": "trusted"}),
        encoding="utf-8",
    )
    pointer_base = tmp_path / "registrar"
    add_pointer("trusted", trusted_dir, base=pointer_base)

    root = _plugin_root(tmp_path)
    _write_declaration(root, "plugin")
    dropins = tmp_path / "registrar.d"
    _write_manifest(dropins, root)
    current = [_activation({SOURCE: root})]
    sources = RegistrarSources(
        base=pointer_base,
        dropins=dropins,
        activation_source=lambda: current[0],
    )
    first = sources.refresh(emit_warnings=False)
    assert {declaration.name for declaration in first.combined.declarations} == {
        "plugin",
        "trusted",
    }

    trusted_declaration.write_bytes(b"\xff")
    current[0] = _activation()
    second = sources.refresh(emit_warnings=False)

    assert second.trusted_authority is ScanAuthority.INDETERMINATE
    assert second.trusted_error is not None
    assert [declaration.name for declaration in second.combined.declarations] == [
        "trusted"
    ]
    assert second.combined.plugins.findings[0].reason == "not-enabled"


def test_trusted_absence_authority_comes_from_pointer_read(tmp_path):
    pointer_base = tmp_path / "registrar"
    authority, declarations = discover_trusted(base=pointer_base)
    assert authority is ScanAuthority.ABSENT
    assert declarations == []

    pointer_base.mkdir()
    (pointer_base / "pointers.json").write_text("[]\n", encoding="utf-8")
    authority, declarations = discover_trusted(base=pointer_base)
    assert authority is ScanAuthority.COMPLETE
    assert declarations == []


def test_operational_warnings_are_capped_deduplicated_and_recover(
    tmp_path,
    caplog,
):
    dropins = tmp_path / "registrar.d"
    dropins.mkdir()
    for index in range(5):
        (dropins / f"{index}.json").write_text("{not json", encoding="utf-8")
    sources = RegistrarSources(
        base=tmp_path / "trusted",
        dropins=dropins,
        activation_source=_activation,
        warning_tracker=WarningTracker(limit=2, repeat_after_seconds=3600),
    )

    caplog.set_level(logging.INFO, logger="agent_dispatch.registrar_discovery")
    sources.refresh()
    warning_messages = [
        record.message for record in caplog.records if record.levelno == logging.WARNING
    ]
    assert len(warning_messages) == 3
    assert "3 additional registrar finding(s) suppressed" in warning_messages[-1]

    caplog.clear()
    sources.refresh()
    assert caplog.records == []

    for path in dropins.iterdir():
        path.unlink()
    sources.refresh()
    assert any(
        "recovered" in record.message
        for record in caplog.records
        if record.levelno == logging.INFO
    )


def test_registrar_doctor_json_and_human_share_classifier(
    tmp_path,
    monkeypatch,
    capsys,
):
    from agent_dispatch import registrar_registry

    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    dropins = tmp_path / "registrar.d"
    manifest = _write_manifest(dropins, root)
    pointer_base = tmp_path / "trusted"
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(pointer_base))
    monkeypatch.setenv(REGISTRAR_DROPINS_DIR_ENV, str(dropins))
    monkeypatch.setattr(
        registrar_registry,
        "resolve_active_plugins",
        _activation,
    )

    rc = main(["registrar", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["trusted"]["authority"] == "absent"
    assert payload["dropins"]["findings"][0]["entry"] == str(manifest)
    assert payload["dropins"]["findings"][0]["reason"] == "not-enabled"
    assert payload["dropins"]["fix_available"] is False
    assert payload["dropins"]["active_basis"] == "current-evidence-only"

    rc = main(["registrar", "doctor"])
    human = capsys.readouterr().out
    assert rc == 1
    assert "not-enabled" in human
    assert str(manifest) in human
    assert "report-only" in human


def test_indeterminate_remedy_never_recommends_removal(monkeypatch, tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    manifest = _write_manifest(registry, root)
    active = _activation({SOURCE: root})
    first = scan_registrar_registry(registry, activation_report=active)
    original_read_text = Path.read_text

    def deny_manifest(path, *args, **kwargs):
        if path == manifest:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_manifest)
    second = scan_registrar_registry(
        registry,
        previous=first.entries,
        activation_report=active,
    )

    remedy = second.findings[0].remedy or ""
    assert "Restore access" in remedy
    assert "remove" not in remedy.casefold()


def test_activation_uncertainty_preserves_exact_diagnostic(tmp_path):
    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    registry = tmp_path / "registrar.d"
    _write_manifest(registry, root)
    settings = tmp_path / "settings.json"
    activation_finding = Finding(
        registry="plugin-activation",
        entry=str(settings),
        status="indeterminate",
        reason="invalid-entry",
        remedy="Repair the settings document.",
        detail="enabledPlugins must be an object",
    )
    report = scan_registrar_registry(
        registry,
        activation_report=ActivationReport(
            authority=ScanAuthority.INDETERMINATE,
            decisions={},
            findings=(activation_finding,),
        ),
    )

    exact = [
        finding
        for finding in report.findings
        if finding.registry == "plugin-activation"
    ]
    assert exact == [activation_finding]
    assert report.declarations == ()


def test_human_doctor_reports_trusted_retention_possibility(
    monkeypatch,
    tmp_path,
    capsys,
):
    pointer_base = tmp_path / "trusted"
    pointer_base.mkdir()
    pointer_file = pointer_base / "pointers.json"
    pointer_file.write_text("[]\n", encoding="utf-8")
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(pointer_base))
    monkeypatch.setenv(
        REGISTRAR_DROPINS_DIR_ENV,
        str(tmp_path / "registrar.d"),
    )
    original_read_text = Path.read_text

    def deny_pointer(path, *args, **kwargs):
        if path == pointer_file:
            raise PermissionError("temporarily denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", deny_pointer)
    rc = main(["registrar", "doctor"])
    human = capsys.readouterr().out

    assert rc == 1
    assert "confirmed by current evidence" in human
    assert "may retain its last-known trusted declarations" in human


def test_registrar_doctor_clean_report_returns_zero(tmp_path, monkeypatch, capsys):
    from agent_dispatch import registrar_registry

    root = _plugin_root(tmp_path)
    _write_declaration(root, "general")
    dropins = tmp_path / "registrar.d"
    _write_manifest(dropins, root)
    monkeypatch.setenv(REGISTRAR_DIR_ENV, str(tmp_path / "trusted"))
    monkeypatch.setenv(REGISTRAR_DROPINS_DIR_ENV, str(dropins))
    monkeypatch.setattr(
        registrar_registry,
        "resolve_active_plugins",
        lambda: _activation({SOURCE: root}),
    )

    rc = main(["registrar", "doctor", "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert [entry["name"] for entry in payload["dropins"]["active"]] == ["general"]
    assert payload["dropins"]["findings"] == []
