"""Versioned tracking tuples retain execution ownership and historical bytes."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, execution_spaces, tracking
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity


def _record(path: Path, *, versioned: bool = True) -> tracking.WorktreeRecord:
    data = {
        "worktree_id": path.stem, "branch": f"worktree/{path.stem}",
        "worktree_path": str(path.parent / "trees" / path.stem), "repo": "project",
        "machine": "desk-wsl", "platform": "wsl", "status": "active",
    }
    if versioned:
        data.update(WorktreeMachineIdentity("desk", "wsl", "guest-space").to_record_fields())
    return tracking.parse_record_data(data, path=path)


@pytest.mark.parametrize("versioned", [False, True])
def test_record_identity_roundtrip_does_not_migrate_legacy(tmp_path: Path, versioned: bool) -> None:
    path = tmp_path / "retained-wsl-wsl.yaml"
    record = _record(path, versioned=versioned)
    tracking.save_record(record, path)
    before = path.read_bytes()
    loaded = tracking.load_record(path)
    assert loaded.machine == ("desk" if versioned else "desk-wsl")
    assert loaded.execution_machine == ("guest-space" if versioned else "desk-wsl")
    assert loaded.worktree_id == path.stem
    assert loaded.machine_identity == record.machine_identity
    assert loaded.execution_reference_fields["machine"] == loaded.execution_machine
    if versioned:
        assert loaded.execution_reference_fields["worktree_identity"] == loaded.machine_fields
    else:
        assert loaded.execution_reference_fields == {"machine": loaded.machine}
    assert yaml.safe_load(before) | loaded.machine_fields == yaml.safe_load(before)
    tracking.save_record(loaded, path)
    assert path.read_bytes() == before
    if not versioned:
        assert b"machine_identity_version" not in before
        assert b"execution_machine" not in before
        assert b"environment:" not in before


@pytest.mark.parametrize("field,value", [
    ("execution_machine", "other-guest"),
    ("environment", "linux"),
    ("machine_identity_version", 2),
])
def test_same_physical_host_cannot_hide_identity_cas_drift(
    tmp_path: Path, field: str, value: object,
) -> None:
    path = tmp_path / "owned.yaml"
    tracking.save_record(_record(path), path)
    stale = tracking.load_record(path)
    data = yaml.safe_load(path.read_text())
    data[field] = value
    if field == "environment":
        data["platform"] = value
    path.write_text(yaml.safe_dump(data))
    before = path.read_bytes()
    stale.title = "stale writer"
    with pytest.raises(ValueError, match="identity|version"):
        tracking.save_record(stale, path)
    assert path.read_bytes() == before


@pytest.mark.parametrize("versioned", [False, True])
def test_ordinary_writer_cannot_upgrade_or_relabel_a_tuple(tmp_path: Path, versioned: bool) -> None:
    path = tmp_path / "owned.yaml"
    tracking.save_record(_record(path, versioned=versioned), path)
    loaded = tracking.load_record(path)
    loaded.machine_identity = WorktreeMachineIdentity(
        loaded.machine, "wsl", "new-guest",
    )
    before = path.read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        tracking.save_record(loaded, path)
    assert path.read_bytes() == before


def test_legacy_explicit_owner_label_repair_still_uses_loaded_fence(tmp_path: Path) -> None:
    path = tmp_path / "owned.yaml"
    tracking.save_record(_record(path, versioned=False), path)
    loaded = tracking.load_record(path)
    loaded.machine = "old-label-repaired"
    tracking.save_record(loaded, path)
    assert tracking.load_record(path).machine == "old-label-repaired"
    assert tracking.load_record(path).machine_identity is None


@pytest.mark.parametrize("defect", [
    "physical", "environment", "execution", "config-platform", "native", "snapshot",
])
def test_tuple_mutation_never_uses_physical_host_equality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    (tmp_path / "machines.yaml").write_text(
        "machines:\n"
        "  native-space: {execution_platform: windows, physical_host: desk}\n"
        "  guest-space: {execution_platform: wsl, physical_host: desk}\n"
        "  other-guest: {execution_platform: wsl, physical_host: elsewhere}\n",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="guest-space", platform="wsl", repo_name="project",
        repos={"project": cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"))},
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    path = tmp_path / "project" / "worktrees" / "owned.yaml"
    path.parent.mkdir(parents=True)
    tracking.save_record(_record(path), path)
    record = tracking.load_record(path)
    execution_spaces.require_record_mutation(record, config)
    if defect == "physical":
        record.machine = "elsewhere"
        record.machine_identity = WorktreeMachineIdentity("elsewhere", "wsl", "guest-space")
    elif defect == "environment":
        record.platform = "windows"
        record.machine_identity = WorktreeMachineIdentity("desk", "windows", "native-space")
    elif defect == "execution":
        record.machine_identity = WorktreeMachineIdentity("desk", "wsl", "other-guest")
    elif defect == "config-platform":
        config = replace(config, platform="windows")
    elif defect == "snapshot":
        record.worktree_path = str(tmp_path / "foreign-checkout")
    else:
        config = replace(config, machine="native-space", platform="windows")
        monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="identity|execution|platform"):
        execution_spaces.require_record_mutation(record, config)


def test_record_json_contains_physical_metadata_and_explicit_owner(tmp_path: Path) -> None:
    from agent_worktrees.__main__ import _worktree_to_dict

    path = tmp_path / "owned.yaml"
    for versioned in (False, True):
        record = _record(path, versioned=versioned)
        result = _worktree_to_dict(record)
        assert {key: result[key] for key in record.machine_fields} == record.machine_fields
        if not versioned:
            assert "execution_machine" not in result
            assert "machine_identity_version" not in result


def test_tuple_requires_registered_owner_even_when_legacy_fallback_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = cfg.Config(
        srcroot=str(tmp_path), machine="desk-wsl", platform="wsl", repo_name="project",
    )
    monkeypatch.setattr(execution_spaces, "registry_for_config", lambda config: {})
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    record = _record(tmp_path / "owned.yaml", versioned=False)
    execution_spaces.require_record_mutation(record, config)

    record.machine = "desk"
    record.machine_identity = WorktreeMachineIdentity("desk", "wsl", "desk-wsl")
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="registered execution identity"):
        execution_spaces.require_record_mutation(record, config)


def test_owner_refs_and_native_session_claims_use_execution_key(tmp_path: Path) -> None:
    from agent_worktrees import claims_owner, native_restart_claims
    from agent_worktrees import tracking_producer_write, tracking_relationship_write

    record = _record(tmp_path / "owned.yaml")
    expected = "guest-space/project/owned"
    claim = tracking.ResourceClaim(
        kind="session", ref=f"{expected}#session",
        producer={"creator": "owner", "token": "token", "request_id": "request", "phase": "bound"},
    )
    assert claims_owner._owner_dict("project", record, claim)["qualified_ref"] == expected
    assert tracking_producer_write._response(record, claim)["owner"] == expected
    assert tracking_relationship_write._response(record, claim)["owner"] == expected
    claim.producer = None
    record.resources = [claim]
    assert native_restart_claims.original_session_only(record, "session")
    claim.ref = "desk/project/owned#session"
    assert not native_restart_claims.original_session_only(record, "session")


@pytest.mark.parametrize("version", [1, True, 2])
def test_resident_rows_preserve_or_reject_explicit_identity(
    tmp_path: Path, version: object,
) -> None:
    from agent_worktrees import worktrees_projection as projection

    record = _record(tmp_path / "owned.yaml")
    row = {"id": record.worktree_id, **record.machine_fields}
    row["machine_identity_version"] = version
    store = projection.WorktreesProjection(tmp_path / "current.sqlite3")
    if type(version) is int and version == 1:
        store.publish("project", [row], observed_at=1)
        assert store.read("project")["worktrees"] == [row]
    else:
        with pytest.raises(projection.ProjectionError, match="version"):
            store.publish("project", [row], observed_at=1)


@pytest.mark.parametrize("machine", ["desk", "guest-space"])
def test_pair_lookup_does_not_borrow_physical_host_equality(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, machine: str,
) -> None:
    record = _record(tmp_path / "owned.yaml")
    sibling_path = tmp_path / "knowledge" / "worktrees" / "sibling.yaml"
    sibling_path.parent.mkdir(parents=True)
    sibling = _record(sibling_path)
    tracking.save_record(sibling, sibling_path)
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    record.pair_ref = f"{machine}/knowledge/sibling"
    found = tracking.find_paired_record(record)
    assert (found is not None) is (machine == "guest-space")


@pytest.mark.parametrize("if_absent", [False, True])
def test_factory_tuple_is_explicit_and_existing_admission_is_not_reformatted(
    tmp_path: Path, if_absent: bool,
) -> None:
    identity = WorktreeMachineIdentity("desk-wsl", "wsl", "custom-guest")
    arguments = (
        "historical-wsl-wsl", "worktree/historical-wsl-wsl", str(tmp_path / "retained"),
        "project", identity.machine, identity.environment, tmp_path,
    )
    if if_absent:
        record, created = tracking.create_new_record_if_absent(*arguments, machine_identity=identity)
        assert created
        path = tmp_path / "historical-wsl-wsl.yaml"
        before = path.read_bytes()
        retained, created = tracking.create_new_record_if_absent(
            *arguments[:2], str(tmp_path / "new-unadmitted-path"),
            *arguments[3:], machine_identity=WorktreeMachineIdentity("other", "wsl", "other-wsl"),
        )
        assert not created
        assert retained.worktree_path == record.worktree_path
        assert retained.creation_nonce == record.creation_nonce
        assert retained.machine_identity == identity
        assert path.read_bytes() == before
    else:
        record = tracking.create_new_record(*arguments, machine_identity=identity)
    assert tracking.load_record(tmp_path / "historical-wsl-wsl.yaml").machine_identity == identity
    assert record.worktree_id == "historical-wsl-wsl"
    assert record.execution_machine == "custom-guest"


def test_delegate_graph_qualifies_same_host_records_by_execution_owner() -> None:
    from agent_worktrees import delegate_cli

    native = {
        "id": "same-id", "repo": "project", "status": "active",
        **WorktreeMachineIdentity("desk", "windows", "native-space").to_record_fields(),
    }
    guest = {
        "id": "same-id", "repo": "project", "status": "active",
        **WorktreeMachineIdentity("desk", "wsl", "guest-space").to_record_fields(),
    }
    child = {
        "id": "delegate", "repo": "project", "status": "active",
        "caller_worktree": "guest-space/project/same-id",
        **WorktreeMachineIdentity("desk", "wsl", "guest-space").to_record_fields(),
    }
    delegate_cli.annotate_delegate_graph([native, guest, child])
    assert child["caller_state"]["state"] == "resolved"
    assert child["caller_state"]["machine"] == "desk"
    assert child["caller_state"]["execution_machine"] == "guest-space"
    assert len(guest["delegates"]) == 1
    assert "delegates" not in native


@pytest.mark.parametrize("registry", ["missing", "empty", "unscoped"])
@pytest.mark.parametrize("versioned", [False, True])
def test_forced_tuple_cleanup_requires_receiving_registry_before_legacy_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, registry: str, versioned: bool,
) -> None:
    from agent_worktrees import cleanup_gc_cli

    if registry != "missing":
        (tmp_path / "machines.yaml").write_text(
            "machines: {}\n" if registry == "empty" else "machines:\n  guest-space: {}\n",
        )
    config = cfg.Config(
        srcroot=str(tmp_path), machine="guest-space", platform="wsl", repo_name="project",
        repos={"project": cfg.RepoConfig(
            anchor=str(tmp_path), worktree_root=str(tmp_path / "trees"),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_kw: config)
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "project_dir", lambda project: tmp_path / project)
    path = tmp_path / "project" / "worktrees" / "owned.yaml"
    path.parent.mkdir(parents=True)
    tracking.save_record(_record(path, versioned=versioned), path)
    before = path.read_bytes()
    monkeypatch.setattr(cleanup_gc_cli, "_hosted_session_blocks_cleanup", lambda _record: False)
    monkeypatch.setattr(cleanup_gc_cli.sessions, "scan_sessions_fast", lambda _records: None)
    monkeypatch.setattr(cleanup_gc_cli, "_build_active_paths", lambda *_a: set())
    reaped = []
    result = cleanup_gc_cli._revalidate_cleanup_safety(
        "owned", repo=config.default_repo, tracking_path=path.parent, force=True,
        reap=lambda record, info: reaped.append(record.worktree_id) or (0, []),
    )
    if versioned:
        assert not result.cleanable and result.bucket == "execution-space"
        assert reaped == []
    else:
        assert result.cleanable and result.reaped
        assert reaped == ["owned"]
    assert path.read_bytes() == before


def test_tuple_controller_navigation_targets_execution_owner(tmp_path: Path) -> None:
    from agent_worktrees import controller_lineage, reciprocal_presentation

    controller = _record(tmp_path / "controller.yaml")
    controller.sessions = [tracking.SessionEntry("controller-session", "2026-01-01T00:00:00")]
    child = _record(tmp_path / "child.yaml")
    child.controllers = [tracking.ControllerRelation(
        kind="session", source="explicit", relation_revision=1,
        created_at="2026-01-01T00:00:00",
        controller_ref="guest-space/project/controller#controller-session",
    )]
    findings = controller_lineage.controller_findings(
        child, record_loader=lambda project, worktree_id: controller,
        local_machine="guest-space",
    )
    assert findings[0]["status"] == "resolved"
    assert findings[0]["controller_machine"] == "guest-space"
    presentation = reciprocal_presentation.derive(child, findings)
    assert presentation["state"] == "controlled-elsewhere"
    assert presentation["actions"][0]["target"] == {
        "machine": "guest-space", "project": "project",
        "worktree_id": "controller", "session_id": "controller-session",
    }
