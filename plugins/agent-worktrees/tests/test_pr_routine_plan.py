"""Data-only planning distinguishes owned continuation from an override."""

from __future__ import annotations

import json
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


@pytest.mark.parametrize("shape", ["legacy", "collection"])
@pytest.mark.parametrize("field", ["local_ref", "published_ref", "lineage_ref"])
@pytest.mark.parametrize("state", ["valid", "repointed", "missing"])
def test_retained_ref_integrity_distinguishes_absent_and_corrupt_evidence(
    tmp_path, monkeypatch, shape, field, state,
):
    pr = tracking.PRRecord(pr_id="owned-pr")
    point = {
        "local_ref": "refs/recovery/local", "local_head": "a" * 40,
        "published_ref": "refs/recovery/published", "published_head": "b" * 40,
        "lineage_ref": "refs/recovery/lineage", "lineage_head": "c" * 40,
    }
    path = tmp_path / "checkpoint.json"
    path.write_text(json.dumps(
        point if shape == "legacy" else {"checkpoints": {pr.pr_id: point}},
    ))
    monkeypatch.setattr(pr_recovery, "_path", lambda *a: path)

    def probe(*args, **kwargs):
        selected = next(key for key in point if key.endswith("_ref") and point[key] == args[-1])
        head = point[selected.replace("_ref", "_head")]
        if selected == field and state == "repointed":
            head = "d" * 40
        if selected == field and state == "missing":
            return SimpleNamespace(returncode=1, stdout="")
        return SimpleNamespace(returncode=0, stdout=head + "\n")

    monkeypatch.setattr(git_ops, "git", probe)
    fingerprint, unusable = pr_recovery_plan._evidence_snapshot(pr, "/synthetic/owned")
    assert len(fingerprint) == 64
    assert unusable is (state != "valid")
