"""Tests for the Phase 3c reciprocal owner-claim journaling on worktree carve.

`_journal_owner_reciprocal_claim` writes the forward half of the owner<->child
resource link (a `worktree` claim on the owner) so the owner's finalize gate sees
the obligation and the child's finalize can settle it.
"""

from __future__ import annotations

import types
import json
from pathlib import Path
import sys

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import state_root
from agent_worktrees import tracking


@pytest.fixture(autouse=True)
def _execution_platform(monkeypatch):
    monkeypatch.setattr(cfg, "detect_platform", lambda: "wsl")


def _seed_owner(tmp_path, monkeypatch, *, machine="anomalous-potato", project="example-web",
                owner_id="wt-owner", platform="wsl"):
    """Create an owner record under project_dir(project)/worktrees and point
    project_dir at a scratch tree."""
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    owner_dir = tmp_path / f".{project}" / "worktrees"
    owner_dir.mkdir(parents=True, exist_ok=True)
    (owner_dir.parent / "config.yaml").write_text(json.dumps({
        "machine": machine, "platform": platform, "repo_name": project,
        "repos": {project: {"anchor": str(tmp_path)}},
    }), encoding="utf-8")
    wdir = tmp_path / owner_id
    wdir.mkdir(exist_ok=True)
    tracking.create_new_record(
        owner_id, f"worktree/{owner_id}", str(wdir), project,
        machine, platform, owner_dir,
    )
    return owner_dir


def _config(machine="anomalous-potato", repo_name="copilot-extensions"):
    return types.SimpleNamespace(machine=machine, repo_name=repo_name)


def test_journals_worktree_claim_on_same_machine_owner(tmp_path, monkeypatch):
    owner_dir = _seed_owner(tmp_path, monkeypatch)
    ok = m._journal_owner_reciprocal_claim(
        _config(), "wt-child", "anomalous-potato/example-web/wt-owner")
    assert ok is True
    rec = tracking.load_record(owner_dir / "wt-owner.yaml")
    assert len(rec.resources) == 1
    c = rec.resources[0]
    assert c.kind == "worktree"
    assert c.ref == "anomalous-potato/copilot-extensions/wt-child"  # child's qualified ref
    assert c.is_unsettled  # active


