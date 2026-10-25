"""Data-only planning distinguishes owned continuation from an override."""

from __future__ import annotations

import json
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, pr_recovery, pr_recovery_plan, tracking


@pytest.mark.parametrize("routine", [False, True])
@pytest.mark.parametrize("ancestor", [False, True])
@pytest.mark.parametrize("unusable", [False, True])
def test_source_lineage_settlement_requires_normal_mode_and_intact_evidence(
    monkeypatch, capsys, routine, ancestor, unusable,
):
    pr = tracking.PRRecord(
        state="open", provider="github", repo="example/project", number=7,
        branch="pr/topic-owned", head_sha="a" * 40,
    )
    record = SimpleNamespace(
        worktree_id="owned", repo="example/project",
        worktree_path="/synthetic/owned", branch="worktree/owned",
    )
    config = SimpleNamespace(default_repo=SimpleNamespace(
        remote="origin", default_branch="main", pr=SimpleNamespace(api_base=""),
    ))
    monkeypatch.setattr(pr_recovery_plan, "_evidence_snapshot", lambda *a: ("", unusable))

    def synced(*args, **kwargs):
        if unusable:
            pytest.fail("Known unusable evidence must not be probed again.")
        return None

    monkeypatch.setattr(pr_recovery, "synced", synced)
    monkeypatch.setattr(pr_recovery, "_rev", lambda *a: "e" * 40)
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(stdout="d" * 40))
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
    assert operation.rebase_base_sha == ("d" * 40 if forward else "")
    assert bool(operation.evidence_fingerprint) == forward
    if unusable:
        captured = capsys.readouterr()
        diagnostic = captured.out + captured.err
        assert ("sign-off" in diagnostic) is not routine


@pytest.fixture
def retained_checkpoint(tmp_path, monkeypatch):
    pr = tracking.PRRecord(pr_id="owned-pr")
    point = asdict(pr_recovery.RecoveryPoint(
        version=1, worktree_id="owned", branch="worktree/owned",
        local_ref="refs/recovery/local", local_head="a" * 40,
        target_head="f" * 40, synced_head="e" * 40,
        published_ref="refs/recovery/published", published_head="b" * 40,
        lineage_ref="refs/recovery/lineage", lineage_head="c" * 40,
        pr_id=pr.pr_id,
    ))
    path = tmp_path / "checkpoint.json"
    monkeypatch.setattr(pr_recovery, "_path", lambda *a: path)
    commits = {
        point[field]: point[field.replace("_ref", "_head")]
        for field in ("local_ref", "published_ref", "lineage_ref")
    }
    commits.update({point[field]: point[field] for field in ("target_head", "synced_head")})
    monkeypatch.setattr(pr_recovery, "_commits", lambda refs, cwd: tuple(commits[ref] for ref in refs))
    monkeypatch.setattr(git_ops, "is_commit_ancestor", lambda *a, **k: True)
    return pr, point, path


def checkpoint_document(pr, point, shape):
    return point if shape == "legacy" else {
        "version": 2, "latest": pr.pr_id, "checkpoints": {pr.pr_id: point},
    }


@pytest.mark.parametrize("shape", ["legacy", "collection"])
@pytest.mark.parametrize("field", ["local_ref", "published_ref", "lineage_ref"])
@pytest.mark.parametrize("state", ["valid", "repointed", "missing"])
def test_retained_ref_integrity_distinguishes_absent_and_corrupt_evidence(
    retained_checkpoint, monkeypatch, shape, field, state,
):
    pr, point, path = retained_checkpoint
    path.write_text(json.dumps(checkpoint_document(pr, point, shape)))

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


@pytest.mark.parametrize("shape", ["legacy", "collection"])
@pytest.mark.parametrize("field", [
    "local_ref", "published_ref", "lineage_ref",
    "local_head", "target_head", "published_head", "lineage_head", "synced_head",
])
@pytest.mark.parametrize("change", ["absent", "empty", "wrong-type"])
def test_completed_checkpoint_requires_all_retention_metadata(
    retained_checkpoint, monkeypatch, shape, field, change,
):
    pr, point, path = retained_checkpoint
    original = point.copy()
    if change == "absent":
        point.pop(field)
    else:
        point[field] = "" if change == "empty" else []
    path.write_text(json.dumps(checkpoint_document(pr, point, shape)))

    def probe(*args, **kwargs):
        selected = next(
            key for key in original if key.endswith("_ref") and original[key] == args[-1]
        )
        return SimpleNamespace(
            returncode=0, stdout=original[selected.replace("_ref", "_head")] + "\n",
        )

    monkeypatch.setattr(git_ops, "git", probe)
    _, unusable = pr_recovery_plan._evidence_snapshot(pr, "/synthetic/owned")
    assert unusable


