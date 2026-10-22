"""Actual new/lookup/resume entry points share declared one-term selection."""

from __future__ import annotations

import base64
from collections.abc import Mapping
import json
from pathlib import Path

import pytest
import yaml

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg, resolve_cli, tracking
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity
from test_resolve_seed_delivery import _args, _stub_launch_plumbing
from test_tuple_creation import _setup


@pytest.fixture(params=["windows", "wsl", "linux"])
def equivalence_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> cfg.Config:
    config = _setup(
        tmp_path, monkeypatch, physical="desk", platform=request.param, execution="custom-space",
    )
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / (name or config.repo_name))
    monkeypatch.setattr(
        cfg, "tracking_dir",
        lambda name=None: cfg.project_dir(name or config.repo_name) / "worktrees",
    )
    monkeypatch.setattr(cfg, "active_project", lambda: config.repo_name)
    monkeypatch.setattr(cfg, "project_name", lambda: config.repo_name)
    _stub_launch_plumbing(monkeypatch, config)
    foreign_platform = "windows" if config.platform != "windows" else "wsl"
    _registry(config, {
        "custom-space": {
            "physical_host": "desk", "execution_platform": config.platform,
            "alias": "route-alias", "hostname": "generated-host",
            # Linux has no inferred legacy suffix; this spelling is declared explicitly.
            "compatibility_aliases": ["desk-linux"] if config.platform == "linux" else [],
            "ssh": {"environments": [{"name": config.platform, "alias": "local-route"}]},
        },
        "other-space": {
            "physical_host": "desk", "execution_platform": foreign_platform,
            "ssh": {"environments": [{"name": foreign_platform, "alias": "other-route"}]},
        },
    })
    actual_core = m._create_worktree_core

    def opted_in(*args, **kwargs):
        return actual_core(*args, **kwargs, identity_mode="tuple-v1")

    monkeypatch.setattr(resolve_cli, "_create_worktree_core", opted_in)
    return config


def _registry(config: cfg.Config, entries: Mapping[str, object]) -> None:
    (Path(config.default_repo.anchor) / "machines.yaml").write_text(
        yaml.safe_dump({"machines": entries}),
    )


def _entries(config: cfg.Config) -> dict[str, dict[str, object]]:
    return yaml.safe_load(
        (Path(config.default_repo.anchor) / "machines.yaml").read_text(),
    )["machines"]


def _selection(config: cfg.Config, form: str) -> tuple[str, str | None]:
    suffix = "win" if config.platform == "windows" else config.platform
    return {
        "shorthand": (f"desk-{suffix}", None),
        "physical": ("desk", config.platform),
        "custom": ("custom-space", None),
        "alias": ("route-alias", None),
    }[form]


def _resolve(capfd: pytest.CaptureFixture, **kwargs) -> tuple[int, dict]:
    result = resolve_cli.cmd_resolve(_args(defer_projections=True, **kwargs))
    return result, json.loads(capfd.readouterr().out)


@pytest.mark.parametrize("form", ["shorthand", "physical", "custom", "alias"])
def test_actual_tuple_new_lookup_and_resume_agree(
    equivalence_config: cfg.Config, capfd: pytest.CaptureFixture, form: str,
) -> None:
    config = equivalence_config
    machine, environment = _selection(config, form)
    code, created = _resolve(
        capfd, new_worktree=True, worktree_id=None, machine=machine, environment=environment,
    )
    assert code == 0, created
    worktree_id = created["worktree"]["id"]
    expected = WorktreeMachineIdentity("desk", config.platform, "custom-space")
    assert {key: created["worktree"][key] for key in expected.to_record_fields()} == (
        expected.to_record_fields()
    )
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    original = tracking.load_record(path)
    original_bytes = path.read_bytes()
    nonce = original.creation_nonce

    # A preview performs actual record lookup and authority checks without resuming it.
    code, preview = _resolve(
        capfd, worktree_id=worktree_id[-4:], machine=machine, environment=environment, dry_run=True,
    )
    assert code == 0, preview
    assert preview["worktree"]["id"] == worktree_id
    assert preview["launch"]["preview"] is True
    assert path.read_bytes() == original_bytes

    code, resumed = _resolve(
        capfd, worktree_id=worktree_id, machine=machine, environment=environment,
    )
    assert code == 0, resumed
    assert resumed["launch"]["action"] == "exec"
    assert resumed["launch"]["work_dir"] == original.worktree_path
    assert resumed["launch"]["worktree_id"] == worktree_id
    assert "--admission-only" in resumed["launch"]["cmd"]
    assert {key: resumed["worktree"][key] for key in expected.to_record_fields()} == (
        expected.to_record_fields()
    )
    retained = tracking._load_record_uncached(path)
    assert retained.creation_nonce == nonce
    assert retained.resume_count == original.resume_count + 1
    assert retained.branch == original.branch
    assert retained.worktree_path == original.worktree_path
    assert retained.machine_fields == original.machine_fields
    assert list(cfg.tracking_dir().glob("*.yaml")) == [path]


