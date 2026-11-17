from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from agent_machines import __main__ as cli
from agent_machines import cli_fleet_update
from agent_machines.layout import (
    inspect_layouts,
    inspect_repo_layout,
    migrate_repo_layout,
    resolve_cwd_repo,
    resolve_repo,
)
from agent_machines.manifest import ManifestError

from ._helpers import base_package, write_package

GIT = shutil.which("git")


def _git(*args: str) -> None:
    assert GIT is not None
    subprocess.run([GIT, *args], check=True)


def test_doctor_reports_canonical_layout(tmp_path: Path) -> None:
    write_package(tmp_path, "defaults.yaml", base_package(gate=["*"]))
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert report.ok
    assert report.status == "canonical"
    assert report.package_count == 1
    assert report.findings == []


def test_doctor_reports_legacy_layout_with_migration_command(tmp_path: Path) -> None:
    write_package(tmp_path, "defaults.yaml", base_package(gate=["*"]), legacy=True)
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert report.ok
    assert report.status == "legacy"
    assert report.package_count == 1
    assert report.findings[0].code == "legacy-layout"
    assert "agent-machines migrate --repo acme" in report.findings[0].message


def test_doctor_reports_mixed_layout_as_error(tmp_path: Path) -> None:
    write_package(tmp_path, "current.yaml", base_package(name="acme/current"))
    write_package(
        tmp_path,
        "legacy.yaml",
        base_package(name="acme/legacy"),
        legacy=True,
    )
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert not report.ok
    assert report.status == "mixed"
    assert any(finding.code == "mixed-layout" for finding in report.findings)


def test_doctor_reports_malformed_canonical_layout(tmp_path: Path) -> None:
    root = tmp_path / ".copilot-extensions" / "agent-machines"
    root.mkdir(parents=True)
    (root / "flat.yaml").write_text(
        "schema_version: 1\npackage: acme/flat\n",
        encoding="utf-8",
    )
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert not report.ok
    assert report.status == "malformed"
    assert report.findings[0].code == "invalid-layout"


def test_migrate_dry_run_preserves_legacy_files(tmp_path: Path) -> None:
    source = write_package(
        tmp_path,
        "defaults.yaml",
        base_package(gate=["*"]),
        legacy=True,
    )
    result = migrate_repo_layout(tmp_path, "acme")
    assert result.status == "would-migrate"
    assert result.dry_run and result.changed
    assert source.exists()
    assert not (tmp_path / ".copilot-extensions" / "agent-machines").exists()


def test_migrate_apply_moves_yaml_and_readme_byte_for_byte(tmp_path: Path) -> None:
    source = write_package(
        tmp_path,
        "defaults.yaml",
        base_package(gate=["*"]),
        legacy=True,
    )
    original = source.read_bytes()
    readme = source.parent / "README.md"
    readme.write_text("# Legacy notes\n", encoding="utf-8")

    result = migrate_repo_layout(tmp_path, "acme", apply=True)

    target = tmp_path / ".copilot-extensions" / "agent-machines" / "all" / "defaults.yaml"
    assert result.status == "migrated"
    assert target.read_bytes() == original
    assert (tmp_path / ".copilot-extensions" / "agent-machines" / "README.md").read_text(
        encoding="utf-8"
    ) == "# Legacy notes\n"
    assert not (tmp_path / ".github" / "machine-state").exists()
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert report.ok and report.status == "canonical"


