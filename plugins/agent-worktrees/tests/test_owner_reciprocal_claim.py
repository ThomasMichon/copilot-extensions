"""Tests for the Phase 3c reciprocal owner-claim journaling on worktree carve.

`_journal_owner_reciprocal_claim` writes the forward half of the owner<->child
resource link (a `worktree` claim on the owner) so the owner's finalize gate sees
the obligation and the child's finalize can settle it.
"""

from __future__ import annotations

import types

import agent_worktrees.__main__ as m
from agent_worktrees import config as cfg
from agent_worktrees import tracking


def _seed_owner(tmp_path, monkeypatch, *, machine="lambda-core", project="odsp-web",
                owner_id="wt-owner"):
    """Create an owner record under project_dir(project)/worktrees and point
    project_dir at a scratch tree."""
    monkeypatch.setattr(cfg, "project_dir", lambda name=None: tmp_path / f".{name}")
    owner_dir = tmp_path / f".{project}" / "worktrees"
    owner_dir.mkdir(parents=True, exist_ok=True)
    wdir = tmp_path / owner_id
    wdir.mkdir(exist_ok=True)
    tracking.create_new_record(
        owner_id, f"worktree/{owner_id}", str(wdir), project,
        machine, "wsl", owner_dir,
    )
    return owner_dir


def _config(machine="lambda-core", repo_name="copilot-extensions"):
    return types.SimpleNamespace(machine=machine, repo_name=repo_name)


def test_journals_worktree_claim_on_same_machine_owner(tmp_path, monkeypatch):
    owner_dir = _seed_owner(tmp_path, monkeypatch)
    ok = m._journal_owner_reciprocal_claim(
        _config(), "wt-child", "lambda-core/odsp-web/wt-owner")
    assert ok is True
    rec = tracking.load_record(owner_dir / "wt-owner.yaml")
    assert len(rec.resources) == 1
    c = rec.resources[0]
    assert c.kind == "worktree"
    assert c.ref == "lambda-core/copilot-extensions/wt-child"  # child's qualified ref
    assert c.is_unsettled  # active


def test_cross_machine_owner_defers_no_write(tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    ok = m._journal_owner_reciprocal_claim(
        _config(machine="lambda-core"), "wt-child",
        "other-box/odsp-web/wt-owner")
    assert ok is False  # cross-machine -> deferred to the lease mirror


def test_no_owner_ref_is_noop(tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    assert m._journal_owner_reciprocal_claim(_config(), "wt-child", None) is False
    assert m._journal_owner_reciprocal_claim(_config(), "wt-child", "") is False


def test_missing_owner_record_is_safe_noop(tmp_path, monkeypatch):
    _seed_owner(tmp_path, monkeypatch)
    ok = m._journal_owner_reciprocal_claim(
        _config(), "wt-child", "lambda-core/odsp-web/no-such-owner")
    assert ok is False  # resolved path doesn't exist -> no write, no raise


def test_idempotent_dedups_by_ref(tmp_path, monkeypatch):
    owner_dir = _seed_owner(tmp_path, monkeypatch)
    ref = "lambda-core/odsp-web/wt-owner"
    m._journal_owner_reciprocal_claim(_config(), "wt-child", ref)
    m._journal_owner_reciprocal_claim(_config(), "wt-child", ref)
    rec = tracking.load_record(owner_dir / "wt-owner.yaml")
    assert len(rec.resources) == 1  # deduped, not doubled