@pytest.mark.parametrize("operation", ["new", "lookup", "resume"])
@pytest.mark.parametrize("defect", ["ambiguous-alias", "conflicting-environment"])
def test_actual_tuple_entry_points_refuse_conflicting_selection_before_effects(
    equivalence_config: cfg.Config, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture, operation: str, defect: str,
) -> None:
    config = equivalence_config
    code, created = _resolve(capfd, new_worktree=True, worktree_id=None)
    assert code == 0
    worktree_id = created["worktree"]["id"]
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    before = path.read_bytes()
    entries = _entries(config)
    if defect == "ambiguous-alias":
        entries["competing-space"] = {
            "physical_host": "elsewhere", "execution_platform": config.platform,
            "alias": "route-alias",
        }
        _registry(config, entries)
        machine, environment = "route-alias", None
    else:
        machine = "custom-space"
        environment = "windows" if config.platform != "windows" else "wsl"
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *_a, **_k: pytest.fail("a conflicting selection must not allocate"),
    )
    code, result = _resolve(
        capfd, new_worktree=operation == "new",
        worktree_id=None if operation == "new" else worktree_id,
        dry_run=operation == "lookup", machine=machine, environment=environment,
    )
    assert code != 0
    assert "ambiguous" in result["error"].lower() or "conflict" in result["error"].lower()
    assert path.read_bytes() == before
    assert list(cfg.tracking_dir().glob("*.yaml")) == [path]


@pytest.mark.parametrize("operation", ["new", "lookup", "resume"])
def test_actual_tuple_entry_points_keep_exact_keys_and_reject_declared_alias_collisions(
    equivalence_config: cfg.Config, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture, operation: str,
) -> None:
    config = equivalence_config
    entries = _entries(config)
    shorthand, _ = _selection(config, "shorthand")
    entries[shorthand] = {
        "physical_host": "elsewhere", "execution_platform": config.platform,
        "ssh": {"environments": [{"name": config.platform, "alias": "exact-route"}]},
    }
    _registry(config, entries)
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *_a, **_k: pytest.fail("an exact foreign key must not allocate locally"),
    )
    code, plan = _resolve(
        capfd, new_worktree=operation == "new",
        worktree_id=None if operation == "new" else "retained-worktree",
        dry_run=operation == "lookup", machine=shorthand,
    )
    if config.platform == "linux":
        assert code != 0
        assert "topology identity" in plan["error"] and "ambiguous" in plan["error"]
        assert not list(cfg.tracking_dir().glob("*.yaml"))
        return
    assert code == 0, plan
    assert plan["action"] == "remote"
    assert plan["machine"] == shorthand and plan["ssh_alias"] == "exact-route"
    command = plan["remote_command"]
    if config.platform == "windows":
        command = base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16le")
    assert "--new" in command if operation == "new" else (
        "--worktree-id" in command and "retained-worktree" in command
    )
    assert not list(cfg.tracking_dir().glob("*.yaml"))


@pytest.mark.parametrize("dry_run", [False, True])
def test_actual_tuple_resume_cannot_borrow_same_physical_hosts_other_environment(
    equivalence_config: cfg.Config, capfd: pytest.CaptureFixture, dry_run: bool,
) -> None:
    config = equivalence_config
    foreign_platform = "windows" if config.platform != "windows" else "wsl"
    identity = WorktreeMachineIdentity("desk", foreign_platform, "other-space")
    record = tracking.create_new_record(
        "retained", "retained-branch", str(Path(config.srcroot) / "retained"),
        config.repo_name, identity.machine, identity.environment, cfg.tracking_dir(),
        machine_identity=identity,
    )
    before = record.yaml_path.read_bytes()
    code, result = _resolve(
        capfd, worktree_id=record.worktree_id, machine="custom-space", dry_run=dry_run,
    )
    assert code == 3
    assert "different execution space" in result["error"]
    assert record.yaml_path.read_bytes() == before


def test_unregistered_linux_suffix_is_not_execution_authority(
    equivalence_config: cfg.Config, monkeypatch: pytest.MonkeyPatch,
    capfd: pytest.CaptureFixture,
) -> None:
    config = equivalence_config
    entries = _entries(config)
    entries["custom-space"]["compatibility_aliases"] = []
    _registry(config, entries)
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *_a, **_k: pytest.fail("an undeclared suffix must not allocate locally"),
    )
    code, result = _resolve(
        capfd, new_worktree=True, worktree_id=None, machine="desk-linux",
    )
    assert code != 0
    assert "unknown or unreachable" in result["error"]
    assert not list(cfg.tracking_dir().glob("*.yaml"))
