"""Creator-ownership reservation around `agent-worktrees run`."""

from __future__ import annotations

import argparse
import json
import types

import pytest

import agent_worktrees.__main__ as m
from agent_worktrees import claim_history, state_root, tracking
from agent_worktrees import config as cfg


def _seed_owner(tmp_path, monkeypatch):
    project_dir = tmp_path / ".p"
    tracking_dir = project_dir / "worktrees"
    tracking_dir.mkdir(parents=True)
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: project_dir)
    monkeypatch.setattr(cfg.socket, "gethostname", lambda: "m")
    monkeypatch.setattr(cfg, "detect_platform", lambda: "windows")
    monkeypatch.setattr(
        cfg, "load_config",
        lambda *a, **k: cfg.load_project_config("p"))
    monkeypatch.setattr(
        cfg, "load_project_config",
        lambda name, **kwargs: cfg.Config(
            srcroot=str(tmp_path), machine="m", platform="windows", repo_name=name,
            repos={"p": cfg.RepoConfig(
                anchor=str(tmp_path), worktree_root=str(tmp_path),
            )},
        ),
    )
    ready_root = state_root.StateRoot(
        str(tmp_path), "launch_repo", "p", False, False, True
    )
    monkeypatch.setattr(
        m.state_root_mod,
        "coordination_readiness",
        lambda config: state_root.CoordinationReadiness(
            True, "ready", ready_root
        ),
    )
    tracking.create_new_record(
        "owner", "worktree/owner", str(tmp_path / "owner"), "p",
        "m", "windows", tracking_dir)
    return tracking_dir / "owner.yaml"


def _args():
    return argparse.Namespace(
        inner_command=["resource-create"], owner_ref="m/p/owner")


def test_run_failure_retains_pending_ownership(tmp_path, monkeypatch):
    path = _seed_owner(tmp_path, monkeypatch)
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=5, stdout=""))
    assert m.cmd_run(_args()) == 5
    rec = tracking.load_record(path)
    assert len(rec.resources) == 1
    assert rec.resources[0].ref.startswith("pending-run:")


def test_run_success_replaces_pending_with_real_claim(
        tmp_path, monkeypatch):
    path = _seed_owner(tmp_path, monkeypatch)
    payload = json.dumps({
        "worktree": {"id": "child", "machine": "m", "repo": "p"}
    })
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload))
    assert m.cmd_run(_args()) == 0
    rec = tracking.load_record(path)
    assert [claim.ref for claim in rec.resources] == ["m/p/child"]


@pytest.mark.parametrize("bound,mismatch", [
    (False, None), (True, None), (False, "target"), (True, "target"), (True, "nonce"),
])
@pytest.mark.parametrize("labels", [{}, {
    "machine": "obsolete-host", "execution_machine": "renamed-space",
    "machine_identity_version": "invalid",
}])
def test_run_reuses_protected_allocation_without_row_naming_authority(
    tmp_path, monkeypatch, bound, mismatch, labels,
):
    from agent_worktrees import owned_worktree as allocation, resource_producer

    path = _seed_owner(tmp_path, monkeypatch)
    identity = {
        "ref": "receiver/destination/child", "creation_nonce": "original-nonce",
        "path": "/worktrees/child", "branch": "worktree/child",
    }
    metadata = {
        "version": allocation.METADATA_VERSION, "lifetime": allocation.LIFETIME,
        "namespace": allocation.NAMESPACE, "creator": "m/p/owner",
        "request_id": "original-request", "token": "a" * 32,
        "phase": "bound" if bound else "pending",
        "recovery": {"machine": "receiver", "project": "destination"},
        **({"identity": identity} if bound else {}),
    }
    ref = allocation.claim_ref(metadata)

    def create(*args, **kwargs):
        record = tracking.load_record(path)
        record.resources.append(tracking.ResourceClaim(
            kind="worktree", ref=ref, producer=metadata,
            created_at=tracking._now_iso(), state="active",
        ))
        with resource_producer.transaction(path):
            tracking.save_record(record, path)
        row_identity = {
            key: identity[key] for key in ("creation_nonce", "path", "branch")
        }
        if mismatch == "nonce":
            row_identity["creation_nonce"] = "replacement"
        destination = {**metadata["recovery"]}
        if mismatch == "target":
            destination["machine"] = "other"
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({
            "worktree": {
                "id": "child", "repo": "destination", "owner_ref": "m/p/owner",
                **row_identity, **labels,
            },
            "allocation": {
                "version": 1, "creator": "m/p/owner", "request_id": "original-request",
                "target": destination, "receiving_status": "allocated",
            },
        }))

    monkeypatch.setattr(m.subprocess, "run", create)
    assert m.cmd_run(_args()) == (1 if mismatch else 0)
    claims = tracking.load_record(path).resources
    protected = [claim for claim in claims if claim.ref == ref]
    assert [(claim.ref, claim.producer) for claim in protected] == [(ref, metadata)]
    pending = [claim for claim in claims if claim.ref.startswith("pending-run:")]
    assert len(claims) == (2 if mismatch else 1)
    assert len(pending) == (1 if mismatch else 0)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("version", [1, 2])