def test_cross_machine_owner_defers_no_write(tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    ok = m._journal_owner_reciprocal_claim(
        _config(machine="anomalous-potato"), "wt-child",
        "other-box/example-web/wt-owner")
    assert ok is False  # cross-machine -> deferred to the lease mirror


def test_no_owner_ref_is_noop(tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    assert m._journal_owner_reciprocal_claim(_config(), "wt-child", None) is False
    assert m._journal_owner_reciprocal_claim(_config(), "wt-child", "") is False


def test_missing_owner_record_is_safe_noop(tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    ok = m._journal_owner_reciprocal_claim(
        _config(), "wt-child", "anomalous-potato/example-web/no-such-owner")
    assert ok is False  # resolved path doesn't exist -> no write, no raise


def test_idempotent_dedups_by_ref(tmp_path, monkeypatch):
    owner_dir = _seed_owner(tmp_path, monkeypatch)
    ref = "anomalous-potato/example-web/wt-owner"
    m._journal_owner_reciprocal_claim(_config(), "wt-child", ref)
    m._journal_owner_reciprocal_claim(_config(), "wt-child", ref)
    rec = tracking.load_record(owner_dir / "wt-owner.yaml")
    assert len(rec.resources) == 1  # deduped, not doubled


@pytest.mark.parametrize("platform", ["windows", "wsl", "linux"])
@pytest.mark.parametrize("receiving_mode", ["unmapped", "same-scoped", "different-scoped"])
def test_receiving_parent_only_accepts_child_owner_that_can_settle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, platform: str, receiving_mode: str,
) -> None:
    from agent_worktrees import creation_identity, execution_spaces, obligations

    monkeypatch.setattr(cfg, "detect_platform", lambda: platform)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "raw-host")
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / "state" / (name or "child-project"))
    monkeypatch.setattr(cfg, "global_config_path", lambda: tmp_path / "missing.yaml")
    monkeypatch.setattr(cfg, "_load_inrepo_config", lambda _anchor: {})
    configs = {}
    for project in ("child-project", "parent-project"):
        anchor = tmp_path / project
        anchor.mkdir()
        data: dict[str, object] = {
            "repo_name": project,
            "repos": {project: {"anchor": str(anchor)}},
        }
        if receiving_mode == "same-scoped" or (
            receiving_mode == "different-scoped" and project == "parent-project"
        ):
            data["machine"] = "selected-space"
            (anchor / "machines.yaml").write_text(
                "machines:\n  selected-space:\n    physical_host: raw-host\n"
                f"    execution_platform: {platform}\n",
            )
        path = cfg.project_dir(project) / "config.yaml"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(data))
        configs[project] = cfg.load_config(
            path, project=project, include_control_plane_related_pr=False,
            include_knowledge_preferences=False,
        )
    monkeypatch.setattr(
        cfg, "load_project_config", lambda name, **_kwargs: configs[name],
    )
    parent_config = configs["parent-project"]
    parent_identity = creation_identity.new_machine_identity(parent_config, "tuple-v1")
    parent = tracking.create_new_record(
        "parent", "worktree/parent", str(Path(parent_config.default_repo.worktree_root) / "parent"),
        parent_config.repo_name, parent_identity.machine, platform,
        cfg.tracking_dir("parent-project"), machine_identity=parent_identity,
    )
    before = parent.yaml_path.read_bytes()
    child_config = configs["child-project"]
    child_identity = creation_identity.new_machine_identity(child_config, "tuple-v1")
    owner_ref = f"{child_identity.execution_machine}/parent-project/parent"
    accepted = m._journal_owner_reciprocal_claim(
        child_config, "child", owner_ref,
        machine_identity=child_identity,
    )
    if receiving_mode == "different-scoped":
        assert accepted is False
        assert parent.yaml_path.read_bytes() == before
        return
    assert accepted is True
    child = tracking.create_new_record(
        "child", "worktree/child", str(Path(child_config.default_repo.worktree_root) / "child"),
        child_config.repo_name, child_identity.machine, platform,
        cfg.tracking_dir("child-project"), machine_identity=child_identity,
        owner_ref=owner_ref,
    )
    warnings = []
    execution_spaces.settle_parent_obligation(
        child, child_config, "child",
        output=types.SimpleNamespace(ok=lambda _message: None, warn=warnings.append),
    )
    assert warnings == []
    saved = tracking.load_record(parent.yaml_path)
    assert saved.resources[0].ref == f"{child_identity.execution_machine}/child-project/child"
    assert saved.resources[0].state == obligations.AT_REST


def test_owned_create_rejects_unready_before_source_or_worktree_side_effects(
    tmp_path,
    monkeypatch,
):
    owner_dir = _seed_owner(
        tmp_path,
        monkeypatch,
        project="owner-project",
        platform="windows",
    )
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    owner_path = owner_dir / "wt-owner.yaml"
    before = owner_path.read_bytes()
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    worktree_root = tmp_path / "worktrees"
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="anomalous-potato",
        platform="windows",
        repo_name="child-project",
        repos={
            "child-project": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(worktree_root),
            )
        },
    )
    monkeypatch.setattr(cfg, "load_project_config", lambda name, **kwargs: config)
    root = state_root.StateRoot(
        None,
        "knowledge_repo",
        "",
        True,
        True,
        False,
        error="no knowledge_repo is bound",
    )
    monkeypatch.setattr(
        m.state_root_mod,
        "coordination_readiness",
        lambda loaded: state_root.CoordinationReadiness(
            False,
            "knowledge_binding_required",
            root,
            error="bind the knowledge repository",
        ),
    )
    monkeypatch.setattr(
        m,
        "_prepare_worktree_source",
        lambda *a, **k: pytest.fail("unready create prepared the source"),
    )
    monkeypatch.setattr(
        m.git_ops,
        "create_worktree",
        lambda *a, **k: pytest.fail("unready create made a Git worktree"),
    )

    with pytest.raises(m.CoordinationReadinessFailure) as caught:
        m._create_worktree_core(
            config,
            no_mux=True,
            owner_ref="anomalous-potato/owner-project/wt-owner",
            launch_preflight=m.LaunchPreflight(),
        )
    assert caught.value.readiness.code == "knowledge_binding_required"
    assert not worktree_root.exists()
    assert owner_path.read_bytes() == before


