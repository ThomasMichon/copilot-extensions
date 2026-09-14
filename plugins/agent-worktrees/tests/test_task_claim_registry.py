"""Tests for the task-claim external-discovery registry (#2584 follow-up).

Covers the 4-tier origin resolution chain and a real round-trip write/read
against a local bare-repo standing in for each tier's remote.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from agent_worktrees import config as cfg
from agent_worktrees import task_claim_registry as tcr
from agent_worktrees.lease_config import ConfigError


def _init_bare(path: Path) -> Path:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(["git", "init", "--bare", str(path)], check=True, env=env,
                   capture_output=True, text=True)
    return path


def _init_repo_with_remote(path: Path, remote: Path) -> Path:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    subprocess.run(["git", "init", str(path)], check=True, env=env,
                   capture_output=True, text=True)
    subprocess.run(["git", "-C", str(path), "remote", "add", "origin", str(remote)],
                   check=True, env=env, capture_output=True, text=True)
    return path


# ── tier resolution ──────────────────────────────────────────────────────────

def test_tier1_bound_knowledge_repo_wins(monkeypatch, tmp_path):
    knowledge_remote = _init_bare(tmp_path / "knowledge.git")
    knowledge_anchor = _init_repo_with_remote(tmp_path / "knowledge", knowledge_remote)
    monkeypatch.setattr("agent_worktrees.repos.resolve_path", lambda name: str(knowledge_anchor))
    repo = cfg.RepoConfig(anchor=str(tmp_path / "harness"), worktree_root=str(tmp_path / "wt"),
                          stateless=True)
    conf = cfg.Config(srcroot=str(tmp_path), machine="m", platform="windows",
                      repo_name="harness", repos={"harness": repo},
                      knowledge_repo="knowledge")
    origin, auth_remote, auth_cwd = tcr.resolve_task_claim_origin(conf)
    assert origin == str(knowledge_remote)
    assert auth_remote == "origin"
    assert auth_cwd == str(knowledge_anchor)


def test_tier2_own_remote_when_not_stateless(monkeypatch, tmp_path):
    own_remote = _init_bare(tmp_path / "own.git")
    own_anchor = _init_repo_with_remote(tmp_path / "own", own_remote)
    repo = cfg.RepoConfig(anchor=str(own_anchor), worktree_root=str(tmp_path / "wt"),
                          stateless=False)
    conf = cfg.Config(srcroot=str(tmp_path), machine="m", platform="windows",
                      repo_name="proj", repos={"proj": repo}, knowledge_repo="")
    origin, auth_remote, auth_cwd = tcr.resolve_task_claim_origin(conf)
    assert origin == str(own_remote)
    assert auth_remote is None and auth_cwd is None


def test_tier3_local_mirror_when_stateless_and_unbound(tmp_path):
    repo = cfg.RepoConfig(anchor=str(tmp_path / "harness"), worktree_root=str(tmp_path / "wt"),
                          stateless=True)
    (tmp_path / "harness").mkdir()
    conf = cfg.Config(srcroot=str(tmp_path), machine="m", platform="windows",
                      repo_name="harness", repos={"harness": repo}, knowledge_repo="")
    origin, auth_remote, auth_cwd = tcr.resolve_task_claim_origin(conf)
    expected = tmp_path / "harness" / ".git" / "agent-worktrees" / "task-claims.git"
    assert origin == str(expected)
    assert (expected / "HEAD").exists()
    assert auth_remote is None and auth_cwd is None


def test_tier4_machine_local_mirror_as_last_resort(monkeypatch, tmp_path):
    # No repos at all -> default_repo raises -> tiers 1-3 all miss.
    conf = cfg.Config(srcroot=str(tmp_path), machine="m", platform="windows",
                      repo_name="x", repos={}, knowledge_repo="")
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path / "install-root")
    origin, auth_remote, auth_cwd = tcr.resolve_task_claim_origin(conf)
    expected = tmp_path / "install-root" / "task-claims.git"
    assert origin == str(expected)
    assert (expected / "HEAD").exists()


# ── round-trip write/read ────────────────────────────────────────────────────

def test_set_and_read_status_round_trips(tmp_path):
    remote = _init_bare(tmp_path / "mirror.git")
    settings = tcr.LeaseSettings(origin=str(remote), ref_prefix="refs/agent-worktrees/leases/v1",
                                 default_ttl_seconds=3600, max_ttl_seconds=604_800)
    ok = tcr.set_task_claim_status("task-abc", "active", settings=settings)
    assert ok is True

    from agent_worktrees import lease_store, obligations
    snap = lease_store.GitLeaseStore(settings).inspect("task", "task-abc")
    assert snap is not None
    assert obligations.from_context(snap.record.context) == "active"

    # Re-writing (renew path -- the lease is live) updates the disposition.
    ok = tcr.set_task_claim_status("task-abc", "released", settings=settings)
    assert ok is True
    snap2 = lease_store.GitLeaseStore(settings).inspect("task", "task-abc")
    assert obligations.from_context(snap2.record.context) == "released"


def test_set_status_degrades_to_false_on_error(monkeypatch):
    def _raise(config=None):
        raise ConfigError("no store")
    monkeypatch.setattr(tcr, "load_task_claim_settings", _raise)
    assert tcr.set_task_claim_status("task-x", "active") is False
