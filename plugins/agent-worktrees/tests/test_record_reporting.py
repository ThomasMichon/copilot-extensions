"""Canonical records contain no reporter naming snapshots."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from machine_transport import LocalExecutionContext

from agent_worktrees import config as cfg, record_reporting, tracking


def mapping(path: Path) -> dict:
    return {
        "worktree_id": path.stem, "repo": "project", "branch": "worktree/example",
        "worktree_path": str(path.parent / "trees" / path.stem), "status": "active",
        "machine": {"invalid": "obsolete"}, "platform": ["obsolete"],
        "machine_identity_version": 999, "environment": False, "execution_machine": None,
    }


def test_pure_parser_ignores_all_legacy_naming_without_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "example.yaml"
    monkeypatch.setattr(record_reporting, "local_context", lambda *a: pytest.fail("parser did I/O"))
    record = tracking.parse_record_data(mapping(path), path=path)
    assert (record.machine, record.platform, record.machine_identity) == ("", "", None)
    assert not path.exists()
    remote = LocalExecutionContext("remote", "reporter", "wsl", "reporter-wsl")
    record = tracking.parse_record_data(mapping(path), path=path, reporting_context=remote)
    assert (record.machine, record.platform, record.execution_machine) == (
        "reporter", "wsl", "reporter-wsl",
    )


def test_strict_liveness_snapshot_keeps_current_reporter_outside_its_subject_fence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import record_liveness_refresh

    path = tmp_path / "example.yaml"
    path.write_text(yaml.safe_dump(mapping(path)), encoding="utf-8")
    context = LocalExecutionContext("observed", "current", "linux", "current")
    monkeypatch.setattr(record_reporting, "local_context", lambda *a: context)
    first = record_liveness_refresh._read(path)
    context = LocalExecutionContext("observed", "renamed", "linux", "renamed")
    second = record_liveness_refresh._read(path)
    assert first.execution_machine == "current" and second.execution_machine == "renamed"
    assert record_liveness_refresh._fence(first) == record_liveness_refresh._fence(second)


@pytest.mark.parametrize("copy_records", [False, True])
@pytest.mark.parametrize("environment", ["windows", "wsl", "linux"])
def test_cached_local_reads_reflect_registry_rename_without_record_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, copy_records: bool, environment: str,
) -> None:
    root = tmp_path / "project"
    directory = root / "worktrees"
    directory.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: root)
    monkeypatch.setattr(record_reporting, "configured_repository_paths", lambda project: {"project": tmp_path})
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "observed-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: environment)
    # Other repository/config discovery is not part of this metadata lookup.
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: pytest.fail("full Config loaded"))
    registry = tmp_path / "machines.yaml"

    def rename(name: str) -> None:
        registry.write_text(yaml.safe_dump({"machines": {name: {
            "hostname": "observed-host", "physical_host": "physical",
            "execution_platform": environment,
        }}}), encoding="utf-8")

    for name in ("first", "second"):
        path = directory / f"{name}.yaml"
        path.write_text(yaml.safe_dump(mapping(path)), encoding="utf-8")
    before = {path: path.read_bytes() for path in directory.glob("*.yaml")}
    original = record_reporting.current_context
    calls = []

    def counted(project: str) -> LocalExecutionContext:
        calls.append(project)
        return original(project)

    monkeypatch.setattr(record_reporting, "current_context", counted)
    rename("first-name")
    rows = tracking.list_records(directory, copy_records=copy_records)
    assert len(rows) == 2 and {row.execution_machine for row in rows} == {"first-name"}
    rename("renamed")
    rows = tracking.list_records(directory, copy_records=copy_records)
    assert len(rows) == 2 and {row.execution_machine for row in rows} == {"renamed"}
    assert calls == ["project", "project"]
    assert {path: path.read_bytes() for path in before} == before
    record = tracking.load_record(next(iter(before)))
    record.title = "ordinary update"
    tracking.save_record(record)
    assert not {
        "machine", "platform", "machine_identity_version", "environment", "execution_machine",
    }.intersection(yaml.safe_load(record.yaml_path.read_text(encoding="utf-8")))
    assert record.execution_machine == "renamed"


@pytest.mark.parametrize("defect", ["invalid", "ambiguous"])
def test_invalid_current_registry_is_not_a_legacy_label_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    monkeypatch.setattr(record_reporting, "configured_repository_paths", lambda project: {"project": tmp_path})
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "observed-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    data = {"machines": []} if defect == "invalid" else {"machines": {
        name: {"hostname": "observed-host", "execution_platform": "linux", "physical_host": "physical"}
        for name in ("first", "second")
    }}
    (tmp_path / "machines.yaml").write_text(yaml.safe_dump(data), encoding="utf-8")
    with pytest.raises(ValueError):
        record_reporting.current_context("project")
    path = tmp_path / "example.yaml"
    path.write_text(yaml.safe_dump(mapping(path)), encoding="utf-8")
    record = tracking.load_record(path)
    assert record.execution_machine == "observed-host"
    assert record.machine_fields["machine_context"] == {
        "status": "unavailable", "reason": "invalid-machine-registry",
    }


def test_filesystem_registry_discovery_ignores_machine_binding_and_keeps_knowledge_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_worktrees import repos

    root, anchor, knowledge = (tmp_path / name for name in ("state", "checkout", "knowledge"))
    for path in (root, anchor, knowledge):
        path.mkdir()
    monkeypatch.setattr(cfg, "project_dir", lambda project=None: root)
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "global.yaml")
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: tmp_path / "repos.yaml")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "observed-host")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "linux")
    monkeypatch.setattr(cfg, "load_config", lambda *a, **k: pytest.fail("full Config loaded"))
    (root / "config.yaml").write_text(yaml.safe_dump({
        "machine": "obsolete-binding", "platform": "windows", "knowledge_repo": "knowledge",
        "repos": {"project": {"anchor": str(anchor)}},
    }), encoding="utf-8")
    repos.write_registry(repos.ReposRegistry(repos={
        "knowledge": repos.RepoEntry(name="knowledge", repo_class="knowledge", paths={"linux": str(knowledge)}),
    }))
    (knowledge / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "current": {"hostname": "observed-host", "execution_platform": "linux", "physical_host": "physical"},
        "obsolete-binding": {"hostname": "another-host", "execution_platform": "linux", "physical_host": "other"},
    }}), encoding="utf-8")
    assert record_reporting.current_context("project").execution_machine == "current"
    (anchor / "machines.yaml").write_text(yaml.safe_dump({"machines": {
        "primary": {"hostname": "observed-host", "execution_platform": "linux", "physical_host": "physical"},
    }}), encoding="utf-8")
    assert record_reporting.current_context("project").execution_machine == "primary"