@pytest.mark.parametrize("shape", ["legacy", "collection"])
def test_checkpoint_for_another_pr_is_inapplicable_not_current_corruption(
    retained_checkpoint, monkeypatch, shape,
):
    pr, point, path = retained_checkpoint
    path.write_text(json.dumps(checkpoint_document(pr, point, shape)))
    other = tracking.PRRecord(pr_id="another-pr")
    monkeypatch.setattr(
        git_ops, "git", lambda *a, **k: pytest.fail("Unrelated checkpoint refs must not be probed."),
    )
    _, unusable = pr_recovery_plan._evidence_snapshot(other, "/synthetic/owned")
    assert not unusable


@pytest.mark.parametrize("change", ["absent", "empty", "different"])
def test_collection_checkpoint_identity_must_match_its_selected_key(
    retained_checkpoint, monkeypatch, change,
):
    pr, point, path = retained_checkpoint
    document = checkpoint_document(pr, point, "collection")
    if change == "absent":
        point.pop("pr_id")
    else:
        point["pr_id"] = "" if change == "empty" else "another-pr"
    path.write_text(json.dumps(document))

    def probe(*args, **kwargs):
        selected = next(key for key in point if key.endswith("_ref") and point[key] == args[-1])
        return SimpleNamespace(returncode=0, stdout=point[selected.replace("_ref", "_head")] + "\n")

    monkeypatch.setattr(git_ops, "git", probe)
    _, unusable = pr_recovery_plan._evidence_snapshot(pr, "/synthetic/owned")
    assert unusable


@pytest.mark.parametrize("failure", [
    "missing-target", "missing-result", "non-commit", "wrong-binding", "wrong-base", "wrong-lineage",
])
def test_completed_checkpoint_requires_its_commit_objects_and_intrinsic_lineage(
    retained_checkpoint, monkeypatch, failure,
):
    pr, point, path = retained_checkpoint
    path.write_text(json.dumps(checkpoint_document(pr, point, "collection")))
    original = pr_recovery._commits

    def commits(refs, cwd):
        if failure in ("missing-target", "missing-result", "non-commit"):
            raise ValueError("missing or non-commit recovery object")
        result = original(refs, cwd)
        return ("d" * 40, *result[1:]) if failure == "wrong-binding" else result

    def probe(*args, **kwargs):
        selected = next(key for key in point if key.endswith("_ref") and point[key] == args[-1])
        return SimpleNamespace(returncode=0, stdout=point[selected.replace("_ref", "_head")] + "\n")

    def ancestor(old, new, **kwargs):
        return not (
            (failure == "wrong-base" and old == point["target_head"])
            or (failure == "wrong-lineage" and old == point["published_head"])
        )

    monkeypatch.setattr(pr_recovery, "_commits", commits)
    monkeypatch.setattr(git_ops, "git", probe)
    monkeypatch.setattr(git_ops, "is_commit_ancestor", ancestor)
    _, unusable = pr_recovery_plan._evidence_snapshot(pr, "/synthetic/owned")
    assert unusable


@pytest.mark.parametrize("bases", ["", "not-a-commit", "d" * 40 + "\n" + "e" * 40])
def test_source_lineage_requires_one_immutable_publication_base(monkeypatch, bases):
    pr = tracking.PRRecord(
        state="open", provider="github", repo="example/project", number=7,
        branch="pr/topic-owned", head_sha="a" * 40,
    )
    record = SimpleNamespace(
        worktree_id="owned", repo="example/project",
        worktree_path="/synthetic/owned", branch="worktree/owned",
    )
    config = SimpleNamespace(default_repo=SimpleNamespace(
        remote="origin", default_branch="main", pr=SimpleNamespace(api_base=""),
    ))
    monkeypatch.setattr(pr_recovery_plan, "_evidence_snapshot", lambda *a: ("", False))
    monkeypatch.setattr(pr_recovery, "synced", lambda *a, **k: None)
    monkeypatch.setattr(pr_recovery, "_rev", lambda *a: "f" * 40)
    monkeypatch.setattr(git_ops, "is_commit_ancestor", lambda *a, **k: True)
    monkeypatch.setattr(git_ops, "git", lambda *a, **k: SimpleNamespace(stdout=bases))
    with pytest.raises(ValueError, match="unique accepted-publication base"):
        pr_recovery_plan.build(
            config, record, pr, source_head="b" * 40, expected_head="c" * 40,
            identity="github.com/example/project", overrides=(), cwd="/synthetic/owned",
            routine=True,
        )
