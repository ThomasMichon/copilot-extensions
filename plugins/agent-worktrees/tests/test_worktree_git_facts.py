"""Shared git wrapper: delegation, refinement, and mode-specific freshness."""

from __future__ import annotations

import argparse
import dataclasses
import types

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import (
    classify_daemon,
    config,
    git_ops,
    locks,
    output,
    session_tracking_cli,
    sessions,
    tracking,
    worktree_git_facts,
    worktree_status_compute,
    worktree_status_daemon,
)
from agent_worktrees.worktree_status_cache import WorktreeStatusCache

pytestmark = pytest.mark.contract("agent_worktrees.shared_git_facts")


@pytest.fixture
def git_facts_context(monkeypatch, tmp_path):
    project = "isolated"
    project_dir = tmp_path / project
    tracking_dir = project_dir / "worktrees"
    tracking_dir.mkdir(parents=True)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / ".git").mkdir()
    record = tracking.WorktreeRecord(
        worktree_id="wt1",
        branch="worktree/wt1",
        worktree_path=str(checkout),
        repo="example",
        machine="example",
        platform="windows",
        started_at="2026-10-08T00:00:00",
        last_resumed_at="2026-10-08T00:00:00",
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=None,
    )
    record_path = tracking_dir / "wt1.yaml"
    tracking.save_record(record, record_path)
    repo = config.RepoConfig(anchor="", worktree_root="", default_branch="main")
    monkeypatch.setattr(config, "project_dir", lambda name=None: project_dir)
    monkeypatch.setattr(config, "project_name", lambda: project)
    monkeypatch.setattr(
        config, "load_config", lambda **kwargs: types.SimpleNamespace(default_repo=repo)
    )
    monkeypatch.setattr(cli, "_build_active_paths", lambda *args: set())
    monkeypatch.setattr(sessions, "scan_sessions_fast", lambda records: sessions.SessionContext())
    monkeypatch.setattr(
        sessions, "verify_worktree_active", lambda record: sessions.LiveVerdict(active=True)
    )
    monkeypatch.setattr(session_tracking_cli, "_find_tracking_file", lambda wt: record_path)
    monkeypatch.setattr(session_tracking_cli, "_project_for_tracking_file", lambda path: project)
    monkeypatch.setattr(cli, "_status_monitor_enabled", lambda: False)
    return project, record, repo, record_path


def test_both_public_entry_points_delegate_to_shared_wrapper_over_real_transports(
    monkeypatch, tmp_path, git_facts_context
):
    project, record, _repo, _record_path = git_facts_context
    calls = []

    def shared_compute(rec, **kwargs):
        calls.append((rec.worktree_id, kwargs))
        return git_ops.WorktreeStateInfo(
            state=git_ops.WorktreeState.WIP,
            ahead=42,
            behind=9 if kwargs["fetch"] else 2,
            fetch_requested=kwargs["fetch"],
        )

    monkeypatch.setattr(worktree_git_facts, "compute", shared_compute)

    def no_caller_compute(*args, **kwargs):
        pytest.fail("live daemon request fell through to caller-side computation")

    monkeypatch.setattr(cli, "_classify_records_lease_guarded", no_caller_compute)
    monkeypatch.setattr(cli, "_worktree_status_compute", no_caller_compute)
    cache = WorktreeStatusCache(tmp_path / "status.sqlite")
    batch = classify_daemon.start_server(cli._classify_daemon_compute)
    bundle = worktree_status_daemon.start_server(
        worktree_status_daemon.build_cached_compute(cache, worktree_status_compute.compute)
    )
    lock_path = tmp_path / "monitor.lock"
    monkeypatch.setattr(cli, "_monitor_lock_path", lambda: lock_path)
    outputs = []
    monkeypatch.setattr(output, "_json_output", outputs.append)
    try:
        batch.start()
        bundle.start()
        locks.write_lock(
            lock_path,
            extra={
                **classify_daemon.rendezvous_fields(batch),
                **worktree_status_daemon.rendezvous_fields(bundle),
            },
        )
        states = cli._classify_records(
            [record],
            daemon_filters={"status_filter": None, "platform_filter": None, "all": False},
        )
        rc = session_tracking_cli.cmd_worktree_status_bundle(
            argparse.Namespace(worktree_id="wt1", force_refresh=True, json=True)
        )
    finally:
        batch.close()
        bundle.close()
        cache.close()

    assert states["wt1"].ahead == 42
    assert rc == 0
    assert outputs[0]["facts"]["git_state"]["value"]["ahead"] == 42
    assert [(wt, kwargs["fetch"]) for wt, kwargs in calls] == [("wt1", False), ("wt1", True)]
    assert calls[0][1]["active_paths"] == set()
    assert calls[1][1]["active_paths"] is None
    assert outputs[0]["facts"]["git_state"]["confirmed"] is True


