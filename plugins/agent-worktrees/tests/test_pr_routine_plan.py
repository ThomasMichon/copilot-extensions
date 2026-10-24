"""Data-only planning distinguishes owned continuation from an override."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_recovery, pr_recovery_plan, tracking


@pytest.mark.parametrize("routine", [False, True])
@pytest.mark.parametrize("ancestor", [False, True])
@pytest.mark.parametrize("unusable", [False, True])
def test_source_lineage_settlement_requires_normal_mode_and_intact_evidence(
    monkeypatch, routine, ancestor, unusable,
):
    pr = tracking.PRRecord(
        state="open", provider="github", repo="example/project", number=7,
        branch="pr/topic-owned", head_sha="a" * 40,
    )
    record = SimpleNamespace(
        worktree_id="owned", repo="example/project",
        worktree_path="/synthetic/owned", branch="worktree/owned",
    )
    config = SimpleNamespace(default_repo=SimpleNamespace(pr=SimpleNamespace(api_base="")))
    monkeypatch.setattr(pr_recovery_plan, "_evidence_snapshot", lambda *a: ("", False))

    def synced(*args, **kwargs):
        if unusable:
            raise ValueError("invalid historical checkpoint")
        return None

    monkeypatch.setattr(pr_recovery, "synced", synced)
    monkeypatch.setattr(git_ops, "is_commit_ancestor", lambda *a, **k: ancestor)
    operation = pr_recovery_plan.build(
        config, record, pr, source_head="b" * 40, expected_head="c" * 40,
        identity="github.com/example/project", overrides=(), cwd="/synthetic/owned",
        routine=routine,
    )
    forward = routine and ancestor and not unusable
    assert operation.action == ("settle-publication" if forward else "rewrite")
    assert operation.evidence_status == (
        "unusable" if unusable else "source-lineage" if forward else "none"
    )
    assert operation.saved_head == "a" * 40
    assert operation.source_head == "b" * 40
    assert operation.expected_head == "c" * 40
    assert bool(operation.evidence_fingerprint) == forward
