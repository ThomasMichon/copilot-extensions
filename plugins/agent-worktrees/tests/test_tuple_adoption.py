"""Opt-in ledger producers preserve Git identity and existing creation winners."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from agent_worktrees import config as cfg, creation_identity, execution_spaces
from agent_worktrees import git_ops, tracking, worktree_identity
from agent_worktrees.worktree_machine_identity import WorktreeMachineIdentity


@pytest.fixture(params=["windows", "wsl", "linux"])
def adoption_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> cfg.Config:
    platform = request.param
    anchor = tmp_path / "anchor"
    git_ops.git("init", "-b", "master", str(anchor))
    git_ops.git("config", "user.email", "test@example.com", cwd=anchor)
    git_ops.git("config", "user.name", "Test", cwd=anchor)
    (anchor / "tracked.txt").write_text("retained\n")
    git_ops.git("add", "tracked.txt", cwd=anchor)
    git_ops.git("commit", "-m", "initial", cwd=anchor)
    (anchor / "machines.yaml").write_text(
        f"machines:\n  custom-space:\n    physical_host: desk-wsl\n"
        f"    execution_platform: {platform}\n",
    )
    config = cfg.Config(
        srcroot=str(tmp_path), repo_name="project", machine="custom-space", platform=platform,
        repos={"project": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(tmp_path / "configured-trees"),
        )},
    )
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    monkeypatch.setattr(cfg, "active_project", lambda: "project")
    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / (name or "project"))
    return config


def _produce(
    config: cfg.Config, kind: str, *, mode: creation_identity.IdentityMode = "tuple-v1",
) -> tracking.WorktreeRecord:
    if kind == "anchor":
        return tracking.load_or_create_anchor_record(
            config.default_repo.anchor, config.repo_name, config.machine, config.platform,
            cfg.tracking_dir(), identity_mode=mode,
        )
    root = Path(config.srcroot) / "external" / "desk-wsl-wsl-retained-01234567"
    if not root.exists():
        root.parent.mkdir()
        git_ops.git(
            "worktree", "add", str(root), "-b", "retained-external", "master",
            cwd=config.default_repo.anchor,
        )
    assert worktree_identity._adopt_linked_worktree(root, identity_mode=mode) == root.name
    return tracking.load_record(cfg.tracking_dir() / f"{root.name}.yaml")


@pytest.mark.parametrize("kind", ["anchor", "external"])
@pytest.mark.parametrize("mode", ["legacy", "tuple-v1"])
def test_tuple_adoption_producer_tracking_json_and_relationships(
    adoption_config: cfg.Config, kind: str, mode: creation_identity.IdentityMode,
) -> None:
    from agent_worktrees.__main__ import _worktree_to_dict
    from agent_worktrees import claims_owner

    record = _produce(adoption_config, kind, mode=mode)
    identity = WorktreeMachineIdentity("desk-wsl", adoption_config.platform, "custom-space")
    assert record.machine_identity == (identity if mode == "tuple-v1" else None)
    assert record.machine == (identity.machine if mode == "tuple-v1" else adoption_config.machine)
    assert record.execution_machine == "custom-space"
    assert record.platform == adoption_config.platform
    assert record.pair_kind == ("anchor" if kind == "anchor" else None)
    assert record.checkout_managed is (kind == "anchor")
    assert record.worktree_id == (
        "@anchor" if kind == "anchor" else "desk-wsl-wsl-retained-01234567"
    )
    assert record.branch == ("@anchor" if kind == "anchor" else "retained-external")
    assert Path(record.worktree_path) == (
        Path(adoption_config.default_repo.anchor) if kind == "anchor"
        else Path(adoption_config.srcroot) / "external" / record.worktree_id
    )
    controller_ref = f"{record.execution_machine}/project/controller#controller-session"
    relation = tracking.set_controller_relation(record, controller_ref=controller_ref)
    record = tracking.load_record(record.yaml_path)
    assert record.active_controllers[0].controller_ref == controller_ref
    assert record.active_controllers[0].relation_revision == relation.relation_revision
    assert record.head_session is None and record.sessions == []
    row = _worktree_to_dict(record)
    assert {key: row[key] for key in record.machine_fields} == record.machine_fields
    if mode == "legacy":
        assert "execution_machine" not in row and "machine_identity_version" not in row
    claim = tracking.ResourceClaim(kind="worktree", ref="custom-space/project/child")
    assert claims_owner._owner_dict("project", record, claim)["qualified_ref"] == (
        f"custom-space/project/{record.worktree_id}"
    )
    before = record.yaml_path.read_bytes()
    tracking.save_record(record)
    assert record.yaml_path.read_bytes() == before


@pytest.mark.parametrize("kind", ["anchor", "external"])
@pytest.mark.parametrize("map_kind,physical_name", [
    ("absent", "friendly-host"), ("empty", "friendly-host"), ("unrelated", "friendly-host"),
    ("matching", "friendly-host"), ("sibling-only", "friendly-host"),
    ("sibling-only", "friendly-wsl"), ("sibling-only", "friendly-win"),
])
def test_actual_tuple_adoption_without_map_enrollment(
    adoption_config: cfg.Config, monkeypatch: pytest.MonkeyPatch, kind: str,
    map_kind: str, physical_name: str,
) -> None:
    config = adoption_config
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "Observed-Host-win")
    topology = Path(config.default_repo.anchor) / "machines.yaml"
    maps = {
        "empty": {},
        "unrelated": {"peer-space": {
            "execution_platform": "wsl", "physical_host": "peer-host",
        }},
        "matching": {"local-space": {
            "hostname": "Observed-Host-win", "execution_platform": config.platform,
            "physical_host": physical_name,
        }},
        "sibling-only": {"sibling-space": {
            "hostname": "Observed-Host-win",
            "execution_platform": "windows" if config.platform == "wsl" else "wsl",
            "physical_host": physical_name,
        }},
    }
    if map_kind == "absent":
        topology.unlink()
    else:
        topology.write_text(yaml.safe_dump({"machines": maps[map_kind]}))
    config = replace(config, machine=cfg.detect_machine(config.default_repo.anchor))
    monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    record = _produce(config, kind)
    physical = physical_name if map_kind in ("matching", "sibling-only") else "observed-host-win"
    execution = "local-space" if map_kind == "matching" else physical + (
        "-wsl" if config.platform == "wsl" else ""
    )
    assert record.machine_identity == WorktreeMachineIdentity(physical, config.platform, execution)
    execution_spaces.require_record_mutation(record, config)
    if map_kind == "sibling-only":
        assert not execution_spaces.require_owner_identity("sibling-space", config)
    assert record.worktree_id == (
        "@anchor" if kind == "anchor" else "desk-wsl-wsl-retained-01234567"
    )
    before = record.yaml_path.read_bytes()
    assert _produce(config, kind).yaml_path.read_bytes() == before


def _retain_history(record: tracking.WorktreeRecord) -> bytes:
    record.title = "retained title"
    record.follow_up = True
    record.paused = True
    record.paused_revision = 1
    record.resume_count = 7
    record.sessions = [tracking.SessionEntry("retained-session", record.started_at)]
    record.head_session = "retained-session"
    record.resources = [tracking.ResourceClaim(kind="worktree", ref="custom-space/project/child")]
    record.controller_revision = 1
    record.controllers = [tracking.ControllerRelation(
        kind="worktree", source="owner-ref", relation_revision=1,
        created_at=record.started_at, controller_ref="custom-space/project/controller",
    )]
    tracking.save_record(record)
    before = record.yaml_path.read_bytes()
    assert b"follow_up: true" in before and b"paused: true" in before
    return before


@pytest.mark.parametrize("kind", ["anchor", "external"])
@pytest.mark.parametrize("first_mode", ["legacy", "tuple-v1"])
def test_tuple_adoption_existing_record_skips_new_mapping_and_preserves_history(
    adoption_config: cfg.Config, monkeypatch: pytest.MonkeyPatch, kind: str,
    first_mode: creation_identity.IdentityMode,
) -> None:
    original = _produce(adoption_config, kind, mode=first_mode)
    before = _retain_history(original)
    monkeypatch.setattr(
        creation_identity, "new_machine_identity",
        lambda *_a: pytest.fail("an existing record must not derive a fresh identity"),
    )
    (Path(adoption_config.default_repo.anchor) / "machines.yaml").write_text("machines: {}\n")
    retained = _produce(adoption_config, kind)
    assert retained.machine_fields == original.machine_fields
    assert retained.creation_nonce == original.creation_nonce
    assert retained.head_session == "retained-session"
    assert retained.title == "retained title" and retained.follow_up and retained.paused
    assert retained.yaml_path.read_bytes() == before


@pytest.mark.parametrize("kind", ["anchor", "external"])
def test_tuple_adoption_competing_if_absent_winner_is_not_upgraded(
    adoption_config: cfg.Config, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    real_create = tracking.create_new_record_if_absent
    winner_bytes: list[bytes] = []

    def competing_create(*args, **kwargs):
        winner_kwargs = dict(kwargs)
        winner_kwargs.pop("machine_identity_factory")
        winner, created = real_create(*args, **winner_kwargs)
        assert created and winner.machine_identity is None
        winner_bytes.append(_retain_history(winner))
        return real_create(*args, **kwargs)

    monkeypatch.setattr(tracking, "create_new_record_if_absent", competing_create)
    monkeypatch.setattr(
        creation_identity, "new_machine_identity",
        lambda *_a: pytest.fail("the losing creation must not derive a tuple"),
    )
    record = _produce(adoption_config, kind)
    assert record.machine_identity is None
    assert record.yaml_path.read_bytes() == winner_bytes[0]


@pytest.mark.parametrize("kind", ["anchor", "external"])
@pytest.mark.parametrize("defect", ["invalid-map", "foreign-platform", "config-platform", "namespace"])
def test_new_tuple_adoption_refuses_without_creating_a_record(
    adoption_config: cfg.Config, monkeypatch: pytest.MonkeyPatch, kind: str, defect: str,
) -> None:
    config = adoption_config
    if defect == "invalid-map":
        (Path(config.default_repo.anchor) / "machines.yaml").write_text(
            "machines:\n  custom-space:\n    execution_platform: invalid\n",
        )
    elif defect == "foreign-platform":
        monkeypatch.setattr(cfg, "detect_platform", lambda: (
            "linux" if config.platform != "linux" else "windows"
        ))
    elif defect == "config-platform":
        config = replace(config, platform="linux" if config.platform != "linux" else "windows")
        monkeypatch.setattr(cfg, "load_config", lambda *_a, **_k: config)
    else:
        actual_tracking_dir = cfg.tracking_dir
        monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: (
            actual_tracking_dir(name) if name is not None else Path(config.srcroot) / "foreign"
        ))
    with pytest.raises((execution_spaces.ExecutionSpaceError, ValueError), match=(
        "identity|namespace|platform|execution"
    )):
        _produce(config, kind)
    assert not list(cfg.tracking_dir().glob("*.yaml"))
    assert not list((Path(config.srcroot) / "project" / "worktrees").glob("*.yaml"))


@pytest.mark.parametrize("defect", ["repo", "machine", "platform", "anchor"])
def test_tuple_anchor_rejects_caller_namespace_not_receiving_config(
    adoption_config: cfg.Config, defect: str,
) -> None:
    arguments = {
        "anchor_path": adoption_config.default_repo.anchor,
        "repo": adoption_config.repo_name, "machine": adoption_config.machine,
        "platform_name": adoption_config.platform, "tracking_path": cfg.tracking_dir(),
    }
    arguments[{"platform": "platform_name", "anchor": "anchor_path"}.get(defect, defect)] = "foreign"
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="receiving project namespace"):
        tracking.load_or_create_anchor_record(**arguments, identity_mode="tuple-v1")
    assert not list(cfg.tracking_dir().glob("*.yaml"))


@pytest.mark.parametrize("defect", ["foreign-anchor", "detached", "collision"])
def test_tuple_external_adoption_keeps_git_and_record_authority(
    adoption_config: cfg.Config, defect: str,
) -> None:
    config = adoption_config
    root = Path(config.srcroot) / "linked"
    if defect == "foreign-anchor":
        anchor = Path(config.srcroot) / "other-anchor"
        git_ops.git("clone", config.default_repo.anchor, str(anchor))
    else:
        anchor = Path(config.default_repo.anchor)
    if defect == "detached":
        git_ops.git("worktree", "add", "--detach", str(root), "master", cwd=anchor)
    else:
        git_ops.git("worktree", "add", str(root), "-b", "linked", "master", cwd=anchor)
    before = None
    if defect == "collision":
        record = tracking.create_new_record(
            root.name, "other-branch", str(root.parent / "different-checkout"),
            config.repo_name, config.machine, config.platform, cfg.tracking_dir(),
        )
        before = _retain_history(record)
    assert worktree_identity._adopt_linked_worktree(root, identity_mode="tuple-v1") is None
    path = cfg.tracking_dir() / f"{root.name}.yaml"
    assert path.read_bytes() == before if before is not None else not path.exists()


@pytest.mark.parametrize("kind", ["anchor", "external"])
def test_unknown_adoption_mode_refuses_explicitly(
    adoption_config: cfg.Config, kind: str,
) -> None:
    with pytest.raises(ValueError, match="Unsupported creation identity mode"):
        _produce(adoption_config, kind, mode="unsupported")
    assert not list(cfg.tracking_dir().glob("*.yaml"))


@pytest.mark.parametrize("producer", ["anchor", "if-absent", "if-absent-tuple"])
def test_new_adoption_record_retains_supplied_destination_for_later_save(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, producer: str,
) -> None:
    default_project = tmp_path / "default-project"
    supplied = tmp_path / "supplied-tracking"
    monkeypatch.setattr(cfg, "project_dir", lambda *_a: default_project)
    if producer == "anchor":
        record = tracking.load_or_create_anchor_record(
            str(tmp_path / "anchor"), "project", "custom-space", "wsl", supplied,
        )
    else:
        identity = (
            WorktreeMachineIdentity("desk", "wsl", "custom-space")
            if producer == "if-absent-tuple" else None
        )
        record, created = tracking.create_new_record_if_absent(
            "external", "retained", str(tmp_path / "external"), "project",
            identity.machine if identity is not None else "custom-space", "wsl", supplied,
            machine_identity=identity,
        )
        assert created
    expected = supplied / f"{record.worktree_id}.yaml"
    assert record.yaml_path == expected
    record.title = "updated at supplied destination"
    tracking.save_record(record)
    assert tracking._load_record_uncached(expected).title == record.title
    assert not (default_project / "worktrees" / expected.name).exists()


def test_repo_discovery_git_probe_uses_shared_headless_kwargs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    install = tmp_path / "install"
    package = install / "agent_worktrees"
    package.mkdir(parents=True)
    anchor = tmp_path / "anchor"
    (anchor / ".git").mkdir(parents=True)
    monkeypatch.setattr(worktree_identity, "__file__", str(package / "worktree_identity.py"))
    monkeypatch.setattr(cfg, "install_dir", lambda: install)
    monkeypatch.setattr(
        worktree_identity, "no_window_kwargs", lambda: {"creationflags": 123}, raising=False,
    )
    monkeypatch.chdir(tmp_path)
    calls = []

    def probe(command, **kwargs):
        calls.append((command, kwargs))
        return worktree_identity.subprocess.CompletedProcess(command, 0, str(anchor), "")

    monkeypatch.setattr(worktree_identity.subprocess, "run", probe)
    assert worktree_identity._find_repo_dir() == anchor
    assert len(calls) == 1
    assert calls[0][1]["creationflags"] == 123
