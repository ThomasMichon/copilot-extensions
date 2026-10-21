"""Opt-in creation uses the registered tuple without changing legacy defaults."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg, local_cache_refresh, tracking
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


@pytest.mark.parametrize("defect", ["missing", "foreign-platform", "config-platform", "unknown-mode"])
def test_tuple_refusal_precedes_allocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, defect: str,
) -> None:
    config = _setup(tmp_path, monkeypatch)
    if defect == "missing":
        (Path(config.default_repo.anchor) / "machines.yaml").write_text("machines: {}\n")
    elif defect == "foreign-platform":
        monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    elif defect == "config-platform":
        config = replace(config, platform="windows")
    effects = []
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    with pytest.raises((ValueError, RuntimeError), match="identity|mode|platform|execution"):
        m._create_worktree_core(
            config, identity_mode="unsupported" if defect == "unknown-mode" else "tuple-v1",
            creation_intent=lambda _allocation: effects.append("reserve"),
        )
    assert effects == []
    assert not (tmp_path / "tracking").exists()


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
    ) -> dict:
        def checked_allocate(reserve: Callable[[dict], None]) -> dict:
            def checked_reserve(worktree: dict) -> None:
                effects.append("reserve")
                reserve(worktree)
            return allocate(checked_reserve)
        return original_create(config, key, facts, checked_allocate, validate_record=validate_record)

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
