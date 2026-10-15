"""Tests for Phase 3 incremental settlement (settle_resource_claim + parent hook)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import obligations as ob
from agent_worktrees import tracking
from agent_worktrees.finalize import _settle_parent_obligation
from agent_worktrees.tracking import ResourceClaim, WorktreeRecord

# ── settle_resource_claim primitive ──────────────────────────────────────────

def _rec(tmp_path: Path, *claims: ResourceClaim) -> WorktreeRecord:
    rec = tracking.create_new_record(
        "wt-A", "worktree/wt-A", str(tmp_path / "wt-A"), "proj",
        "machine-x", "windows", tmp_path,
    )
    rec.resources = list(claims)
    return rec


def test_settle_flips_matching_claim_to_at_rest(tmp_path):
    rec = _rec(tmp_path, ResourceClaim(kind="codespace", ref="cs-1", state="active"))
    got = tracking.settle_resource_claim(rec, "cs-1", ob.AT_REST, save=False)
    assert got is not None and got.state == "at-rest"
    assert got.is_at_rest and not got.is_unsettled


def test_settle_to_released(tmp_path):
    rec = _rec(tmp_path, ResourceClaim(kind="worktree", ref="m/p/w", state="active"))
    got = tracking.settle_resource_claim(rec, "m/p/w", ob.RELEASED, save=False)
    assert got is not None and got.state == "released" and not got.is_live


def test_settle_unknown_ref_is_noop(tmp_path):
    rec = _rec(tmp_path, ResourceClaim(kind="codespace", ref="cs-1"))
    assert tracking.settle_resource_claim(rec, "nope", save=False) is None
    assert rec.resources[0].state == "active"  # untouched


def test_settle_normalizes_bad_disposition(tmp_path):
    rec = _rec(tmp_path, ResourceClaim(kind="codespace", ref="cs-1", state="active"))
    got = tracking.settle_resource_claim(rec, "cs-1", "bogus", save=False)
    assert got is not None and got.state == "active"  # normalized


def test_settle_persists_and_reloads(tmp_path):
    rec = _rec(tmp_path, ResourceClaim(kind="codespace", ref="cs-1", state="active"))
    path = tmp_path / "wt-A.yaml"
    tracking.settle_resource_claim(rec, "cs-1", ob.AT_REST, save=True, path=path)
    reloaded = tracking.load_record(path)
    assert reloaded.resources[0].state == "at-rest"


# ── _settle_parent_obligation hook ───────────────────────────────────────────

def _child(
    project_root: Path, owner_ref: str = "", *,
    machine: str = "machine-x", platform: str = "windows",
) -> WorktreeRecord:
    return tracking.create_new_record(
        "wt-child", "worktree/wt-child", str(project_root / "wt-child"),
        "childproj", machine, platform, project_root,
        owner_ref=owner_ref,
    )


def _write_parent(project_root: Path, project: str, parent_id: str,
                  child_ref: str, *, machine: str = "machine-x",
                  platform: str = "windows") -> Path:
    """Write a parent record (with an active claim on the child) to its tracking
    dir under ``~/.{project}/worktrees/``, returning the path."""
    wtdir = project_root / f".{project}" / "worktrees"
    wtdir.mkdir(parents=True, exist_ok=True)
    (project_root / parent_id).mkdir(exist_ok=True)
    parent = tracking.create_new_record(
        parent_id, f"worktree/{parent_id}", str(project_root / parent_id),
        project, machine, platform, wtdir.parent,
    )
    parent.resources = [ResourceClaim(kind="worktree", ref=child_ref, state="active")]
    path = wtdir / f"{parent_id}.yaml"
    tracking.save_record(parent, path)
    return path


@pytest.fixture
def _project_root(tmp_path, monkeypatch):
    """Redirect cfg.project_dir(name) into a temp home so cross-project parent
    paths resolve under the sandbox. The hook imports ``config as cfg`` locally,
    so patching the config module attribute covers it."""
    from agent_worktrees import config as cfg
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(cfg, "project_dir",
                        lambda name=None: tmp_path / f".{name or 'proj'}")
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda project, **_kwargs: cfg.Config(
            srcroot=str(tmp_path), repo_name=project,
            machine="machine-x-wsl" if cfg.detect_platform() == "wsl" else "machine-x",
            platform=cfg.detect_platform(),
            repos={project: cfg.RepoConfig(anchor=str(tmp_path), worktree_root=str(tmp_path))},
        ),
    )
    return tmp_path


@pytest.mark.parametrize("platform", ["windows", "linux"])
def test_parent_receiving_identity_cannot_authorize_a_different_caller(
    _project_root, monkeypatch, platform,
):
    from agent_worktrees import config as cfg, execution_spaces

    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_args, **_kwargs: {})
    child_ref = "host-a/childproj/wt-child"
    parent_path = _write_parent(
        _project_root, "parentproj", "wt-parent", child_ref,
        machine="host-b", platform=platform,
    )
    before = parent_path.read_bytes()
    receiving_config = cfg.Config(
        srcroot=str(_project_root), machine="host-b", platform=platform,
        repo_name="parentproj",
        repos={"parentproj": cfg.RepoConfig(
            anchor=str(_project_root), worktree_root=str(_project_root),
        )},
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda *_args, **_kwargs: receiving_config)
    child = _child(
        _project_root, "host-a/parentproj/wt-parent", machine="host-a", platform=platform,
    )
    caller = SimpleNamespace(machine="host-a", repo_name="childproj")
    with pytest.raises(execution_spaces.ExecutionSpaceError, match="caller is not local"):
        _settle_parent_obligation(child, caller, "wt-child")
    assert parent_path.read_bytes() == before
    assert tracking.load_record(parent_path).resources[0].state == "active"


@pytest.mark.parametrize("alias", ["Generated-Host", "Legacy-Alias", "Example Workstation"])
def test_caller_alias_uses_its_own_map_before_parent_receiving_check(_project_root, monkeypatch, alias):
    from agent_worktrees import config as cfg
    from machine_transport import MachineEntry

    local = MachineEntry(
        key="Example-Host", hostname="Generated-Host", alias="Legacy-Alias",
        display_name="Example Workstation",
    )
    native = MachineEntry(
        key="Example-Host", hostname="Generated-Host", display_name="Example Workstation",
        execution_platform="windows",
    )
    caller_anchor, parent_anchor = _project_root / "caller", _project_root / "parent"
    monkeypatch.setattr(
        cfg, "load_machines_yaml",
        lambda anchor, **_kwargs: {local.key: local} if str(anchor) == str(caller_anchor)
        else {native.key: native},
    )
    def repo(anchor: Path) -> cfg.RepoConfig:
        return cfg.RepoConfig(anchor=str(anchor), worktree_root=str(_project_root))
    caller = cfg.Config(
        srcroot=str(_project_root), machine=alias, platform="windows", repo_name="childproj",
        repos={"childproj": repo(caller_anchor)},
    )
    receiving = cfg.Config(
        srcroot=str(_project_root), machine="Example-Host", platform="windows", repo_name="parentproj",
        repos={"parentproj": repo(parent_anchor)},
    )
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda project, **_kwargs: caller if project == "childproj" else receiving,
    )
    child_ref = tracking.format_claim_ref(alias, "childproj", "wt-child")
    parent_path = _write_parent(
        _project_root, "parentproj", "wt-parent", child_ref, machine="Example-Host",
    )
    child = _child(_project_root, f"{alias}/parentproj/wt-parent", machine=alias)
    _settle_parent_obligation(child, caller, "wt-child")
    claim, = tracking.load_record(parent_path).resources
    assert (claim.ref, claim.state) == (child_ref, "at-rest")


@pytest.mark.guard
def test_parent_claim_settled_on_child_finalize(_project_root, monkeypatch):
    child_id = "wt-child"
    child_ref = tracking.format_claim_ref("machine-x", "childproj", child_id)
    parent_ref = tracking.format_claim_ref("machine-x", "parentproj", "wt-parent")
    parent_path = _write_parent(_project_root, "parentproj", "wt-parent", child_ref)

    child = _child(_project_root, parent_ref)
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")
    persisted_child = tracking.load_record(_project_root / "wt-child.yaml")
    assert persisted_child.owner_ref == parent_ref
    assert len(persisted_child.controllers) == 1
    assert persisted_child.controllers[0].controller_ref == parent_ref
    assert persisted_child.controllers[0].source == "owner-ref"

    _settle_parent_obligation(child, config, child_id)

    reloaded = tracking.load_record(parent_path)
    assert reloaded.resources[0].state == "at-rest"


@pytest.mark.guard
def test_cross_machine_parent_is_skipped(_project_root):
    child_ref = tracking.format_claim_ref("machine-x", "childproj", "wt-child")
    # Parent on a DIFFERENT machine -> hook must not touch it.
    parent_ref = tracking.format_claim_ref("other-machine", "parentproj", "wt-parent")
    parent_path = _write_parent(_project_root, "parentproj", "wt-parent", child_ref)

    child = _child(_project_root, parent_ref)
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")

    _settle_parent_obligation(child, config, "wt-child")

    assert tracking.load_record(parent_path).resources[0].state == "active"


@pytest.mark.guard
def test_no_owner_ref_is_noop(_project_root):
    child = _child(_project_root)
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")
    # Must simply not raise.
    _settle_parent_obligation(child, config, "wt-child")


@pytest.mark.guard
def test_missing_parent_record_is_noop(_project_root):
    parent_ref = tracking.format_claim_ref("machine-x", "parentproj", "ghost")
    child = _child(_project_root, parent_ref)
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")
    _settle_parent_obligation(child, config, "wt-child")  # no file -> no-op, no raise


def test_legacy_wsl_parent_claim_settled_using_legacy_child_ref(_project_root, monkeypatch):
    """A record-scoped legacy pre-split WSL parent (recorded under the bare
    native-host name) still settles its claim on this child -- and the
    parent's stored resource ref, itself the child's OLD pre-split spelling,
    is matched and settled WITHOUT being rewritten."""
    from agent_worktrees import config as cfg

    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")
    monkeypatch.setattr(cfg, "load_machines_yaml", lambda *_a, **_k: {})
    child_id = "wt-child"
    legacy_child_ref = tracking.format_claim_ref("machine-x", "childproj", child_id)
    parent_ref = tracking.format_claim_ref("machine-x", "parentproj", "wt-parent")
    parent_path = _write_parent(
        _project_root, "parentproj", "wt-parent", legacy_child_ref,
        machine="machine-x", platform="wsl",
    )

    child_dir = _project_root / ".childproj" / "worktrees"
    child_dir.mkdir(parents=True)
    (_project_root / child_id).mkdir()
    child = tracking.create_new_record(
        child_id, f"worktree/{child_id}", str(_project_root / child_id),
        "childproj", "machine-x", "wsl", child_dir, owner_ref=parent_ref,
    )
    config = cfg.load_project_config("childproj")

    _settle_parent_obligation(child, config, child_id)

    reloaded = tracking.load_record(parent_path)
    assert reloaded.resources[0].state == "at-rest"
    assert reloaded.resources[0].ref == legacy_child_ref  # spelling preserved