@pytest.mark.parametrize(
    "status,state,dirty,expected",
    [
        ("finalized", "unused", 0, "completed"),
        ("complete", "wip", 0, "completed"),
        ("completed", "wip", 0, "completed"),
        ("finalized", "active", 0, "active"),
        ("finalized", "dirty", 0, "dirty"),
        ("finalized", "orphan", 1, "orphan"),
        ("active", "unused", 0, "convo"),
    ],
)
def test_shared_refinements_preserve_disposition_and_freshness_fields(
    monkeypatch, git_facts_context, status, state, dirty, expected
):
    _project, record, repo, _path = git_facts_context
    record.status = status
    raw = git_ops.WorktreeStateInfo(
        state=git_ops.WorktreeState(state),
        ahead=3,
        behind=5,
        dirty=dirty,
        current_branch=record.branch,
        branch_drift=True,
        fetch_requested=True,
        fetch_failed=True,
    )
    monkeypatch.setattr(git_ops, "classify_worktree", lambda *args, **kwargs: raw)
    info = worktree_git_facts.compute(
        record, repo=repo, fetch=True, active_paths=None, session_turns=4
    )
    assert info == dataclasses.replace(raw, state=git_ops.WorktreeState(expected))


@pytest.mark.parametrize("status,expected", [("finalized", "completed"), ("active", "gone")])
def test_missing_checkout_never_fetches(monkeypatch, git_facts_context, status, expected):
    _project, record, repo, _path = git_facts_context
    record.worktree_path += "-missing"
    record.status = status

    def no_git(*args, **kwargs):
        pytest.fail("missing checkout must not invoke git")

    monkeypatch.setattr(git_ops, "classify_worktree", no_git)
    info = worktree_git_facts.compute(record, repo=repo, fetch=True, active_paths=None)
    assert info.state == git_ops.WorktreeState(expected)


def test_finalized_attached_shell_cannot_hide_dirty_checkout(
    monkeypatch, git_facts_context,
):
    from agent_worktrees import prune

    _project, record, repo, _path = git_facts_context
    record.status = "finalized"
    calls = []

    def classify(*args, active_paths=None, **kwargs):
        calls.append(active_paths)
        return git_ops.WorktreeStateInfo(
            state=git_ops.WorktreeState.ACTIVE if active_paths else git_ops.WorktreeState.DIRTY,
            dirty=0 if active_paths else 1,
        )

    monkeypatch.setattr(git_ops, "classify_worktree", classify)
    info = worktree_git_facts.compute(
        record, repo=repo, fetch=False, active_paths={record.worktree_path},
    )
    assert calls == [{record.worktree_path}, None]
    assert info.state == git_ops.WorktreeState.ACTIVE
    assert info.dirty == 1
    descriptor = prune.assemble_closure_descriptor(
        record, info, prune.CleanupDisposition(False, "active", "live"),
        held_claims=0, open_follow_ups=0, evidence_mode="cached",
    )
    assert descriptor.display["finalized"] is False
    assert not info.fetch_requested
    assert not info.fetch_failed


def test_batch_preserves_root_tracking_override_compatibility(monkeypatch, git_facts_context):
    _project, record, repo, _path = git_facts_context
    monkeypatch.setattr(
        git_ops, "classify_worktree",
        lambda *args, **kwargs: git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.UNUSED),
    )
    monkeypatch.setattr(
        cli, "_apply_tracking_override",
        lambda rec, info: dataclasses.replace(info, state=git_ops.WorktreeState.WIP),
    )
    assert cli._classify_one_record(
        record, repo=repo, active_paths=None
    ).state == git_ops.WorktreeState.WIP


