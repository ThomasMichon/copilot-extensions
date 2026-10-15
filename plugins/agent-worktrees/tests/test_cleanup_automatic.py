"""Automatic exact cleanup retains dispatch ownership independently of claims."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from agent_worktrees import __main__ as cli
from agent_worktrees import cleanup_gc_cli, output, reap_cli
from _automatic_cleanup_owner_fixture import dispatch_attempt, exercise
from _cleanup_revalidation_helpers import CleanupHarness, S, info, make_record, mutate_record


def arguments(*extra: str) -> list[str]:
    return [
        "--project", "project-a", "cleanup", "--worktree-id", "workspace-a",
        "--include-unused", "--json", *extra,
    ]


@pytest.mark.parametrize("state", ["pending", "retrying", "opaque"])
def test_automatic_exact_cleanup_preserves_dispatch_without_claimant(tmp_path, state):
    code, proof = exercise(tmp_path, state, arguments("--automatic"))
    assert code == 1
    assert proof["payload"]["bucket"] == "dispatch-pending"
    assert proof["payload"]["removed"] is False
    assert proof["reaped"] == []
    assert proof["workspace_exists"] and proof["record_exists"] and proof["unrelated_exists"]


def test_automatic_exact_cleanup_reaps_only_normal_unused_workspace(tmp_path):
    code, proof = exercise(tmp_path, "unused", arguments("--automatic"))
    assert code == 0
    assert proof["payload"]["removed"] is True
    assert proof["reaped"] == [("workspace-a", "unused")]
    assert proof["workspace_exists"] and proof["record_exists"] and proof["unrelated_exists"]


def test_explicit_operator_exact_cleanup_retains_existing_dispatch_semantics(tmp_path):
    code, proof = exercise(tmp_path, "pending", arguments())
    assert code == 0
    assert proof["payload"]["removed"] is True
    assert proof["reaped"] == [("workspace-a", "unused")]


def test_automatic_cleanup_rechecks_dispatch_provenance_after_selection(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(tmp_path, status="active")
    path = harness.seed(record)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    harness.set_finalize_lock(
        lambda: mutate_record(path, lambda rec: setattr(rec, "dispatch_attempt", dispatch_attempt())),
    )
    result = cli.reap_one("wt1", include_unused=True, automatic=True)
    assert result["removed"] is False
    assert result["bucket"] == "dispatch-pending"
    assert harness.reaped == []
    assert path.is_file() and (tmp_path / "wt1").is_dir()


def test_concluded_dispatch_retains_managed_gc_route(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    record = make_record(tmp_path, status="complete", dispatch_attempt=dispatch_attempt())
    record.kind = "bridge"
    harness.seed(record)
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    monkeypatch.setattr(reap_cli, "_repo_for_record", lambda config, rec: harness.repo)
    monkeypatch.setattr(cli.git_ops, "current_branch", lambda path: record.branch)
    removed = []

    def intercept(rec, repo, tracking_path):
        removed.append(rec.worktree_id)
        return True, []

    monkeypatch.setattr(cli, "_remove_managed_worktree", intercept)
    automatic = cli.reap_one("wt1", include_unused=True, automatic=True)
    assert automatic["removed"] is False
    assert "agent-owned" in automatic["reason"]
    report = cli.sweep_managed_worktrees(min_idle_secs=0, worktree_ids={"wt1"})
    assert [item["id"] for item in report["removed"]] == ["wt1"]
    assert removed == ["wt1"]
    assert Path(record.worktree_path).is_dir() and harness.record_path().is_file()


@pytest.mark.parametrize(("worktree_id", "force"), [(None, False), ("workspace-a", True)])
def test_automatic_cli_rejects_unscoped_or_forced_cleanup(worktree_id, force):
    with output.capture_json_output() as captured:
        result = cleanup_gc_cli.cmd_cleanup(argparse.Namespace(
            automatic=True, worktree_id=worktree_id, force=force,
        ))
    assert result == 2
    assert "requires --worktree-id and forbids --force" in captured.getvalue()


def test_batch_cleanup_still_preserves_pending_dispatch(monkeypatch, tmp_path):
    harness = CleanupHarness(monkeypatch, tmp_path)
    harness.seed(make_record(tmp_path, status="active", dispatch_attempt=dispatch_attempt()))
    harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
    assert harness.run_batch(include_unused=True) == 0
    assert harness.reaped == []
