"""Tests for declarative static instruction projections."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

_SCRIPTS = (
    Path(__file__).resolve().parents[1]
    / "skills"
    / "reviewing-customizations"
    / "scripts"
)
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import instruction_projections as projections

_SCANNER_PATH = _SCRIPTS / "scan-customizations.py"
_scanner_spec = importlib.util.spec_from_file_location(
    "scan_customizations_projection_tests", _SCANNER_PATH
)
scanner = importlib.util.module_from_spec(_scanner_spec)
sys.modules[_scanner_spec.name] = scanner
_scanner_spec.loader.exec_module(scanner)
_MANAGER_PATH = _SCRIPTS / "manage-instruction-projections.py"
_manager_spec = importlib.util.spec_from_file_location(
    "manage_instruction_projections_tests", _MANAGER_PATH
)
manager = importlib.util.module_from_spec(_manager_spec)
sys.modules[_manager_spec.name] = manager
_manager_spec.loader.exec_module(manager)

REPO = Path(__file__).resolve().parents[3]


def _source(plugin: Path, marketplace: str, name: str) -> SimpleNamespace:
    return SimpleNamespace(
        origin=f"{marketplace}/{name}",
        payload_root=plugin,
        skills_root=plugin / "skills",
        controlled=False,
        source="",
        version="",
    )


def _write_plugin(
    root: Path,
    marketplace: str,
    name: str,
    *,
    version: str = "1.0.0",
    entries: list[dict] | None = None,
    body: str = "Keep this static fallback useful.\n",
) -> tuple[Path, SimpleNamespace]:
    plugin = root / marketplace / name
    template = plugin / "instructions" / "fallback.instructions.md"
    template.parent.mkdir(parents=True, exist_ok=True)
    template.write_text(
        '---\napplyTo: "**"\n---\n\n# Fallback\n\n' + body,
        encoding="utf-8",
        newline="\n",
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": name, "version": version}),
        encoding="utf-8",
    )
    declarations = entries or [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/{name}/fallback.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [f"{name}:static-fallback"],
        }
    ]
    (plugin / "instruction-projections.json").write_text(
        json.dumps(
            {
                "schema": projections.DECLARATION_SCHEMA,
                "version": projections.DECLARATION_VERSION,
                "projections": declarations,
            }
        ),
        encoding="utf-8",
    )
    return plugin, _source(plugin, marketplace, name)


def _lock(repo: Path) -> dict:
    return json.loads(
        (repo / ".github" / "copilot" / "context-projections.json").read_text(
            encoding="utf-8"
        )
    )


def _projection(repo: Path, name: str = "policy") -> Path:
    return (
        repo
        / ".github"
        / "instructions"
        / name
        / "fallback.instructions.md"
    )


def test_render_is_deterministic_and_marker_carries_complete_provenance(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    result = projections.Result(operation="test")
    specs, _unknown = projections._load_specs(repo, [source], result)

    first = projections.render_projection(specs[0])
    second = projections.render_projection(specs[0])
    marker = projections._parse_marker(first.content)

    assert result.blocking == 0
    assert first.content == second.content
    assert first.sha256 == second.sha256
    assert b"\r" not in first.content
    assert marker == first.marker
    assert marker["schema"] == projections.PROJECTION_SCHEMA
    assert marker["version"] == projections.PROJECTION_VERSION
    assert marker["plugin"] == "policy@market"
    assert marker["pluginVersion"] == "1.0.0"
    assert marker["templateSha256"] == specs[0].template_sha256
    assert marker["renderedBytes"] == len(first.content)
    without_marker = b"\n".join(
        line
        for line in first.content.splitlines()
        if not line.startswith(projections.MARKER_PREFIX.encode("ascii"))
    )
    assert b"Keep this static fallback useful." in without_marker


def test_sync_safely_creates_then_updates_projection_and_lock(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")

    created = projections.sync_repository(repo, [source])
    original = _projection(repo).read_bytes()
    initial_lock = _lock(repo)

    assert created.blocking == 0
    assert created.changed == [
        ".github/instructions/policy/fallback.instructions.md"
    ]
    assert created.lock_updated is True
    assert initial_lock["schema"] == projections.LOCK_SCHEMA
    assert initial_lock["version"] == projections.LOCK_VERSION

    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "1.0.1"}),
        encoding="utf-8",
    )
    template = plugin / "instructions" / "fallback.instructions.md"
    template.write_text(
        '---\napplyTo: "**"\n---\n\n# Fallback\n\nUpdated policy.\n',
        encoding="utf-8",
        newline="\n",
    )
    updated = projections.sync_repository(repo, [source])

    assert updated.blocking == 0
    assert _projection(repo).read_bytes() != original
    assert _lock(repo)["projections"][0]["pluginVersion"] == "1.0.1"

    unchanged = projections.sync_repository(repo, [source])
    assert unchanged.blocking == 0
    assert unchanged.changed == []
    assert unchanged.unchanged == [
        ".github/instructions/policy/fallback.instructions.md"
    ]
    assert unchanged.lock_updated is False


@pytest.mark.parametrize(
    "mutation",
    [
        "unmarked",
        "local-body",
        "malformed-marker",
        "missing-lock",
        "different-owner",
    ],
)
def test_sync_refuses_unsafe_overwrite_cases(
    tmp_path: Path,
    mutation: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    destination = _projection(repo)
    if mutation == "unmarked":
        destination.parent.mkdir(parents=True)
        destination.write_text("repository-owned\n", encoding="utf-8")
    else:
        assert projections.sync_repository(repo, [source]).blocking == 0
        if mutation == "local-body":
            destination.write_text(
                destination.read_text(encoding="utf-8") + "\nlocal edit\n",
                encoding="utf-8",
            )
        elif mutation == "malformed-marker":
            text = destination.read_text(encoding="utf-8")
            destination.write_text(
                text.replace(
                    projections.MARKER_PREFIX,
                    projections.MARKER_PREFIX + "{bad ",
                    1,
                ),
                encoding="utf-8",
            )
        elif mutation == "missing-lock":
            (
                repo / ".github" / "copilot" / "context-projections.json"
            ).unlink()
        elif mutation == "different-owner":
            lock = _lock(repo)
            lock["projections"][0]["plugin"] = "other@market"
            (
                repo / ".github" / "copilot" / "context-projections.json"
            ).write_text(json.dumps(lock), encoding="utf-8")

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert result.changed == []


def test_sync_refuses_destination_symlink_or_reparse_point(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    instructions = repo / ".github" / "instructions"
    instructions.parent.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        instructions.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert not (outside / "policy" / "fallback.instructions.md").exists()


def test_sync_and_scan_refuse_dangling_lock_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    lock_path.parent.mkdir(parents=True)
    try:
        lock_path.symlink_to(tmp_path / "missing-lock-target")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    synced = projections.sync_repository(repo, [source])
    scanned = projections.scan_repository(repo)

    assert synced.blocking > 0
    assert synced.changed == []
    assert scanned.blocking > 0
    assert not _projection(repo).exists()


def test_sync_refuses_dangling_declaration_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    declaration = plugin / "instruction-projections.json"
    declaration.unlink()
    try:
        declaration.symlink_to(tmp_path / "missing-declaration")
    except OSError:
        pytest.skip("symlink creation is unavailable")

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-declaration"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


def test_sync_refuses_partial_enabled_plugin_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    partial = tmp_path / "installed" / "market" / "policy"
    partial.mkdir(parents=True)
    source = _source(partial, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-source-unavailable"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


@pytest.mark.parametrize(
    "manifest_relative",
    [
        Path("plugin.json"),
        Path(".claude-plugin/plugin.json"),
    ],
)
def test_nonparticipating_available_plugin_is_not_projection_validated(
    tmp_path: Path,
    manifest_relative: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin = tmp_path / "installed" / "market" / "policy"
    manifest = plugin / manifest_relative
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        json.dumps({"name": "policy", "version": "1.0.0-beta.1"}),
        encoding="utf-8",
    )
    source = _source(plugin, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert result.declared == 0
    assert result.changed == []


def test_projection_participant_supports_legacy_manifest_layout(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    root_manifest = plugin / "plugin.json"
    legacy_manifest = plugin / ".claude-plugin" / "plugin.json"
    legacy_manifest.parent.mkdir()
    root_manifest.replace(legacy_manifest)

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert result.declared == 1
    assert _projection(repo).is_file()


def test_sync_refuses_symlinked_repository_root(tmp_path: Path) -> None:
    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    linked_repo = tmp_path / "linked-repo"
    try:
        linked_repo.symlink_to(real_repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    result = projections.sync_repository(linked_repo, [source])

    assert any(
        finding.check == "projection-root"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )


def test_cli_preserves_symlinked_repository_root_for_refusal(
    tmp_path: Path,
    capsys,
) -> None:
    real_repo = tmp_path / "real-repo"
    real_repo.mkdir()
    linked_repo = tmp_path / "linked-repo"
    try:
        linked_repo.symlink_to(real_repo, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable")

    exit_code = manager.main(["scan", str(linked_repo), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["findings"][0]["check"] == "projection-root"


def test_offline_scan_validates_marker_lock_and_orphans(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    clean = projections.scan_repository(repo)
    assert clean.blocking == 0

    orphan = (
        repo
        / ".github"
        / "instructions"
        / "orphan"
        / "orphan.instructions.md"
    )
    orphan.parent.mkdir()
    orphan.write_bytes(_projection(repo).read_bytes())
    orphaned = projections.scan_repository(repo)
    assert any(
        finding.check == "projection-orphan-file"
        for finding in orphaned.findings
    )

    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    lock_path.write_text('{"schema":"wrong"}\n', encoding="utf-8")
    malformed = projections.scan_repository(repo)
    assert any(
        finding.check == "projection-lock"
        and finding.severity == projections.BLOCKING
        for finding in malformed.findings
    )


def test_current_source_update_is_advisory_until_sync(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    original = _projection(repo).read_bytes()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )

    result = projections.scan_repository(repo, [source])

    assert result.blocking == 0
    assert any(
        finding.check == "projection-source-update"
        and finding.severity == projections.WARNING
        for finding in result.findings
    )
    assert _projection(repo).read_bytes() == original


def test_duplicate_sources_destinations_and_overlap_are_reported(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    duplicate_entries = [
        {
            "id": "same",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                ".github/instructions/policy/first.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
        {
            "id": "same",
            "template": "instructions/fallback.instructions.md",
            "destination": (
                ".github/instructions/policy/first.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        },
    ]
    _plugin, duplicate_source = _write_plugin(
        tmp_path, "market", "policy", entries=duplicate_entries
    )
    duplicate_result = projections.scan_repository(repo, [duplicate_source])
    assert any(
        finding.check == "projection-duplicate-source"
        for finding in duplicate_result.findings
    )
    assert any(
        finding.check == "projection-duplicate-destination"
        for finding in duplicate_result.findings
    )

    _one, first = _write_plugin(tmp_path, "market", "first")
    _two, second = _write_plugin(tmp_path, "market", "second")
    overlap = projections.scan_repository(repo, [first, second])
    assert any(
        finding.check == "projection-overlap"
        and finding.severity == projections.WARNING
        for finding in overlap.findings
    )


@pytest.mark.parametrize(
    "filename",
    [
        "AUX.instructions.md",
        "bad?.instructions.md",
        "trailing.instructions.md.",
    ],
)
def test_declarations_reject_nonportable_destinations(
    tmp_path: Path,
    filename: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": "fallback",
            "template": "instructions/fallback.instructions.md",
            "destination": f".github/instructions/policy/{filename}",
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
    ]
    _plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-declaration"
        and "portable filesystem components" in finding.message
        for finding in result.findings
    )


def test_declarations_reject_casefolded_destination_collisions(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{filename}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id, filename in (
            ("upper", "Fallback"),
            ("lower", "fallback"),
        )
    ]
    _plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-duplicate-destination"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


@pytest.mark.parametrize("failure_call", [2, 3])
def test_sync_rolls_back_projection_transaction_failures(
    tmp_path: Path,
    monkeypatch,
    failure_call: int,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{source_id}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id in ("first", "second")
    ]
    plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )
    assert projections.sync_repository(repo, [source]).blocking == 0
    paths = [
        repo
        / ".github"
        / "instructions"
        / "policy"
        / f"{source_id}.instructions.md"
        for source_id in ("first", "second")
    ]
    lock_path = repo / ".github" / "copilot" / "context-projections.json"
    before = {path: path.read_bytes() for path in [*paths, lock_path]}
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )
    original_atomic_write = projections._atomic_write
    calls = 0

    def fail_once(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise OSError("injected transaction failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_once)

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert result.changed == []
    assert {path: path.read_bytes() for path in before} == before


def test_sync_preserves_edit_made_after_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0
    destination = _projection(repo)
    lock_before = (
        repo / ".github" / "copilot" / "context-projections.json"
    ).read_bytes()
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )
    original_transaction = projections._transactional_write
    local_edit = b"concurrent local edit\n"

    def edit_before_commit(changes) -> None:
        destination.write_bytes(local_edit)
        original_transaction(changes)

    monkeypatch.setattr(
        projections, "_transactional_write", edit_before_commit
    )

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert result.changed == []
    assert destination.read_bytes() == local_edit
    assert (
        repo / ".github" / "copilot" / "context-projections.json"
    ).read_bytes() == lock_before


def test_rollback_does_not_overwrite_a_concurrent_edit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    entries = [
        {
            "id": source_id,
            "template": "instructions/fallback.instructions.md",
            "destination": (
                f".github/instructions/policy/{source_id}.instructions.md"
            ),
            "customizationKind": "instructions",
            "applyTo": "**",
            "legacyMarkers": [],
        }
        for source_id in ("first", "second")
    ]
    plugin, source = _write_plugin(
        tmp_path, "market", "policy", entries=entries
    )
    assert projections.sync_repository(repo, [source]).blocking == 0
    first = (
        repo
        / ".github"
        / "instructions"
        / "policy"
        / "first.instructions.md"
    )
    (plugin / "plugin.json").write_text(
        json.dumps({"name": "policy", "version": "2.0.0"}),
        encoding="utf-8",
    )
    original_atomic_write = projections._atomic_write
    local_edit = b"concurrent local edit\n"
    calls = 0

    def fail_after_edit(path: Path, content: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            first.write_bytes(local_edit)
            raise OSError("injected transaction failure")
        original_atomic_write(path, content)

    monkeypatch.setattr(projections, "_atomic_write", fail_after_edit)

    result = projections.sync_repository(repo, [source])

    assert result.blocking > 0
    assert first.read_bytes() == local_edit
    assert any("rollback also failed" in finding.message for finding in result.findings)


def test_file_and_aggregate_budgets_are_blocking(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, oversized = _write_plugin(
        tmp_path,
        "market",
        "large",
        body="x" * 3700 + "\n",
    )
    file_result = projections.sync_repository(repo, [oversized])
    assert any(
        finding.check == "projection-budget"
        and finding.severity == projections.BLOCKING
        for finding in file_result.findings
    )

    aggregate_repo = tmp_path / "aggregate-repo"
    aggregate_repo.mkdir()
    sources = [
        _write_plugin(
            tmp_path,
            "aggregate-market",
            f"policy-{index}",
            body="x" * 2700 + "\n",
        )[1]
        for index in range(4)
    ]
    aggregate_result = projections.sync_repository(aggregate_repo, sources)
    assert any(
        finding.check == "projection-budget"
        and "aggregate" in finding.message
        for finding in aggregate_result.findings
    )


@pytest.mark.parametrize(
    "dynamic",
    [
        "Session 123e4567-e89b-12d3-a456-426614174000\n",
        "Read ${HOME} before acting.\n",
        "Read ~/.copilot/session-state/<id>/files/policy.md.\n",
        "Read C:\\Users\\example\\.copilot\\installed-plugins\\policy.\n",
    ],
)
def test_declarations_reject_dynamic_or_session_specific_content(
    tmp_path: Path,
    dynamic: str,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(
        tmp_path, "market", "policy", body=dynamic
    )

    result = projections.scan_repository(repo, [source])

    assert any(
        finding.check == "projection-declaration"
        and "forbidden dynamic content" in finding.message
        for finding in result.findings
    )


def test_legacy_managed_region_is_actionable_but_not_deleted(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    legacy = (
        "<!-- policy:static-fallback:start -->\n"
        "Old fallback.\n"
        "<!-- policy:static-fallback:end -->\n"
    )
    (repo / "AGENTS.md").write_text(legacy, encoding="utf-8")
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert result.blocking == 0
    assert any(
        finding.check == "projection-legacy-region"
        and finding.severity == projections.WARNING
        for finding in result.findings
    )
    assert (repo / "AGENTS.md").read_text(encoding="utf-8") == legacy


def test_existing_scanner_includes_projection_findings_and_inventory(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")

    report = scanner.run(repo, [source])

    assert any(
        finding.check == "projection-missing"
        for finding in report.findings
    )
    assert report.instruction_projections is not None
    assert report.instruction_projections["schema"] == projections.RESULT_SCHEMA
    assert report.instruction_projections["declared"] == 1


def test_orphaned_lock_is_reported_when_enabled_sources_no_longer_declare_it(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _plugin, source = _write_plugin(tmp_path, "market", "policy")
    assert projections.sync_repository(repo, [source]).blocking == 0

    result = projections.scan_repository(repo, [])

    assert any(
        finding.check == "projection-orphan-lock"
        and finding.severity == projections.WARNING
        for finding in result.findings
    )


def test_repository_projection_discovery_excludes_user_only_plugins(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    home = tmp_path / "home"
    installed = tmp_path / "installed"
    _write_plugin(installed, "market", "repo-policy")
    _write_plugin(installed, "market", "user-policy")
    repo_settings = repo / ".github" / "copilot" / "settings.json"
    repo_settings.parent.mkdir(parents=True)
    repo_settings.write_text(
        json.dumps(
            {"enabledPlugins": {"repo-policy@market": True}}
        ),
        encoding="utf-8",
    )
    (repo_settings.parent / "settings.local.json").write_text(
        json.dumps(
            {"enabledPlugins": {"user-policy@market": True}}
        ),
        encoding="utf-8",
    )
    claude_settings = repo / ".claude" / "settings.local.json"
    claude_settings.parent.mkdir(parents=True)
    claude_settings.write_text(
        json.dumps(
            {"enabledPlugins": {"user-policy@market": True}}
        ),
        encoding="utf-8",
    )
    user_settings = home / ".copilot" / "settings.json"
    user_settings.parent.mkdir(parents=True)
    user_settings.write_text(
        json.dumps(
            {"enabledPlugins": {"user-policy@market": True}}
        ),
        encoding="utf-8",
    )

    sources = projections.discover_enabled_sources(
        repo,
        installed_root=installed,
        home=home,
        require_trust=False,
    )

    assert [source.origin for source in sources] == ["market/repo-policy"]


def test_cli_sync_reads_committed_settings_without_folder_trust(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    installed = tmp_path / "installed"
    _write_plugin(installed, "market", "policy")
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps({"enabledPlugins": {"policy@market": True}}),
        encoding="utf-8",
    )

    exit_code = manager.main(
        [
            "sync",
            str(repo),
            "--installed-root",
            str(installed),
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["declared"] == 1
    assert _projection(repo).is_file()


def test_integrated_scanner_reads_projection_settings_without_folder_trust(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    marketplace_root = repo / "payloads" / "market"
    _write_plugin(repo / "payloads", "market", "policy")
    marketplace_manifest = (
        marketplace_root / ".claude-plugin" / "marketplace.json"
    )
    marketplace_manifest.parent.mkdir()
    marketplace_manifest.write_text(
        json.dumps(
            {
                "name": "market",
                "plugins": [{"name": "policy", "source": "policy"}],
            }
        ),
        encoding="utf-8",
    )
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(
        json.dumps(
            {
                "enabledPlugins": {"policy@market": True},
                "extraKnownMarketplaces": {
                    "market": {
                        "source": {
                            "source": "directory",
                            "path": "payloads/market",
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    exit_code = scanner.main(
        [str(repo), "--from-settings", "--strict", "--json"]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["instruction_projections"]["declared"] == 1
    assert any(
        finding["check"] == "projection-missing"
        for finding in payload["findings"]
    )


def test_cli_sync_blocks_malformed_committed_settings(
    tmp_path: Path,
    capsys,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    settings = repo / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{", encoding="utf-8")

    exit_code = manager.main(["sync", str(repo), "--json"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 1
    assert payload["findings"][0]["check"] == "projection-settings"


def test_sync_blocks_unavailable_enabled_plugin_payload(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    missing = tmp_path / "installed" / "market" / "policy"
    source = _source(missing, "market", "policy")

    result = projections.sync_repository(repo, [source])

    assert any(
        finding.check == "projection-source-unavailable"
        and finding.severity == projections.BLOCKING
        for finding in result.findings
    )
    assert result.changed == []


def test_self_host_source_supports_legacy_marketplace_manifest(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    editable, _editable_source = _write_plugin(
        repo, "payloads", "policy", version="2.0.0", body="Editable.\n"
    )
    legacy_manifest = repo / ".claude-plugin" / "marketplace.json"
    legacy_manifest.parent.mkdir()
    legacy_manifest.write_text(
        json.dumps(
            {
                "name": "market",
                "plugins": [{"name": "policy", "source": "payloads/policy"}],
            }
        ),
        encoding="utf-8",
    )
    installed, source = _write_plugin(
        tmp_path, "market", "policy", version="1.0.0", body="Installed.\n"
    )
    assert editable != installed
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(repo, [source], result)

    assert result.blocking == 0
    assert specs[0].plugin_version == "2.0.0"
    assert b"Editable." in specs[0].template_content


def test_consumer_same_named_plugin_directory_does_not_shadow_resolved_payload(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    shadow, _shadow_source = _write_plugin(
        repo, "plugins", "policy", version="9.9.9", body="Shadow.\n"
    )
    resolved, source = _write_plugin(
        tmp_path, "market", "policy", version="1.2.3", body="Resolved.\n"
    )
    assert shadow != resolved
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(repo, [source], result)

    assert result.blocking == 0
    assert specs[0].plugin_version == "1.2.3"
    assert b"Resolved." in specs[0].template_content


def test_result_json_shape_is_versioned_and_deterministic() -> None:
    result = projections.Result(operation="scan")
    result.add(
        projections.WARNING,
        "projection-overlap",
        "<plugin-stack>",
        "example",
    )

    first = json.dumps(result.to_dict(), sort_keys=True)
    second = json.dumps(result.to_dict(), sort_keys=True)

    assert first == second
    assert result.to_dict()["schema"] == projections.RESULT_SCHEMA
    assert result.to_dict()["version"] == projections.RESULT_VERSION


def test_representative_plugins_ship_valid_canonical_declarations() -> None:
    sources = [
        _source(
            REPO / "plugins" / "ai-attribution",
            "copilot-extensions",
            "ai-attribution",
        ),
        _source(
            REPO / "plugins" / "efforts",
            "copilot-extensions",
            "efforts",
        ),
    ]
    result = projections.Result(operation="test")

    specs, _unknown = projections._load_specs(REPO, sources, result)

    assert result.blocking == 0
    assert {spec.source_id for spec in specs} == {
        "publication-safety",
        "completion-gate",
    }
    assert all(spec.apply_to == "**" for spec in specs)
    assert all(spec.template_bytes < projections.MAX_TEMPLATE_BYTES for spec in specs)
    assert all(
        f"[owner: {spec.plugin_name}@{spec.plugin_version}]"
        in spec.template_content.decode("utf-8")
        for spec in specs
    )
