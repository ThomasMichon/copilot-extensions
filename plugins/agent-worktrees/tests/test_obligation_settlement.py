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

def _write_parent(project_root: Path, project: str, parent_id: str,
                  child_ref: str) -> Path:
    """Write a parent record (with an active claim on the child) to its tracking
    dir under ``~/.{project}/worktrees/``, returning the path."""
    wtdir = project_root / f".{project}" / "worktrees"
    wtdir.mkdir(parents=True, exist_ok=True)
    parent = tracking.create_new_record(
        parent_id, f"worktree/{parent_id}", str(project_root / parent_id),
        project, "machine-x", "windows", wtdir.parent,
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
    monkeypatch.setattr(cfg, "project_dir",
                        lambda name=None: tmp_path / f".{name or 'proj'}")
    return tmp_path


def test_parent_claim_settled_on_child_finalize(_project_root, monkeypatch):
    child_id = "wt-child"
    child_ref = tracking.format_claim_ref("machine-x", "childproj", child_id)
    parent_ref = tracking.format_claim_ref("machine-x", "parentproj", "wt-parent")
    parent_path = _write_parent(_project_root, "parentproj", "wt-parent", child_ref)

    child = SimpleNamespace(owner_claim_ref=tracking.parse_claim_ref(parent_ref))
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")

    _settle_parent_obligation(child, config, child_id)

    reloaded = tracking.load_record(parent_path)
    assert reloaded.resources[0].state == "at-rest"


def test_cross_machine_parent_is_skipped(_project_root):
    child_ref = tracking.format_claim_ref("machine-x", "childproj", "wt-child")
    # Parent on a DIFFERENT machine -> hook must not touch it.
    parent_ref = tracking.format_claim_ref("other-machine", "parentproj", "wt-parent")
    parent_path = _write_parent(_project_root, "parentproj", "wt-parent", child_ref)

    child = SimpleNamespace(owner_claim_ref=tracking.parse_claim_ref(parent_ref))
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")

    _settle_parent_obligation(child, config, "wt-child")

    assert tracking.load_record(parent_path).resources[0].state == "active"


def test_no_owner_ref_is_noop(_project_root):
    child = SimpleNamespace(owner_claim_ref=None)
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")
    # Must simply not raise.
    _settle_parent_obligation(child, config, "wt-child")


def test_missing_parent_record_is_noop(_project_root):
    parent_ref = tracking.format_claim_ref("machine-x", "parentproj", "ghost")
    child = SimpleNamespace(owner_claim_ref=tracking.parse_claim_ref(parent_ref))
    config = SimpleNamespace(machine="machine-x", repo_name="childproj")
    _settle_parent_obligation(child, config, "wt-child")  # no file -> no-op, no raise
