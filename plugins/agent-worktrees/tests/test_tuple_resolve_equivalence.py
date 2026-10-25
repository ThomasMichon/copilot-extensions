"""Actual new/lookup/resume entry points agree with optional local naming."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
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


@pytest.fixture
def observed_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str, str, str], cfg.Config]:
    load_config = cfg.load_config

    def configure(platform: str, map_kind: str, identity_mode: str) -> cfg.Config:
        initial = _setup(tmp_path, monkeypatch, platform=platform)
        monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
        topology = Path(initial.default_repo.anchor) / "machines.yaml"
        entries = {
            "empty": {},
            "unrelated": {"peer-space": {
                "hostname": "other-host", "physical_host": "peer-host",
                "execution_platform": platform,
            }},
            "matching": {"local-space": {
                "hostname": "raw-host", "physical_host": "friendly-host",
                "execution_platform": platform,
            }},
            "unscoped": {"friendly-host": {"hostname": "raw-host"}},
            "sibling-only": {"sibling-space": {
                "hostname": "raw-host", "physical_host": "friendly-host",
                "execution_platform": "windows" if platform == "wsl" else "wsl",
            }},
        }
        if map_kind == "absent":
            topology.unlink()
        else:
            topology.write_text(yaml.safe_dump({"machines": entries[map_kind]}))
        monkeypatch.setattr(
            cfg, "project_dir", lambda name=None: tmp_path / (name or initial.repo_name),
        )
        monkeypatch.setattr(
            cfg, "tracking_dir", lambda name=None: cfg.project_dir(name) / "worktrees",
        )
        monkeypatch.setattr(cfg, "active_project", lambda: initial.repo_name)
        monkeypatch.setattr(cfg, "project_name", lambda: initial.repo_name)
        config_path = cfg.project_dir() / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(yaml.safe_dump({
            "repo_name": initial.repo_name, "repos": {initial.repo_name: {
                "anchor": initial.default_repo.anchor,
                "worktree_root": initial.default_repo.worktree_root,
            }},
        }))
        monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
        monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})

        def receiving_config(*_args, **_kwargs) -> cfg.Config:
            return load_config(
                config_path, project=initial.repo_name, include_control_plane_related_pr=False,
                include_knowledge_preferences=False,
            )

        config = receiving_config()
        assert config.configured_machine is None
        _stub_launch_plumbing(monkeypatch, config)
        monkeypatch.setattr(cfg, "load_config", receiving_config)
        monkeypatch.setattr(cfg, "load_project_config", receiving_config)
        if identity_mode == "tuple-v1":
            actual_core = m._create_worktree_core

            def opted_in(*args, **kwargs):
                return actual_core(*args, **kwargs, identity_mode="tuple-v1")

            monkeypatch.setattr(resolve_cli, "_create_worktree_core", opted_in)
        return config

    return configure


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("identity_mode", ["legacy", "tuple-v1"])
@pytest.mark.parametrize("map_kind", [
    "absent", "empty", "unrelated", "matching", "unscoped", "sibling-only",
])
@pytest.mark.parametrize("selector", ["default", "current", "observed"])
def test_actual_local_new_lookup_resume_without_enrollment(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, identity_mode: str, map_kind: str, selector: str,
) -> None:
    config = observed_config(platform, map_kind, identity_mode)
    machine = {"default": None, "current": config.machine, "observed": "raw-host"}[selector]
    code, created = _resolve(
        capfd, new_worktree=True, worktree_id=None, machine=machine,
    )
    assert code == 0, created
    assert created["launch"]["action"] == "exec"
    row = created["worktree"]
    path = cfg.tracking_dir() / f"{row['id']}.yaml"
    original = tracking._load_record_uncached(path)
    assert original.creation_nonce
    assert original.platform == platform
    if identity_mode == "legacy":
        assert original.machine == config.machine
        assert original.machine_identity is None
        assert "machine_identity_version" not in row and "execution_machine" not in row
    else:
        physical = "raw-host" if map_kind in ("absent", "empty", "unrelated") else "friendly-host"
        execution = "local-space" if map_kind == "matching" else physical + (
            "-wsl" if platform == "wsl" else ""
        )
        expected = WorktreeMachineIdentity(physical, platform, execution)
        assert original.machine_identity == expected
        assert {key: row[key] for key in expected.to_record_fields()} == expected.to_record_fields()
    before = path.read_bytes()
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("lookup/resume must not allocate"),
    )
    code, preview = _resolve(
        capfd, worktree_id=original.worktree_id[-4:], machine=machine, dry_run=True,
    )
    assert code == 0, preview
    assert preview["worktree"]["id"] == original.worktree_id
    assert preview["launch"]["preview"] is True
    assert path.read_bytes() == before
    code, resumed = _resolve(capfd, worktree_id=original.worktree_id, machine=machine)
    assert code == 0, resumed
    assert resumed["launch"]["action"] == "exec"
    assert resumed["launch"]["worktree_id"] == original.worktree_id
    assert resumed["launch"]["work_dir"] == original.worktree_path
    assert "--admission-only" in resumed["launch"]["cmd"]
    retained = tracking._load_record_uncached(path)
    assert retained.resume_count == original.resume_count + 1
    assert retained.creation_nonce == original.creation_nonce
    assert retained.machine_fields == original.machine_fields
    assert retained.branch == original.branch
    assert retained.worktree_path == original.worktree_path
    assert list(cfg.tracking_dir().glob("*.yaml")) == [path]
    assert not yaml.safe_load((cfg.project_dir() / "config.yaml").read_text()).get("machine")


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("identity_mode", ["legacy", "tuple-v1"])
@pytest.mark.parametrize("defect", ["platform", "project", "namespace"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_observed_selector_does_not_admit_foreign_records(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    tmp_path: Path, platform: str, identity_mode: str, defect: str, dry_run: bool,
) -> None:
    observed_config(platform, "unrelated", identity_mode)
    code, created = _resolve(capfd, new_worktree=True, worktree_id=None)
    assert code == 0, created
    worktree_id = created["worktree"]["id"]
    path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    raw = yaml.safe_load(path.read_text())
    if defect == "platform":
        raw["platform"] = "windows" if platform != "windows" else "wsl"
        if identity_mode == "tuple-v1":
            raw["environment"] = raw["platform"]
    elif defect == "project":
        raw["repo"] = "foreign-project"
    else:
        raw["worktree_path"] = str(tmp_path / "foreign-root" / worktree_id)
    path.write_text(yaml.safe_dump(raw))
    before = path.read_bytes()
    monkeypatch.setattr(
        m.git_ops, "create_worktree", lambda *_a: pytest.fail("foreign records must not allocate"),
    )
    code, result = _resolve(
        capfd, worktree_id=worktree_id[-4:], machine="raw-host", dry_run=dry_run,
    )
    assert code == 3, result
    assert result["error"]
    assert path.read_bytes() == before
    assert list(cfg.tracking_dir().glob("*.yaml")) == [path]


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("selector_kind", ["key", "alias", "conflicting-environment"])
@pytest.mark.parametrize("operation", ["new", "lookup", "resume"])
def test_observation_never_overrides_named_foreign_selection(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, selector_kind: str, operation: str,
) -> None:
    config = observed_config(platform, "empty", "tuple-v1")
    environment = None
    if selector_kind == "conflicting-environment":
        environment = "windows" if platform != "windows" else "wsl"
    else:
        _registry(config, {
            "raw-host" if selector_kind == "key" else "foreign-space": {
                "physical_host": "foreign-host",
                "execution_platform": "windows" if platform != "windows" else "wsl",
                **({"alias": "raw-host"} if selector_kind == "alias" else {}),
            },
        })
    monkeypatch.setattr(
        resolve_cli, "_create_worktree_core",
        lambda *_a, **_k: pytest.fail("foreign selection must not allocate"),
    )
    code, result = _resolve(
        capfd, new_worktree=operation == "new",
        worktree_id=None if operation == "new" else "missing-retained",
        machine="raw-host", environment=environment, dry_run=operation == "lookup",
    )
    assert code != 0, result
    assert result["error"]
    assert not list(cfg.tracking_dir().glob("*.yaml"))


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("identity_mode", ["legacy", "tuple-v1"])
@pytest.mark.parametrize("hostname", ["literal-host-wsl", "literal-host-win"])
def test_literal_observed_suffix_is_not_a_requested_environment(
    observed_config, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture,
    platform: str, identity_mode: str, hostname: str,
) -> None:
    observed_config(platform, "empty", identity_mode)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: hostname)
    code, created = _resolve(
        capfd, new_worktree=True, worktree_id=None, machine=hostname,
    )
    assert code == 0, created
    row = created["worktree"]
    path = cfg.tracking_dir() / f"{row['id']}.yaml"
    original = tracking._load_record_uncached(path)
    assert original.platform == platform
    if identity_mode == "tuple-v1":
        assert original.machine_identity == WorktreeMachineIdentity(
            hostname, platform, hostname + ("-wsl" if platform == "wsl" else ""),
        )
    before = path.read_bytes()
    code, preview = _resolve(
        capfd, worktree_id=row["id"][-4:], machine=hostname, dry_run=True,
    )
    assert code == 0, preview
    assert preview["launch"]["preview"] is True
    assert path.read_bytes() == before
    code, resumed = _resolve(capfd, worktree_id=row["id"], machine=hostname)
    assert code == 0, resumed
    assert resumed["launch"]["work_dir"] == original.worktree_path
    retained = tracking._load_record_uncached(path)
    assert retained.machine_fields == original.machine_fields
    assert retained.creation_nonce == original.creation_nonce


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