def test_ownerless_create_reaches_source_preparation_when_unready(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    anchor = tmp_path / "anchor"
    anchor.mkdir()
    config = cfg.Config(
        srcroot=str(tmp_path),
        machine="anomalous-potato",
        platform="windows",
        repo_name="child-project",
        repos={
            "child-project": cfg.RepoConfig(
                anchor=str(anchor),
                worktree_root=str(tmp_path / "worktrees"),
            )
        },
    )
    monkeypatch.setattr(
        m,
        "_coordination_readiness_for_owner_ref",
        lambda *a, **k: pytest.fail(
            "owner-less creation evaluated owner coordination"
        ),
    )

    class ReachedSourcePreparation(Exception):
        pass

    monkeypatch.setattr(
        m,
        "_prepare_worktree_source",
        lambda *a, **k: (_ for _ in ()).throw(
            ReachedSourcePreparation()
        ),
    )
    with pytest.raises(ReachedSourcePreparation):
        m._create_worktree_core(
            config,
            no_mux=True,
            owner_ref=None,
            launch_preflight=m.LaunchPreflight(),
        )


class _FailingCreationNotice:
    def __init__(self, fail):
        self.fail = fail

    def write(self, text):
        if self.fail(text):
            raise OSError(19, "diagnostic-sink-private-marker secret-value-marker", "private-diagnostic-path")
        return len(text)

    def flush(self):
        pass


def _creation_activity(tmp_path, monkeypatch, *, available=True):
    from agent_worktrees import activity

    path = tmp_path / "creation-activity.jsonl"
    if not available:
        path.mkdir()
    monkeypatch.setattr(activity, "log_path", lambda: path)
    monkeypatch.setattr(activity, "_maybe_prune", lambda _path: None)
    return path, activity.log_event_failure_count()


def _assert_creation_diagnostic(path, *, errno):
    events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert any(
        event.get("error_type") == "OSError"
        and event.get("os_errno", event.get("errno")) == errno
        for event in events
    )
    text = path.read_text(encoding="utf-8")
    assert "diagnostic-sink-private-marker" not in text
    assert "storage-private-marker" not in text
    assert "secret-value-marker" not in text
    assert "private-diagnostic-path" not in text
    assert "private-storage-path" not in text


@pytest.mark.guard
@pytest.mark.parametrize("logger_available", [True, False])
def test_persisted_owner_claim_survives_advisory_stderr_failure(
    tmp_path, monkeypatch, capsys, logger_available,
):
    from agent_worktrees import activity

    owner_dir = _seed_owner(tmp_path, monkeypatch)
    path, failures = _creation_activity(tmp_path, monkeypatch, available=logger_available)
    monkeypatch.setattr(sys, "stderr", _FailingCreationNotice(lambda _text: True))

    accepted = m._journal_owner_reciprocal_claim(
        _config(), "wt-child", "anomalous-potato/example-web/wt-owner",
    )

    assert accepted is True
    owner = tracking.load_record(owner_dir / "wt-owner.yaml")
    assert len(owner.resources) == 1
    assert owner.resources[0].ref == "anomalous-potato/copilot-extensions/wt-child"
    assert owner.resources[0].is_unsettled
    assert capsys.readouterr().out == ""
    if logger_available:
        _assert_creation_diagnostic(path, errno=19)
        assert activity.log_event_failure_count() == failures
    else:
        assert activity.log_event_failure_count() > failures


@pytest.mark.guard
def test_owner_claim_persistence_failure_is_not_masked_by_advisory_stderr(
    tmp_path, monkeypatch, capsys,
):
    owner_dir = _seed_owner(tmp_path, monkeypatch)
    owner_path = owner_dir / "wt-owner.yaml"
    before = owner_path.read_bytes()
    path, _ = _creation_activity(tmp_path, monkeypatch)

    def fail_save(record, target=None, **kwargs):
        assert target == owner_path
        raise OSError(28, "storage-private-marker", "private-storage-path")

    monkeypatch.setattr(tracking, "save_record", fail_save)
    monkeypatch.setattr(sys, "stderr", _FailingCreationNotice(lambda _text: True))

    accepted = m._journal_owner_reciprocal_claim(
        _config(), "wt-child", "anomalous-potato/example-web/wt-owner",
    )

    assert accepted is False
    assert owner_path.read_bytes() == before
    assert tracking.load_record(owner_path).resources == []
    _assert_creation_diagnostic(path, errno=28)
    _assert_creation_diagnostic(path, errno=19)
    assert capsys.readouterr().out == ""


def _owned_diagnostic_creation(tmp_path, monkeypatch):
    from agent_worktrees import activity
    from test_worktree_creation_seed import (
        _create_config, _stub_create_worktree_core_internals,
    )

    monkeypatch.setattr(
        cfg, "project_dir", lambda name=None: tmp_path / "state" / (name or "demo-repo"),
    )
    config = _create_config(tmp_path)
    records = cfg.project_dir(config.repo_name) / "worktrees"
    monkeypatch.setattr(cfg, "tracking_dir", lambda _name=None: records)
    log_event = activity.log_event
    _stub_create_worktree_core_internals(monkeypatch, tmp_path, config)
    monkeypatch.setattr(activity, "log_event", log_event)
    monkeypatch.setattr(cfg, "load_project_config", lambda _name, **_kwargs: config)
    owner_checkout = Path(config.default_repo.worktree_root) / "wt-owner"
    owner_checkout.mkdir(parents=True)
    owner = tracking.create_new_record(
        "wt-owner", "worktree/wt-owner", str(owner_checkout), config.repo_name,
        config.machine, config.platform, records,
    )
    path, _ = _creation_activity(tmp_path, monkeypatch)
    order = []
    save_record = tracking.save_record

    def observe_save(record, target=None, **kwargs):
        save_record(record, target, **kwargs)
        order.append("claim" if record.worktree_id == owner.worktree_id else "record")

    monkeypatch.setattr(tracking, "save_record", observe_save)
    return config, owner, path, order


@pytest.mark.guard
def test_owned_creation_branch_notice_failure_preserves_git_and_first_record(
    tmp_path, monkeypatch, capsys,
):
    from agent_worktrees import creation_requests

    config, owner, path, order = _owned_diagnostic_creation(tmp_path, monkeypatch)

    def create_git(_anchor, checkout, *_args):
        assert tracking.load_record(owner.yaml_path).resources[0].is_unsettled
        order.append("git")
        Path(checkout).mkdir()

    monkeypatch.setattr(m.git_ops, "create_worktree", create_git)
    monkeypatch.setattr(sys, "stderr", _FailingCreationNotice(
        lambda text: "Creating worktree on branch" in text,
    ))
    result = m._create_worktree_core(
        config, owner_ref=f"{config.machine}/{config.repo_name}/wt-owner",
        creation_key="creation-notice", build_launch_plan=False,
        inherit_parent_session=False, no_pair=True,
    )

    assert order == ["claim", "git", "record"]
    record = tracking.load_record(cfg.tracking_dir() / f"{result['worktree']['id']}.yaml")
    assert record.owner_ref == f"{config.machine}/{config.repo_name}/wt-owner"
    assert creation_requests.inspect(config, "creation-notice")["status"] == "complete"
    _assert_creation_diagnostic(path, errno=19)
    assert capsys.readouterr().out == ""


@pytest.mark.guard
def test_owned_creation_git_failure_is_not_replaced_by_diagnostic_io(
    tmp_path, monkeypatch,
):
    from agent_worktrees import creation_requests

    config, owner, path, order = _owned_diagnostic_creation(tmp_path, monkeypatch)
    failure = OSError(28, "storage-private-marker", "private-storage-path")

    def fail_git(_anchor, _checkout, *_args):
        assert tracking.load_record(owner.yaml_path).resources[0].is_unsettled
        order.append("git")
        raise failure

    monkeypatch.setattr(m.git_ops, "create_worktree", fail_git)
    monkeypatch.setattr(sys, "stderr", _FailingCreationNotice(
        lambda text: "Creating worktree on branch" in text or " failed " in text,
    ))
    with pytest.raises(OSError) as caught:
        m._create_worktree_core(
            config, owner_ref=f"{config.machine}/{config.repo_name}/wt-owner",
            creation_key="creation-fault", build_launch_plan=False,
            inherit_parent_session=False, no_pair=True,
        )

    assert caught.value is failure
    assert order == ["claim", "git"]
    assert creation_requests.inspect(config, "creation-fault")["status"] == "pending"
    assert len(tracking.list_records(cfg.tracking_dir())) == 1
    assert tracking.load_record(owner.yaml_path).resources[0].is_unsettled
    _assert_creation_diagnostic(path, errno=19)
