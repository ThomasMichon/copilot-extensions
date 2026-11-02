"""Ordinary creation selects tuples without changing low-level legacy defaults."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import (
    config as cfg, create_destination, handoff_cli, repos, resolve_cli, resolve_launch_cli, tracking,
)
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_embody import _ns
from test_launch_preflight import _create_args
from test_resolve_seed_delivery import _args
from test_tuple_resolve_equivalence import observed_config as observed_config
from test_paired_carve import _state_root


@pytest.mark.parametrize("selectors", [False, True])
def test_local_bridge_cli_replay_refuses_changed_caller(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture, selectors: bool,
) -> None:
    config = observed_config("linux", "matching", "ordinary")
    args = _create_args(json_output=True)
    args.bridge, args.caller_worktree, args.creation_key = True, "caller-a", "caller-key"
    args.no_owner = True
    if selectors:
        args.repo = config.repo_name
        monkeypatch.setattr(create_destination, "_origin", lambda *a, **k: (config.repo_name, config, None))
        monkeypatch.setattr(
            create_destination, "_target",
            lambda *a: {"machine": "local-space", "project": config.repo_name},
        )
    assert m.cmd_create(args) == 0
    first = json.loads(capfd.readouterr().out)
    assert first["worktree"]["caller_worktree"] == "caller-a"
    assert m.cmd_create(args) == 0
    assert json.loads(capfd.readouterr().out)["worktree"] == first["worktree"]
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: pytest.fail("replay allocated a child"))
    args.caller_worktree = "caller-b"
    assert m.cmd_create(args) == 1
    failure = json.loads(capfd.readouterr().out)
    assert failure["creation"]["status"] == "conflict"


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind", [
    "absent", "empty", "unrelated", "matching", "unscoped", "sibling-only",
])
@pytest.mark.parametrize("system", [False, True])
def test_ordinary_create_selects_tuple_at_actual_entry(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, map_kind: str, system: bool,
) -> None:
    config = observed_config(platform, map_kind, "ordinary")
    args = _create_args(json_output=True, system=system)
    args.creation_key = "ordinary-entry"
    assert m.cmd_create(args) == 0
    result = json.loads(capfd.readouterr().out)
    row = result["worktree"]
    record = tracking.load_record(cfg.tracking_dir() / f"{row['id']}.yaml")
    physical = "raw-host" if map_kind in ("absent", "empty", "unrelated") else "friendly-host"
    execution = "local-space" if map_kind == "matching" else physical + (
        "-wsl" if platform == "wsl" else ""
    )
    expected = WorktreeMachineIdentity(physical, platform, execution)
    assert record.machine_identity == expected
    assert {key: row[key] for key in expected.to_record_fields()} == expected.to_record_fields()
    prefix = "sys-service-" if system else physical + (
        "-win-" if platform == "windows" else f"-{platform}-"
    )
    assert row["id"].startswith(prefix)
    assert record.kind == ("system" if system else "session")
    assert record.repo == config.repo_name
    before = record.yaml_path.read_bytes()
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("replay must not allocate"),
    )
    assert m.cmd_create(args) == 0
    assert json.loads(capfd.readouterr().out)["worktree"] == row
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_omitted_core_mode_remains_legacy(
    observed_config, capfd: pytest.CaptureFixture, platform: str,
) -> None:
    config = observed_config(platform, "matching", "ordinary")
    row = m._create_worktree_core(
        config, build_launch_plan=False, creation_key="low-level-control",
    )["worktree"]
    record = tracking.load_record(cfg.tracking_dir() / f"{row['id']}.yaml")
    assert record.machine == config.machine
    assert record.machine_identity is None
    assert "machine_identity_version" not in row and "execution_machine" not in row


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("map_kind", [
    "absent", "empty", "unrelated", "matching", "unscoped", "sibling-only",
])
@pytest.mark.parametrize("surface", ["json", "bridge", "interactive", "embody"])
def test_ordinary_new_selects_tuple_without_test_mode_injection(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, map_kind: str, surface: str,
) -> None:
    config = observed_config(platform, map_kind, "ordinary")
    args = _args(
        new_worktree=True, worktree_id=None, dry_run=False, defer_projections=True,
        bridge=surface == "bridge",
    )
    preflights = []
    preflight = m._preflight_launch

    def observe_preflight(config, args, path, **kwargs):
        preflights.append(path)
        return preflight(config, args, path, **kwargs)

    monkeypatch.setattr(m, "_preflight_launch", observe_preflight)
    if surface == "interactive":
        plans = []
        monkeypatch.setattr(resolve_launch_cli, "_emit_plan", plans.append)
        args.dry_run = True
        assert resolve_launch_cli._resolve_new(config, args) == 0
        assert not list(cfg.tracking_dir().glob("*.yaml"))
        args.dry_run = False
        assert resolve_launch_cli._resolve_new(config, args) == 0
        assert plans[-1]["action"] == "exec"
    elif surface == "embody":
        monkeypatch.setattr(m.sessions, "has_mux_session", lambda _id: False)
        assert handoff_cli.cmd_embody(_ns(new=True, dry_run=True)) == 0
        assert json.loads(capfd.readouterr().out)["ok"]
    else:
        assert resolve_cli.cmd_resolve(args) == 0
        assert json.loads(capfd.readouterr().out)["launch"]["action"] == "exec"
    paths = list(cfg.tracking_dir().glob("*.yaml"))
    assert len(paths) == 1
    record = tracking.load_record(paths[0])
    physical = "raw-host" if map_kind in ("absent", "empty", "unrelated") else "friendly-host"
    execution = "local-space" if map_kind == "matching" else physical + (
        "-wsl" if platform == "wsl" else ""
    )
    assert record.machine_identity == WorktreeMachineIdentity(physical, platform, execution)
    assert record.kind == ("bridge" if surface == "bridge" else "session")
    prefix = physical + ("-win-" if platform == "windows" else f"-{platform}-")
    assert record.worktree_id.startswith(prefix)
    if surface == "interactive":
        assert len(preflights) >= 2
        assert all(Path(path).name.startswith(prefix) for path in preflights)


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("surface", ["create", "json", "interactive", "embody"])
@pytest.mark.parametrize("defect", ["malformed", "ambiguous", "conflicting-platform"])
def test_ordinary_new_refuses_before_effects(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, surface: str, defect: str,
) -> None:
    config = observed_config(platform, "empty", "ordinary")
    topology = Path(config.default_repo.anchor) / "machines.yaml"
    if defect == "malformed":
        topology.write_text("machines: [\n")
    elif defect == "ambiguous":
        topology.write_text(
            "machines:\n"
            "  first: {hostname: raw-host, physical_host: first-host}\n"
            "  second: {hostname: raw-host, physical_host: second-host}\n",
        )
    else:
        config = replace(config, platform="linux" if platform != "linux" else "windows")
        monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    effects = []
    monkeypatch.setattr(m.git_ops, "create_worktree", lambda *_a: effects.append("git"))
    if surface == "create":
        code = m.cmd_create(_create_args(json_output=True))
    elif surface == "json":
        code = resolve_cli.cmd_resolve(_args(new_worktree=True, worktree_id=None))
    elif surface == "embody":
        code = handoff_cli.cmd_embody(_ns(new=True, dry_run=True))
    else:
        plans = []
        monkeypatch.setattr(resolve_launch_cli, "_emit_plan", plans.append)
        code = resolve_launch_cli._resolve_new(config, _args(dry_run=True))
        assert plans[-1]["action"] == "error" and plans[-1]["error"]
    assert code != 0
    assert capfd.readouterr().out
    assert effects == []
    assert not list(cfg.tracking_dir().glob("*.yaml"))


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_ordinary_create_carries_selected_tuple_to_actual_knowledge_pair(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    tmp_path: Path, platform: str,
) -> None:
    config = observed_config(platform, "matching", "ordinary")
    knowledge_anchor = tmp_path / "knowledge"
    knowledge_anchor.mkdir()
    monkeypatch.setattr(
        m.state_root_mod, "resolve_state_root",
        lambda _config: _state_root(path=str(knowledge_anchor), repo="knowledge"),
    )
    monkeypatch.setattr(repos, "find_repo", lambda _name: repos.RepoEntry(
        name="knowledge", repo_class="worktree", remote="https://example.com/knowledge.git",
    ))
    monkeypatch.setattr(m.git_ops, "resolve_remote_name", lambda *_a, **_k: "origin")
    assert m.cmd_create(_create_args(json_output=True)) == 0
    row = json.loads(capfd.readouterr().out)["worktree"]
    harness = tracking.load_record(cfg.tracking_dir() / f"{row['id']}.yaml")
    knowledge = tracking.load_record(cfg.tracking_dir("knowledge") / f"{row['id']}-k.yaml")
    expected = WorktreeMachineIdentity("friendly-host", platform, "local-space")
    assert harness.machine_identity == knowledge.machine_identity == expected
    assert harness.pair_id == knowledge.pair_id
    assert harness.pair_ref == f"local-space/knowledge/{knowledge.worktree_id}"
    assert knowledge.pair_ref == f"local-space/{config.repo_name}/{harness.worktree_id}"
    assert knowledge.owner_ref == knowledge.pair_ref


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("hostname", ["literal-host-wsl", "literal-host-win"])
def test_ordinary_preview_preserves_literal_physical_suffix(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, hostname: str,
) -> None:
    config = observed_config(platform, "empty", "ordinary")
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    config = cfg.load_config()
    paths = []
    preflight = m._preflight_launch

    def observe(config, args, path, **kwargs):
        paths.append(Path(path).name)
        return preflight(config, args, path, **kwargs)

    monkeypatch.setattr(m, "_preflight_launch", observe)
    monkeypatch.setattr(resolve_launch_cli, "_emit_plan", lambda _plan: None)
    assert resolve_launch_cli._resolve_new(config, _args(dry_run=True)) == 0
    assert resolve_launch_cli._resolve_new(config, _args(dry_run=False)) == 0
    prefix = hostname + ("-win-" if platform == "windows" else f"-{platform}-")
    assert paths and all(name.startswith(prefix) for name in paths)
    record = tracking.load_record(next(cfg.tracking_dir().glob("*.yaml")))
    assert record.machine == hostname
    assert record.worktree_id.startswith(prefix)


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("system", [False, True])
def test_ordinary_create_replays_completed_legacy_key(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, system: bool,
) -> None:
    config = observed_config(platform, "matching", "ordinary")
    original = m._create_worktree_core(
        config, no_mux=True, kind="system" if system else "session",
        owner="service" if system else None, name="service" if system else None,
        inherit_parent_session=False, creation_key="retained-key", build_launch_plan=False,
    )["worktree"]
    path = cfg.tracking_dir() / f"{original['id']}.yaml"
    before = path.read_bytes()
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("old key must not allocate"),
    )
    args = _create_args(json_output=True, system=system)
    args.creation_key = "retained-key"
    capfd.readouterr()
    assert m.cmd_create(args) == 0
    assert json.loads(capfd.readouterr().out)["worktree"] == original
    assert path.read_bytes() == before
    assert tracking.load_record(path).machine_identity is None


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
def test_ordinary_dispatch_creation_stamps_selected_execution_owner(
    observed_config, capfd: pytest.CaptureFixture, platform: str,
) -> None:
    observed_config(platform, "matching", "ordinary")
    args = _create_args(json_output=True)
    args.creation_key = "dispatch-key"
    args.dispatch_task_id = "task"
    args.dispatch_reservation_key = "reservation"
    args.dispatch_attempt = 1
    args.dispatch_driver = "driver"
    args.dispatch_supervisor = "supervisor"
    assert m.cmd_create(args) == 0
    row = json.loads(capfd.readouterr().out)["worktree"]
    record = tracking.load_record(cfg.tracking_dir() / f"{row['id']}.yaml")
    assert record.machine == "friendly-host" and record.execution_machine == "local-space"
    assert record.dispatch_attempt.creator_machine == "local-space"
