"""Opt-in creation uses the registered tuple without changing legacy defaults."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest
import yaml

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg, execution_spaces, local_cache_refresh, tracking
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_worktree_creation_seed import _create_config, _stub_create_worktree_core_internals


def _setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, physical: str = "desk",
    platform: str = "wsl", execution: str = "guest-space",
) -> cfg.Config:
    config = replace(_create_config(tmp_path), machine=execution, platform=platform)
    anchor = Path(config.default_repo.anchor)
    (anchor / "machines.yaml").write_text(
        f"machines:\n  {execution}:\n    physical_host: {json.dumps(physical)}\n"
        f"    execution_platform: {platform}\n",
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg, "tracking_dir", lambda _name=None: tmp_path / "tracking")
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    monkeypatch.setattr(m.permissions, "ensure_extension_permission_approvals", lambda *_a: False)
    monkeypatch.setattr(local_cache_refresh, "prepare_for_launch", lambda *_a: None)

    def create_checkout(_anchor: str, path: str, _branch: str, _start: str) -> None:
        Path(path).mkdir()

    monkeypatch.setattr(m.git_ops, "create_worktree", create_checkout)
    return config


@pytest.mark.parametrize("kind", ["session", "bridge", "system"])
@pytest.mark.parametrize("physical,platform,execution,prefix", [
    ("desk", "windows", "native-space", "desk-win"),
    ("desk", "wsl", "guest-space", "desk-wsl"),
    ("desk", "linux", "linux-space", "desk-linux"),
    ("desk-wsl", "wsl", "custom-guest", "desk-wsl-wsl"),
    ("desk-win", "windows", "custom-native", "desk-win-win"),
])
def test_actual_tuple_producer_roundtrips_record_and_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
    physical: str, platform: str, execution: str, prefix: str,
) -> None:
    config = _setup(tmp_path, monkeypatch, physical=physical, platform=platform, execution=execution)
    result = m._create_worktree_core(
        config, kind=kind, name="service", identity_mode="tuple-v1",
        creation_intent=lambda _allocation: None,
    )
    row = result["worktree"]
    assert row["id"].startswith("sys-service-" if kind == "system" else f"{prefix}-")
    record = tracking.load_record(tmp_path / "tracking" / f"{row['id']}.yaml")
    expected = WorktreeMachineIdentity(physical, platform, execution)
    assert record.machine_identity == expected
    assert record.machine == physical and record.platform == platform
    assert record.execution_machine == execution
    assert {key: row[key] for key in expected.to_record_fields()} == expected.to_record_fields()
    before = record.yaml_path.read_bytes()
    tracking.save_record(record)
    assert record.yaml_path.read_bytes() == before


def test_ordinary_producer_stays_legacy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _setup(tmp_path, monkeypatch)
    row = m._create_worktree_core(config, creation_intent=lambda _allocation: None)["worktree"]
    record = tracking.load_record(tmp_path / "tracking" / f"{row['id']}.yaml")
    assert record.machine == config.machine
    assert record.machine_identity is None
    assert "machine_identity_version" not in row and "execution_machine" not in row


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("owned", [False, True])
def test_ordinary_sibling_only_creation_remains_mutable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, owned: bool,
) -> None:
    from agent_worktrees import config_authority, obligations, tracking_session_registration_write

    load_config = cfg.load_config
    config = _setup(tmp_path, monkeypatch, platform=platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    sibling = "windows" if platform == "wsl" else "wsl"
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(
        "machines:\n  sibling-space:\n    hostname: raw-host\n"
        f"    physical_host: friendly-host\n    execution_platform: {sibling}\n",
    )
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "repo_name": config.repo_name, "repos": {config.repo_name: {
            "anchor": config.default_repo.anchor,
            "worktree_root": config.default_repo.worktree_root,
        }},
    }))
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})
    config = load_config(
        path, project=config.repo_name, include_control_plane_related_pr=False,
        include_knowledge_preferences=False,
    )
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / "state")
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir() / "worktrees")
    cfg.project_dir().mkdir()
    (cfg.project_dir() / "config.yaml").write_text(path.read_text())
    monkeypatch.setattr(config_authority, "_load_state_root_authority", lambda *_a: ({}, ""))
    owner_ref = None
    if owned:
        parent_checkout = Path(config.default_repo.worktree_root) / "parent"
        parent_checkout.mkdir(parents=True)
        parent = tracking.create_new_record(
            "parent", "worktree/parent", str(parent_checkout), config.repo_name,
            config.machine, platform, cfg.tracking_dir(),
        )
        owner_ref = f"{config.machine}/{config.repo_name}/parent"
        monkeypatch.setattr(
            m, "_coordination_readiness_for_owner_ref", lambda *_a: SimpleNamespace(ready=True),
        )
    row = m._create_worktree_core(
        config, creation_key="ordinary-request", owner_ref=owner_ref,
    )["worktree"]
    record = tracking.load_record(cfg.tracking_dir() / f"{row['id']}.yaml")
    assert record.machine_identity is None
    assert record.machine == config.machine
    before = record.yaml_path.read_bytes()
    execution_spaces.require_record_mutation(record, config)
    replayed = m._create_worktree_core(
        config, creation_key="ordinary-request", owner_ref=owner_ref,
    )
    assert replayed["worktree"] == row
    assert record.yaml_path.read_bytes() == before
    registered = tracking_session_registration_write.apply_session_register({
        "worktree_id": record.worktree_id, "yaml_path": str(record.yaml_path),
        "session_id": "ordinary-session",
    })
    assert registered == {"ok": True, "linked_handoff": None}
    assert tracking.load_record(record.yaml_path).session_entry("ordinary-session") is not None
    before = record.yaml_path.read_bytes()
    for changes in (
        {"machine": "sibling-space"}, {"platform": sibling},
        {"repo": "foreign-project"}, {"worktree_path": str(tmp_path / "unproven")},
    ):
        try:
            execution_spaces.require_record_mutation(replace(record, **changes), config)
        except execution_spaces.ExecutionSpaceError:
            pass
        else:
            pytest.fail(f"accepted foreign record changes: {changes!r}")
        assert record.yaml_path.read_bytes() == before
    if owned:
        warnings = []
        execution_spaces.settle_parent_obligation(
            tracking.load_record(record.yaml_path), config, record.worktree_id,
            output=SimpleNamespace(ok=lambda _message: None, warn=warnings.append),
        )
        assert warnings == []
        assert tracking.load_record(parent.yaml_path).resources[0].state == obligations.AT_REST


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_tuple_child_does_not_borrow_legacy_parent_proof(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    from agent_worktrees.legacy_wsl_record_locality import resolve_legacy_wsl_owner_ref

    config = _setup(tmp_path, monkeypatch, platform=platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    topology = Path(config.default_repo.anchor) / "machines.yaml"
    if platform == "wsl":
        topology.unlink()
    else:
        topology.write_text(
            "machines:\n  sibling-space:\n    hostname: raw-host\n"
            "    physical_host: friendly-host\n    execution_platform: wsl\n",
        )
    legacy_owner = "raw-host"
    config = replace(
        config, machine=f"{legacy_owner}-wsl" if platform == "wsl" else legacy_owner,
    )
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / "state")
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir() / "worktrees")
    monkeypatch.setattr(
        m, "_coordination_readiness_for_owner_ref", lambda *_a: SimpleNamespace(ready=True),
    )
    parent_checkout = Path(config.default_repo.worktree_root) / "parent"
    parent_checkout.mkdir(parents=True)
    parent = tracking.create_new_record(
        "parent", "worktree/parent", str(parent_checkout), config.repo_name,
        legacy_owner, platform, cfg.tracking_dir(),
    )
    owner_ref = f"{legacy_owner}/{config.repo_name}/parent"
    assert resolve_legacy_wsl_owner_ref(tracking.parse_claim_ref(owner_ref), config) == parent.yaml_path
    before = parent.yaml_path.read_bytes()
    effects = []
    carve = m.git_ops.create_worktree

    def create_checkout(*args: str) -> None:
        effects.append("git")
        carve(*args)

    monkeypatch.setattr(m.git_ops, "create_worktree", create_checkout)
    with pytest.raises(RuntimeError, match="cross-space|physical-host"):
        m._create_worktree_core(
            config, identity_mode="tuple-v1",
            owner_ref=owner_ref,
            creation_intent=lambda _allocation: effects.append("reserve"),
        )
    assert effects == []
    assert parent.yaml_path.read_bytes() == before
    assert [path.name for path in cfg.tracking_dir().glob("*.yaml")] == ["parent.yaml"]


@pytest.mark.parametrize("defect", [
    "invalid-map", "foreign-platform", "config-platform", "config-platform-unmapped", "unknown-mode",
])
def test_tuple_refusal_precedes_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    if defect == "invalid-map":
        (Path(config.default_repo.anchor) / "machines.yaml").write_text(
            "machines:\n  guest-space: {execution_platform: windows}\n",
        )
    elif defect == "foreign-platform":
        monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    elif defect in ("config-platform", "config-platform-unmapped"):
        config = replace(config, platform="windows")
        if defect == "config-platform-unmapped":
            (Path(config.default_repo.anchor) / "machines.yaml").unlink()
    effects = []
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    with pytest.raises((ValueError, RuntimeError), match="identity|mode|platform|execution"):
        m._create_worktree_core(
            config, identity_mode="unsupported" if defect == "unknown-mode" else "tuple-v1",
            creation_intent=lambda _allocation: effects.append("reserve"),
        )
    assert effects == []
    assert not (tmp_path / "tracking").exists()


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind,physical_name", [
    ("absent", "friendly-host"), ("empty", "friendly-host"), ("unrelated", "friendly-host"),
    ("matching", "friendly-host"), ("sibling-only", "friendly-host"),
    ("sibling-only", "friendly-wsl"), ("sibling-only", "friendly-win"),
])
@pytest.mark.parametrize("kind", ["session", "bridge", "system"])
def test_actual_local_tuple_creation_without_map_enrollment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, map_kind: str, physical_name: str, kind: str,
) -> None:
    config = _setup(tmp_path, monkeypatch, platform=platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host-wsl")
    topology = Path(config.default_repo.anchor) / "machines.yaml"
    maps = {
        "empty": {},
        "unrelated": {"peer-space": {
            "execution_platform": "linux", "physical_host": "peer-host",
        }},
        "matching": {"local-space": {
            "hostname": "Observed-Host-wsl", "execution_platform": platform,
            "physical_host": physical_name,
        }},
        "sibling-only": {"sibling-space": {
            "hostname": "Observed-Host-wsl",
            "execution_platform": "windows" if platform == "wsl" else "wsl",
            "physical_host": physical_name,
        }},
    }
    if map_kind == "absent":
        topology.unlink()
    else:
        topology.write_text(yaml.safe_dump({"machines": maps[map_kind]}))
    config = replace(config, machine=cfg.detect_machine(config.default_repo.anchor))
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    physical = physical_name if map_kind in ("matching", "sibling-only") else "observed-host-wsl"
    execution = "local-space" if map_kind == "matching" else physical + (
        "-wsl" if platform == "wsl" else ""
    )
    assert execution_spaces.require_owner_identity(execution, config)
    if map_kind == "sibling-only":
        assert not execution_spaces.require_owner_identity("sibling-space", config)
    result = m._create_worktree_core(
        config, kind=kind, name="service", identity_mode="tuple-v1",
        creation_key="local-request",
    )
    row = result["worktree"]
    record = tracking.load_record(tmp_path / "tracking" / f"{row['id']}.yaml")
    assert record.machine_identity == WorktreeMachineIdentity(physical, platform, execution)
    assert {key: row[key] for key in record.machine_fields} == record.machine_fields
    before = record.yaml_path.read_bytes()
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("replay must not allocate"),
    )
    replayed = m._create_worktree_core(
        config, kind=kind, name="service", identity_mode="legacy",
        creation_key="local-request",
    )
    assert replayed["worktree"] == row
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_owned_tuple_creation_journals_and_settles_its_execution_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str,
) -> None:
    from agent_worktrees import creation_identity, obligations

    config = _setup(tmp_path, monkeypatch, platform=platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(
        "machines:\n  sibling-space:\n    hostname: raw-host\n"
        "    physical_host: friendly-host\n    execution_platform: wsl\n",
    )
    config = replace(config, machine="raw-host")
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config)
    monkeypatch.setattr(
        cfg, "project_dir", lambda name=None: tmp_path / "state" / (name or config.repo_name),
    )
    monkeypatch.setattr(
        cfg, "tracking_dir", lambda name=None: cfg.project_dir(name) / "worktrees",
    )
    monkeypatch.setattr(
        m, "_coordination_readiness_for_owner_ref", lambda *_a: SimpleNamespace(ready=True),
    )
    identity = creation_identity.new_machine_identity(config, "tuple-v1")
    parent = tracking.create_new_record(
        "parent", "worktree/parent", str(tmp_path / "worktrees" / "parent"),
        config.repo_name, identity.machine, platform, cfg.tracking_dir(),
        machine_identity=identity,
    )
    owner_ref = f"{identity.execution_machine}/{config.repo_name}/parent"
    row = m._create_worktree_core(
        config, owner_ref=owner_ref, identity_mode="tuple-v1",
        creation_intent=lambda _allocation: None,
    )["worktree"]
    child = tracking.load_record(cfg.tracking_dir() / f"{row['id']}.yaml")
    saved = tracking.load_record(parent.yaml_path)
    assert saved.resources[0].ref == (
        f"{child.execution_machine}/{config.repo_name}/{child.worktree_id}"
    )
    assert saved.resources[0].state == obligations.ACTIVE
    warnings = []
    execution_spaces.settle_parent_obligation(
        child, config, child.worktree_id,
        output=SimpleNamespace(ok=lambda _message: None, warn=warnings.append),
    )
    assert warnings == []
    assert tracking.load_record(parent.yaml_path).resources[0].state == obligations.AT_REST


def test_changed_tuple_refuses_before_reserving_or_carving(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    effects = []

    def change_mapping(*_args: object, **_kwargs: object) -> SimpleNamespace:
        (Path(config.default_repo.anchor) / "machines.yaml").write_text(
            "machines:\n  guest-space: {execution_platform: wsl, physical_host: elsewhere}\n",
        )
        return SimpleNamespace(start_point="HEAD")

    monkeypatch.setattr(m, "_prepare_worktree_source", change_mapping)
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    with pytest.raises(RuntimeError, match="machine identity changed"):
        m._create_worktree_core(
            config, identity_mode="tuple-v1",
            creation_intent=lambda _allocation: effects.append("reserve"),
        )
    assert effects == []
    assert not list((tmp_path / "tracking").glob("*.yaml"))


@pytest.mark.parametrize("first_mode,replay_mode", [
    ("legacy", "tuple-v1"), ("tuple-v1", "legacy"), ("tuple-v1", "tuple-v1"),
])
def test_creation_key_mode_change_replays_original_representation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_mode: str, replay_mode: str,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    first = m._create_worktree_core(config, creation_key="request", identity_mode=first_mode)
    path = tmp_path / "tracking" / f"{first['worktree']['id']}.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(
        m.git_ops, "create_worktree",
        lambda *_a: pytest.fail("completed creation key must not allocate"),
    )
    replayed = m._create_worktree_core(config, creation_key="request", identity_mode=replay_mode)
    assert replayed["worktree"] == first["worktree"]
    assert replayed["creation"]["replayed"] is True
    assert path.read_bytes() == before


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("first_mode", ["legacy", "tuple-v1"])
@pytest.mark.parametrize("scoped", [False, True])
@pytest.mark.parametrize("historical_receipt", [False, True])
@pytest.mark.parametrize("initial_customization", [False, True])
def test_completed_key_replays_after_optional_customization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    platform: str, first_mode: str, scoped: bool, historical_receipt: bool,
    initial_customization: bool,
) -> None:
    from agent_worktrees import creation_identity

    load_config = cfg.load_config
    initial = _setup(tmp_path, monkeypatch, platform=platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    topology = Path(initial.default_repo.anchor) / "machines.yaml"
    topology.write_text(yaml.safe_dump({"machines": {
        "original-space": {
            "hostname": "raw-host", "physical_host": "original-host",
            **({"execution_platform": platform} if scoped else {}),
        },
    } if initial_customization else {}}))
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({
        "repo_name": initial.repo_name, "repos": {initial.repo_name: {
            "anchor": initial.default_repo.anchor,
            "worktree_root": initial.default_repo.worktree_root,
        }},
    }))
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})

    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / "state")
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(name) / "worktrees")

    def receiving_config() -> cfg.Config:
        return load_config(
            path, project=initial.repo_name, include_control_plane_related_pr=False,
            include_knowledge_preferences=False,
        )

    config = receiving_config()
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: receiving_config())
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: receiving_config())
    original_identity = creation_identity.new_machine_identity(config, "tuple-v1")
    first = m._create_worktree_core(config, creation_key="request", identity_mode=first_mode)
    record_path = cfg.tracking_dir() / f"{first['worktree']['id']}.yaml"
    before = record_path.read_bytes()
    journal = cfg.project_dir() / "workspace-creation.sqlite3"
    with sqlite3.connect(journal) as connection:
        if historical_receipt:
            receipt = json.loads(connection.execute("SELECT worktree FROM creations").fetchone()[0])
            del receipt["execution_origin"]
            connection.execute("UPDATE creations SET worktree=?", (json.dumps(receipt),))
        admitted = connection.execute("SELECT * FROM creations").fetchall()
    entry = {"hostname": "raw-host", "physical_host": "friendly-host"}
    if scoped:
        entry["execution_platform"] = platform
    topology.write_text(yaml.safe_dump({"machines": {"local-space": entry}}))
    renamed = receiving_config()
    assert creation_identity.new_machine_identity(renamed, "tuple-v1") != original_identity
    monkeypatch.setattr(
        m.git_ops, "create_worktree",
        lambda *_a: pytest.fail("retained replay must not carve a new checkout"),
    )
    if historical_receipt and initial_customization and (
        first_mode == "tuple-v1" or scoped or platform == "wsl"
    ):
        with pytest.raises((ValueError, RuntimeError)):
            m._create_worktree_core(renamed, creation_key="request", identity_mode=first_mode)
    else:
        replayed = m._create_worktree_core(
            renamed, creation_key="request", identity_mode=first_mode,
        )
        assert replayed["worktree"] == first["worktree"]
        assert replayed["creation"]["replayed"] is True
    assert record_path.read_bytes() == before
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT * FROM creations").fetchall() == admitted
    foreign_configs = [
        replace(renamed, machine="foreign-space", configured_machine="foreign-space"),
        replace(renamed, repos={renamed.repo_name: replace(
            renamed.default_repo, worktree_root=str(tmp_path / "foreign"),
        )}),
    ]
    if first_mode == "tuple-v1":
        foreign_configs.append(replace(
            renamed, platform="windows" if platform != "windows" else "wsl",
        ))
    for foreign in foreign_configs:
        with pytest.raises((ValueError, RuntimeError)):
            m._create_worktree_core(foreign, creation_key="request", identity_mode=first_mode)
        assert record_path.read_bytes() == before
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "foreign-host")
    with pytest.raises((ValueError, RuntimeError)):
        m._create_worktree_core(renamed, creation_key="request", identity_mode=first_mode)
    assert record_path.read_bytes() == before
    with sqlite3.connect(journal) as connection:
        assert connection.execute("SELECT * FROM creations").fetchall() == admitted


@pytest.mark.parametrize("actual,override", [
    ("windows", "wsl"), ("windows", "linux"), ("wsl", "windows"),
    ("wsl", "linux"), ("linux", "windows"), ("linux", "wsl"),
])
@pytest.mark.parametrize("historical_receipt", [False, True])
def test_completed_legacy_key_preserves_unchanged_platform_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    actual: str, override: str, historical_receipt: bool,
) -> None:
    config = replace(_setup(tmp_path, monkeypatch, platform=actual), machine="raw-host", platform=override)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(
        "machines:\n  peer-space:\n    physical_host: peer\n    execution_platform: windows\n",
    )
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "load_project_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / "state")
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: cfg.project_dir(name) / "worktrees")
    first = m._create_worktree_core(config, creation_key="request")
    record_path = cfg.tracking_dir() / f"{first['worktree']['id']}.yaml"
    assert tracking.load_record(record_path).platform == actual
    journal = cfg.project_dir() / "workspace-creation.sqlite3"
    with sqlite3.connect(journal) as connection:
        if historical_receipt:
            receipt = json.loads(connection.execute("SELECT worktree FROM creations").fetchone()[0])
            del receipt["execution_origin"]
            connection.execute("UPDATE creations SET worktree=?", (json.dumps(receipt),))
    before = record_path.read_bytes(), journal.read_bytes()
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("must not carve"))
    replayed = m._create_worktree_core(config, creation_key="request")
    assert replayed["worktree"] == first["worktree"]
    assert replayed["creation"]["replayed"] is True
    assert (record_path.read_bytes(), journal.read_bytes()) == before


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("hostname", "foreign-host"), ("machine", "foreign-host"),
    ("environment", "windows"), ("execution_machine", "foreign-space"),
    ("configured_machine", "foreign-space"), ("unexpected", True),
])
def test_completed_key_origin_conflict_never_reallocates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    first = m._create_worktree_core(config, creation_key="request", identity_mode="tuple-v1")
    journal = tmp_path / "workspace-creation.sqlite3"
    with sqlite3.connect(journal) as connection:
        receipt = json.loads(connection.execute("SELECT worktree FROM creations").fetchone()[0])
        receipt["execution_origin"][field] = value
        connection.execute("UPDATE creations SET worktree=?", (json.dumps(receipt),))
    record_path = tmp_path / "tracking" / f"{first['worktree']['id']}.yaml"
    before = record_path.read_bytes(), journal.read_bytes()
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("must not reallocate"))
    with pytest.raises((ValueError, RuntimeError), match="origin"):
        m._create_worktree_core(config, creation_key="request", identity_mode="tuple-v1")
    assert (record_path.read_bytes(), journal.read_bytes()) == before


def test_core_passes_admitted_tuple_to_pair_producer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    calls = []

    def pair(_config: cfg.Config, **kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(m, "_carve_paired_knowledge", pair)
    row = m._create_worktree_core(
        config, identity_mode="tuple-v1", creation_intent=lambda _allocation: None,
    )["worktree"]
    assert len(calls) == 1
    assert calls[0]["harness_id"] == row["id"]
    assert calls[0]["machine_identity"] == WorktreeMachineIdentity("desk", "wsl", "guest-space")


def test_tuple_refuses_partial_allocation_without_reformatting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    effects = []
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    with pytest.raises(ValueError, match="complete admitted allocation"):
        m._create_worktree_core(
            config, identity_mode="tuple-v1", allocation=("20261021-010203", "abcd"),
            creation_intent=lambda _allocation: effects.append("reserve"),
        )
    assert effects == []
    assert not (tmp_path / "tracking").exists()


@pytest.mark.parametrize("first_mode", ["legacy", "tuple-v1"])
def test_completed_key_cannot_replay_into_sibling_execution_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, first_mode: str,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    first = m._create_worktree_core(config, creation_key="request", identity_mode=first_mode)
    path = tmp_path / "tracking" / f"{first['worktree']['id']}.yaml"
    before = path.read_bytes()
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(
        "machines:\n"
        "  guest-space: {physical_host: desk, execution_platform: wsl}\n"
        "  native-space: {physical_host: desk, execution_platform: windows}\n",
    )
    native = replace(config, machine="native-space", platform="windows")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("replay must not allocate"),
    )
    with pytest.raises(RuntimeError, match="execution|platform"):
        m._create_worktree_core(native, creation_key="request", identity_mode="tuple-v1")
    assert path.read_bytes() == before


@pytest.mark.parametrize("physical", [
    "rack/desk", r"rack\desk", "D:desk", "rack..desk", ".desk", "rack@{desk}",
    "rack desk", "rack~desk", "rack^desk", "rack?desk", "rack*desk", "rack[desk",
    'rack"desk', "rack<desk", "rack>desk", "rack|desk", "CON.desk",
])
@pytest.mark.parametrize("kind", ["session", "bridge"])
@pytest.mark.parametrize("keyed", [False, True])
def test_invalid_physical_component_refuses_before_reservation_or_carve(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, physical: str, kind: str, keyed: bool,
) -> None:
    from agent_worktrees import creation_requests

    config = _setup(tmp_path, monkeypatch, physical=physical)
    effects = []
    original_create = creation_requests.create

    def create(
        config: cfg.Config, key: str, facts: dict,
        allocate: Callable[[Callable[[dict], None]], dict], *,
        validate_record: Callable[[tracking.WorktreeRecord], None] | None = None,
        validate_replay: Callable[[tracking.WorktreeRecord, dict], None] | None = None,
    ) -> dict:
        def checked_allocate(reserve: Callable[[dict], None]) -> dict:
            def checked_reserve(worktree: dict) -> None:
                effects.append("reserve")
                reserve(worktree)
            return allocate(checked_reserve)
        return original_create(
            config, key, facts, checked_allocate,
            validate_record=validate_record, validate_replay=validate_replay,
        )

    monkeypatch.setattr(creation_requests, "create", create)
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    with pytest.raises(ValueError, match="physical machine.*path"):
        m._create_worktree_core(
            config, kind=kind, identity_mode="tuple-v1",
            creation_key="request" if keyed else None,
            creation_intent=None if keyed else lambda _allocation: effects.append("reserve"),
        )
    assert effects == []
    assert not (tmp_path / "worktrees").exists()
    assert not list((tmp_path / "tracking").rglob("*.yaml"))
    if keyed:
        with sqlite3.connect(tmp_path / "workspace-creation.sqlite3") as connection:
            assert connection.execute("SELECT status, worktree FROM creations").fetchall() == [
                ("refused", None),
            ]