def test_migrate_refuses_unknown_legacy_entries(tmp_path: Path) -> None:
    legacy = tmp_path / ".github" / "machine-state"
    legacy.mkdir(parents=True)
    (legacy / "notes.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(ManifestError, match="unsupported legacy entry"):
        migrate_repo_layout(tmp_path, "acme")
    assert not (tmp_path / ".copilot-extensions" / "agent-machines").exists()


def test_doctor_keeps_nonmigratable_legacy_layout_advisory(tmp_path: Path) -> None:
    source = write_package(
        tmp_path,
        "defaults.yaml",
        base_package(gate=["*"]),
        legacy=True,
    )
    notes = source.parent / "notes"
    notes.mkdir()
    (notes / "detail.txt").write_text("keep", encoding="utf-8")
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert report.ok
    assert report.status == "legacy"
    assert report.package_count == 1
    assert report.findings[0].code == "legacy-not-migratable"


def test_legacy_package_count_is_gate_filtered(tmp_path: Path) -> None:
    write_package(
        tmp_path,
        "defaults.yaml",
        base_package(gate=["other-box"]),
        legacy=True,
    )
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert report.status == "legacy"
    assert report.package_count == 0


def test_migrate_refuses_duplicate_legacy_package_names(tmp_path: Path) -> None:
    write_package(
        tmp_path,
        "one.yaml",
        base_package(name="acme/duplicate", gate=["*"]),
        legacy=True,
    )
    write_package(
        tmp_path,
        "two.yaml",
        base_package(name="acme/duplicate", gate=["*"]),
        legacy=True,
    )
    with pytest.raises(ManifestError, match="canonical packages require unique names"):
        migrate_repo_layout(tmp_path, "acme")


def test_migrate_refuses_mixed_layout(tmp_path: Path) -> None:
    write_package(tmp_path, "current.yaml", base_package(name="acme/current"))
    write_package(
        tmp_path,
        "legacy.yaml",
        base_package(name="acme/legacy"),
        legacy=True,
    )
    with pytest.raises(ManifestError, match="refusing mixed-layout migration"):
        migrate_repo_layout(tmp_path, "acme")


def test_migrate_rolls_back_partial_move_and_created_dirs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = write_package(
        tmp_path,
        "one.yaml",
        base_package(name="acme/one", gate=["*"]),
        legacy=True,
    )
    second = write_package(
        tmp_path,
        "two.yaml",
        base_package(name="acme/two", gate=["*"]),
        legacy=True,
    )
    original_replace = Path.replace

    def fail_second(source: Path, target: Path) -> Path:
        if source.name == "two.yaml":
            raise OSError("synthetic move failure")
        return original_replace(source, target)

    monkeypatch.setattr(Path, "replace", fail_second)
    with pytest.raises(ManifestError, match="synthetic move failure"):
        migrate_repo_layout(tmp_path, "acme", apply=True)
    assert first.exists() and second.exists()
    assert not (tmp_path / ".copilot-extensions" / "agent-machines").exists()


def test_migrate_rolls_back_when_legacy_directory_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = write_package(
        tmp_path,
        "one.yaml",
        base_package(name="acme/one", gate=["*"]),
        legacy=True,
    )
    legacy = source.parent
    original_rmdir = Path.rmdir

    def fail_legacy(directory: Path) -> None:
        if directory == legacy:
            raise OSError("synthetic rmdir failure")
        return original_rmdir(directory)

    monkeypatch.setattr(Path, "rmdir", fail_legacy)
    with pytest.raises(ManifestError, match="synthetic rmdir failure"):
        migrate_repo_layout(tmp_path, "acme", apply=True)
    assert source.exists()
    assert not (tmp_path / ".copilot-extensions" / "agent-machines").exists()


def test_doctor_reports_layout_path_type_collisions(tmp_path: Path) -> None:
    root = tmp_path / ".copilot-extensions" / "agent-machines"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("not a directory", encoding="utf-8")
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert not report.ok
    assert report.status == "malformed"
    assert "not a directory" in report.findings[0].message


def test_migrate_rejects_canonical_root_file(tmp_path: Path) -> None:
    root = tmp_path / ".copilot-extensions" / "agent-machines"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("not a directory", encoding="utf-8")
    with pytest.raises(ManifestError, match="canonical package path is not a directory"):
        migrate_repo_layout(tmp_path, "acme")


def test_migrate_second_apply_is_idempotent(tmp_path: Path) -> None:
    write_package(
        tmp_path,
        "defaults.yaml",
        base_package(gate=["*"]),
        legacy=True,
    )
    first = migrate_repo_layout(tmp_path, "acme", apply=True)
    second = migrate_repo_layout(tmp_path, "acme", apply=True)
    assert first.status == "migrated" and first.changed
    assert second.status == "already-canonical" and not second.changed


def test_migrate_empty_legacy_layout_is_noop(tmp_path: Path) -> None:
    legacy = tmp_path / ".github" / "machine-state"
    legacy.mkdir(parents=True)
    result = migrate_repo_layout(tmp_path, "acme", apply=True)
    assert result.status == "no-layout"
    assert not result.changed
    assert legacy.exists()
    assert not (tmp_path / ".copilot-extensions" / "agent-machines").exists()


def test_resolve_repo_prefers_adopted_name_over_cwd_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    registered = tmp_path / "registered"
    registered.mkdir()
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    (cwd / "acme").mkdir()
    monkeypatch.chdir(cwd)
    registry = {
        "repos": {
            "acme": {
                "windows": str(registered),
                "linux": str(registered),
                "wsl": str(registered),
            }
        }
    }
    projects: dict[str, Any] = {"projects": {"acme": {}}}
    name, path = resolve_repo("acme", registry, projects)
    assert name == "acme"
    assert path == registered


def test_resolve_repo_matches_canonical_name_case_insensitively(tmp_path: Path) -> None:
    registered = tmp_path / "registered"
    registered.mkdir()
    registry = {
        "repos": {
            "knowledge": {
                "windows": str(registered),
                "linux": str(registered),
                "wsl": str(registered),
            }
        }
    }
    projects: dict[str, Any] = {"projects": {"KNOWLEDGE": {}}}

    name, path = resolve_repo("KNOWLEDGE", registry, projects)

    assert name == "knowledge"
    assert path == registered


@pytest.mark.skipif(GIT is None, reason="git is required for linked-worktree fixture")
@pytest.mark.parametrize("entrypoint", [cli, cli_fleet_update])
@pytest.mark.parametrize("explicit_path", [False, True])
def test_resolve_cwd_repo_matches_registered_linked_worktree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    entrypoint: ModuleType, explicit_path: bool,
) -> None:
    anchor = tmp_path / "anchor"
    _git("init", "-q", str(anchor))
    _git("-C", str(anchor), "config", "user.email", "test@example.com")
    _git("-C", str(anchor), "config", "user.name", "Test")
    (anchor / "file.txt").write_text("x", encoding="utf-8")
    _git("-C", str(anchor), "add", "file.txt")
    _git("-C", str(anchor), "commit", "-qm", "init")
    worktree = tmp_path / "worktree"
    _git(
        "-C",
        str(anchor),
        "worktree",
        "add",
        "-q",
        "-b",
        "test",
        str(worktree),
    )
    registry = {
        "repos": {
            "canonical": {
                "windows": str(anchor),
                "linux": str(anchor),
                "wsl": str(anchor),
            }
        }
    }
    projects: dict[str, Any] = {"projects": {"canonical": {}}}

    name, path, anchor_path = resolve_cwd_repo(worktree, registry, projects)

    assert name == "canonical"
    assert path == worktree.resolve()
    assert anchor_path == anchor.resolve()

    for root, platform_name in ((anchor, "linux"), (worktree, "windows")):
        (root / "machines.yaml").write_text(
            f"machines:\n  fixture:\n    execution_platform: {platform_name}\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(entrypoint._discover, "read_registry", lambda: registry)
    monkeypatch.setattr(entrypoint._discover, "read_projects", lambda: projects)
    monkeypatch.chdir(worktree)

    identity = entrypoint._resolve_machine_identity(
        argparse.Namespace(
            repo=str(worktree) if explicit_path else None, machine="fixture"
        )
    )

    assert identity.execution_platform == "linux"
    assert identity.topology_path == anchor / "machines.yaml"


@pytest.mark.skipif(GIT is None, reason="git is required for linked-worktree fixture")
def test_resolve_cwd_repo_matches_registered_nonadopted_worktree(tmp_path: Path) -> None:
    anchor = tmp_path / "anchor"
    _git("init", "-q", str(anchor))
    _git("-C", str(anchor), "config", "user.email", "test@example.com")
    _git("-C", str(anchor), "config", "user.name", "Test")
    (anchor / "file.txt").write_text("x", encoding="utf-8")
    _git("-C", str(anchor), "add", "file.txt")
    _git("-C", str(anchor), "commit", "-qm", "init")
    worktree = tmp_path / "worktree"
    _git(
        "-C",
        str(anchor),
        "worktree",
        "add",
        "-q",
        "-b",
        "test",
        str(worktree),
    )
    registry = {
        "repos": {
            "supplemental": {
                "windows": str(anchor),
                "linux": str(anchor),
                "wsl": str(anchor),
            }
        }
    }

    name, path, anchor_path = resolve_cwd_repo(worktree, registry, {})

    assert name == "supplemental"
    assert path == worktree.resolve()
    assert anchor_path == anchor.resolve()


@pytest.mark.skipif(GIT is None, reason="git is required for repository fixture")
def test_resolve_cwd_repo_standalone_falls_back_to_top_level(tmp_path: Path) -> None:
    repo = tmp_path / "standalone"
    _git("init", "-q", str(repo))
    nested = repo / "nested"
    nested.mkdir()

    name, path, anchor_path = resolve_cwd_repo(nested, {}, {})

    assert name == "standalone"
    assert path == repo.resolve()
    assert anchor_path == repo.resolve()


@pytest.mark.skipif(GIT is None, reason="git is required for repository fixture")
def test_resolve_cwd_repo_outside_git_fails(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match=r"pass --repo.*or --all-projects"):
        resolve_cwd_repo(tmp_path, {}, {})


def test_resolve_cwd_repo_reports_missing_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agent_machines.layout.shutil.which", lambda name: None)

    with pytest.raises(
        ManifestError,
        match=r"pass --repo <registered-name>.*--all-projects",
    ):
        resolve_cwd_repo(tmp_path, {}, {})


def test_unavailable_adopted_repo_is_advisory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    registry = {
        "repos": {
            "ghost": {
                "windows": str(missing),
                "linux": str(missing),
                "wsl": str(missing),
            }
        }
    }
    projects: dict[str, Any] = {"projects": {"ghost": {}}}
    reports = inspect_layouts("box-1", registry=registry, projects=projects)
    assert len(reports) == 1
    assert reports[0].status == "unavailable"
    assert reports[0].ok
    assert reports[0].findings[0].level == "advisory"


def test_unavailable_bound_supplemental_repo_is_error(tmp_path: Path) -> None:
    harness = tmp_path / "harness"
    harness.mkdir()
    repo_config = harness / ".agent-worktrees" / "config.yaml"
    repo_config.parent.mkdir()
    repo_config.write_text("requires_external_state_root: true\n", encoding="utf-8")
    config_dir = tmp_path / ".harness"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text(
        "knowledge_repo: knowledge\n",
        encoding="utf-8",
    )
    missing = tmp_path / "knowledge"
    registry = {
        "repos": {
            "harness": {
                "windows": str(harness),
                "linux": str(harness),
                "wsl": str(harness),
            },
            "knowledge": {
                "windows": str(missing),
                "linux": str(missing),
                "wsl": str(missing),
            },
        }
    }
    projects = {"projects": {"harness": {"config_dir": str(config_dir)}}}

    reports = inspect_layouts("box-1", registry=registry, projects=projects)

    assert len(reports) == 2
    supplemental = reports[1]
    assert supplemental.status == "unavailable"
    assert not supplemental.ok
    assert supplemental.findings[0].code == "supplemental-repo-unavailable"


def test_doctor_cli_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_package(tmp_path, "defaults.yaml", base_package(gate=["*"]), legacy=True)
    rc = cli.main([
        "doctor",
        "--machine",
        "box-1",
        "--repo",
        str(tmp_path),
        "--json",
    ])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["ok"] is True
    assert payload["repos"][0]["status"] == "legacy"


def test_doctor_cli_returns_one_for_malformed_layout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    root = tmp_path / ".copilot-extensions" / "agent-machines"
    root.parent.mkdir(parents=True, exist_ok=True)
    root.write_text("bad", encoding="utf-8")
    rc = cli.main([
        "doctor",
        "--machine",
        "box-1",
        "--repo",
        str(tmp_path),
    ])
    assert rc == 1
    assert "[malformed]" in capsys.readouterr().out


def test_doctor_reports_repo_legacy_layout_with_migration_command(tmp_path: Path) -> None:
    write_package(tmp_path, "defaults.yaml", base_package(gate=["*"]), repo_legacy=True)
    report = inspect_repo_layout(tmp_path, "acme", "box-1")
    assert report.ok
    assert report.status == "legacy"
    assert report.package_count == 1
    assert report.findings[0].code == "legacy-layout"


def test_migrate_apply_moves_repo_legacy_tree(tmp_path: Path) -> None:
    shared = write_package(
        tmp_path,
        "shared.yaml",
        base_package(name="acme/shared", gate=["*"]),
        repo_legacy=True,
    )
    shared_bytes = shared.read_bytes()
    specific = write_package(
        tmp_path,
        "specific.yaml",
        base_package(name="acme/specific", gate=["*"]),
        machine="box-1",
        repo_legacy=True,
    )
    specific_bytes = specific.read_bytes()

    result = migrate_repo_layout(tmp_path, "acme", apply=True)

    assert result.status == "migrated"
    assert (
        tmp_path / ".copilot-extensions" / "agent-machines" / "all" / "shared.yaml"
    ).read_bytes() == shared_bytes
    assert (
        tmp_path
        / ".copilot-extensions"
        / "agent-machines"
        / "machines"
        / "box-1"
        / "specific.yaml"
    ).read_bytes() == specific_bytes
    assert not (tmp_path / ".agent-machines").exists()


def test_migrate_cli_is_dry_run_by_default(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    source = write_package(
        tmp_path,
        "defaults.yaml",
        base_package(gate=["*"]),
        legacy=True,
    )
    rc = cli.main(["migrate", "--repo", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "would-migrate"
    assert payload["dry_run"] is True
    assert source.exists()


def test_migrate_cli_json_error_is_structured(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    legacy = tmp_path / ".github" / "machine-state"
    legacy.mkdir(parents=True)
    (legacy / "bad.txt").write_text("bad", encoding="utf-8")
    rc = cli.main(["migrate", "--repo", str(tmp_path), "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert payload["ok"] is False
    assert "unsupported legacy entry" in payload["error"]