@pytest.mark.parametrize("missing", [False, True])
def test_bundle_honors_finalized_tracking(monkeypatch, git_facts_context, missing):
    project, record, _repo, record_path = git_facts_context
    record.status = "finalized"
    if missing:
        record.worktree_path += "-missing"
    tracking.save_record(record, record_path)
    monkeypatch.setattr(
        git_ops, "classify_worktree",
        lambda *args, **kwargs: git_ops.WorktreeStateInfo(
            state=git_ops.WorktreeState.WIP, fetch_requested=True
        ),
    )
    fact = worktree_status_compute.compute(project, record.worktree_id)["facts"]["git_state"]
    assert fact["value"]["state"] == "completed"
    assert fact["value"]["fetch_requested"] is (not missing)
    assert fact["confirmed"] is True


def test_bundle_uses_durable_turns_and_keeps_live_fact_separate(monkeypatch, git_facts_context):
    project, record, _repo, record_path = git_facts_context
    record.session_turns = 4
    tracking.save_record(record, record_path)
    calls = []

    def leaf(path, branch, **kwargs):
        calls.append(kwargs)
        return git_ops.WorktreeStateInfo(
            state=git_ops.WorktreeState.UNUSED, fetch_requested=True
        )

    monkeypatch.setattr(git_ops, "classify_worktree", leaf)
    bundle = worktree_status_compute.compute(project, record.worktree_id)
    assert bundle["facts"]["git_state"]["value"]["state"] == "convo"
    assert bundle["facts"]["git_state"]["value"]["fetch_requested"] is True
    assert bundle["facts"]["liveness"]["value"]["active"] is True
    assert calls == [{"fetch": True, "remote": "origin", "default_branch": "main", "active_paths": None}]

    ctx = sessions.SessionContext(turn_count={sessions._normalize_path(record.worktree_path): 0})
    batch = cli._classify_one_record(record, repo=_repo, active_paths=set(), session_ctx=ctx)
    assert batch.state == git_ops.WorktreeState.UNUSED
    ctx.turn_count[sessions._normalize_path(record.worktree_path)] = 4
    batch = cli._classify_one_record(record, repo=_repo, active_paths=set(), session_ctx=ctx)
    assert batch.state == git_ops.WorktreeState.CONVO


def test_fetch_modes_legitimately_observe_different_remote_refs(monkeypatch, tmp_path, git_facts_context):
    project, record, repo, record_path = git_facts_context
    seed = tmp_path / "seed"
    remote = tmp_path / "remote.git"
    checkout = tmp_path / "real-checkout"
    git_ops.git("init", "-b", "main", str(seed))
    git_ops.git("-c", "user.name=Example", "-c", "user.email=example@example.invalid",
                "commit", "--allow-empty", "-m", "initial", cwd=str(seed), no_hooks=True)
    git_ops.git("clone", "--bare", str(seed), str(remote))
    git_ops.git("clone", str(remote), str(checkout))
    git_ops.git("checkout", "-b", record.branch, cwd=str(checkout))
    git_ops.git("remote", "add", "origin", str(remote), cwd=str(seed))
    git_ops.git("-c", "user.name=Example", "-c", "user.email=example@example.invalid",
                "commit", "--allow-empty", "-m", "remote advances", cwd=str(seed), no_hooks=True)
    git_ops.git("push", "origin", "main", cwd=str(seed), no_hooks=True)
    record.worktree_path = str(checkout)
    tracking.save_record(record, record_path)

    stale = cli._classify_one_record(record, repo=repo, active_paths=None)
    fresh = worktree_status_compute.compute(project, record.worktree_id)["facts"]["git_state"]
    assert stale.behind == 0
    assert not stale.fetch_requested
    assert fresh["value"]["behind"] == 1
    assert fresh["value"]["fetch_requested"] is True
    assert not fresh["value"]["fetch_failed"]
    assert fresh["confirmed"] is True