def test_run_settles_only_a_valid_execution_qualified_tuple(
    tmp_path, monkeypatch, wrapped, version,
):
    path = _seed_owner(tmp_path, monkeypatch)
    row = {
        "id": "child", "repo": "p", "machine_identity_version": version,
        "machine": "desk", "environment": "wsl", "platform": "wsl",
        "execution_machine": "guest-space",
    }
    payload = json.dumps({"worktree": row} if wrapped else row)
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload),
    )

    assert m.cmd_run(_args()) == (0 if version == 1 else 1)
    claims = tracking.load_record(path).resources
    assert len(claims) == 1
    if version == 1:
        assert claims[0].ref == "guest-space/p/child"
    else:
        assert claims[0].ref.startswith("pending-run:")



def test_run_success_feeds_claim_history_for_a_pr_claim(
        tmp_path, monkeypatch):
    """worktree-claims-transitive-finalization Phase 3b: `run`'s own
    live save site (`_finish_pending`, replacing the pending-ref
    placeholder with the real claim) must feed the same append-only
    ownership ledger `pr_ops.py` and the daemon claim verbs already feed,
    for a produced `pr`-kind claim -- only after the save is confirmed."""
    _seed_owner(tmp_path, monkeypatch)
    payload = json.dumps({
        "success": True, "pr_opened": True,
        "pr": {"ref": "https://github.com/o/r/pull/9"},
    })
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload))
    assert m.cmd_run(_args()) == 0
    events = claim_history.history_for_ref("https://github.com/o/r/pull/9")
    assert [e["event"] for e in events] == ["claimed"]
    assert events[0]["worktree_id"] == "owner" and events[0]["machine"] == "m"


def test_run_repeat_success_on_same_active_pr_claim_does_not_duplicate_history(
        tmp_path, monkeypatch):
    """A retried/re-run `run` re-observing the same already-active PR claim
    is a no-op reconciliation, not a fresh transition -- must feed exactly
    one "claimed" event, never two (matches `pr_ops._ensure_pr_claim`'s own
    idempotency contract)."""
    _seed_owner(tmp_path, monkeypatch)
    payload = json.dumps({
        "success": True, "pr_opened": True,
        "pr": {"ref": "https://github.com/o/r/pull/9"},
    })
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload))
    assert m.cmd_run(_args()) == 0
    assert m.cmd_run(_args()) == 0
    events = claim_history.history_for_ref("https://github.com/o/r/pull/9")
    assert [e["event"] for e in events] == ["claimed"]


def test_run_success_worktree_claim_does_not_feed_claim_history(
        tmp_path, monkeypatch):
    """A `worktree`-kind claim is outside claim_history's scope (`pr`-kind
    only today) -- `run` producing one must never feed a spurious entry."""
    path = _seed_owner(tmp_path, monkeypatch)
    payload = json.dumps({
        "worktree": {"id": "child", "machine": "m", "repo": "p"}
    })
    monkeypatch.setattr(
        m.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout=payload))
    assert m.cmd_run(_args()) == 0
    rec = tracking.load_record(path)
    assert rec.resources[0].kind == "worktree"
    assert claim_history.history_for_ref("m/p/child") == []


def test_run_refuses_cross_machine_owner_before_creation(
        tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    called = {"run": False}

    def _run(*args, **kwargs):
        called["run"] = True
        return types.SimpleNamespace(returncode=0, stdout="{}")

    monkeypatch.setattr(m.subprocess, "run", _run)
    args = argparse.Namespace(
        inner_command=["resource-create"], owner_ref="other/p/owner")
    assert m.cmd_run(args) == 1
    assert called["run"] is False


def test_run_cross_machine_error_precedes_unready_ambient_config(
    tmp_path,
    monkeypatch,
):
    _seed_owner(tmp_path, monkeypatch)
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
        lambda config: state_root.CoordinationReadiness(
            False,
            "knowledge_binding_required",
            root,
            error="bind the knowledge repository",
        ),
    )
    called = {"run": False}
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *a, **k: called.__setitem__("run", True),
    )

    args = argparse.Namespace(
        inner_command=["resource-create"], owner_ref="other/p/owner"
    )
    assert m.cmd_run(args) == 1
    assert called["run"] is False


def test_run_rejects_unready_owner_without_mutation_or_subprocess(
    tmp_path,
    monkeypatch,
):
    path = _seed_owner(tmp_path, monkeypatch)
    before = path.read_bytes()
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
        lambda config: state_root.CoordinationReadiness(
            False,
            "knowledge_binding_required",
            root,
            error="bind the knowledge repository",
        ),
    )
    monkeypatch.setattr(
        m.subprocess,
        "run",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("unready run launched a subprocess")
        ),
    )

    assert m.cmd_run(_args()) == 3
    assert path.read_bytes() == before


def test_owner_config_resolution_failure_is_structured_readiness(
    tmp_path,
    monkeypatch,
):
    _seed_owner(tmp_path, monkeypatch)
    monkeypatch.setattr(
        cfg,
        "load_project_config",
        lambda name, **kwargs: (_ for _ in ()).throw(
            ValueError("No repo could be resolved")
        ),
    )
    config = types.SimpleNamespace(machine="m", repo_name="p")

    result = m._coordination_readiness_for_owner_ref(
        "m/p/owner", config
    )

    assert result.ready is False
    assert result.code == "state_root_resolution_failed"
    assert "No repo could be resolved" in result.error
