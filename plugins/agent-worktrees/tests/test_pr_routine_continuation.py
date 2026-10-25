"""Ordinary continuation needs no operator recovery for established live state."""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import (
    finalize,
    git_collab,
    git_ops,
    pr_break_glass,
    pr_ops,
    pr_publish,
    pr_recovery,
    pr_routine_recovery,
    pr_rewrite,
    providers,
    tracking,
)
from agent_worktrees.providers.base import ProviderError, PullResult
from test_pr_recovery_advanced_head import git, metadata, record
from pr_test_helpers import _stub_remote_target

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr.operator_recovery"),
    pytest.mark.timeout(120),
]


@pytest.fixture
def owned_publication(published_refspec_pr_repo, monkeypatch, request):
    config, wid, wt, remote = published_refspec_pr_repo
    monkeypatch.setattr(git_ops, "remote_slug", lambda *a, **k: "example/project")
    _stub_remote_target(monkeypatch, None, "example/project")
    result = pr_ops.set_pr(
        wid, url="https://gitea.example.com/example/project/pulls/42",
        number=42, provider="gitea", config=config,
    )
    assert result["success"], result
    current = record(wid)
    saved = git("rev-parse", "HEAD", cwd=wt)
    branch = current.pr.branch
    assert pr_publish._tip("origin", branch, str(wt)) == saved
    current.pr.head_sha = saved
    current.pr.base_sha = git("merge-base", saved, "origin/master", cwd=wt)
    current.pr.rewrite_owner = f"{wid}:{branch}"
    current.pr.rewrite_identity = pr_publish.push_identity("origin", cwd=str(wt))
    tracking.save_record(current)
    anchor = Path(config.default_repo.anchor)
    upstream_merge = getattr(request, "param", "") == "upstream-merge"
    if upstream_merge:
        git("switch", "-c", "upstream-side", cwd=anchor)
        (anchor / "side.txt").write_text("upstream side\n")
        git("add", "side.txt", cwd=anchor)
        git("commit", "-m", "upstream side", cwd=anchor)
        git("switch", "master", cwd=anchor)
    (anchor / "upstream.txt").write_text("new upstream\n")
    git("add", "upstream.txt", cwd=anchor)
    git("commit", "-m", "new upstream", cwd=anchor)
    if upstream_merge:
        git("merge", "--no-ff", "upstream-side", "-m", "merge upstream history", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)
    git("fetch", "origin", cwd=wt)
    git("rebase", "origin/master", cwd=wt)
    published = git("rev-parse", "HEAD", cwd=wt)
    git("--git-dir", str(remote), "fetch", "--no-tags", str(wt), published, cwd=wt)
    git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", published, saved, cwd=wt)
    (wt / "unpublished.txt").write_text("keep this unpublished\n")
    git("add", "unpublished.txt", cwd=wt)
    git("commit", "-m", "local followup", cwd=wt)
    monkeypatch.setattr(pr_rewrite, "observe_pr", lambda config, pr: PullResult(
        number=pr.number, head_sha=pr_publish._tip("origin", pr.branch, str(wt)),
        head_ref=pr.branch, head_url=str(remote),
    ))
    monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
    monkeypatch.setattr(pr_ops, "refresh_source_attribution", lambda *a: "")
    return config, wid, wt, remote, saved, branch, published


def observe_terminal(monkeypatch, *, branch, remote, published, state):
    def get_pull(*args, **kwargs):
        if state == "unavailable":
            raise ProviderError("provider unavailable")
        return PullResult(
            number=42, state="closed" if state == "merged" else state,
            merged=state == "merged", head_sha=published,
            head_ref=branch, head_url=str(remote),
        )

    monkeypatch.setattr(providers, "get_provider", lambda *a: SimpleNamespace(
        get_pull=get_pull,
        authority_endpoint=lambda *a: "https://gitea.example.com",
    ))
    monkeypatch.setattr(providers, "account_token_for_slug", lambda *a: None)


def test_sync_reconciles_merged_deleted_head_and_keeps_local_work(
    owned_publication, monkeypatch,
):
    config, wid, wt, remote, _saved, branch, published = owned_publication
    local_branch = git("symbolic-ref", "--short", "HEAD", cwd=wt)
    anchor = Path(config.default_repo.anchor)
    git("merge", "--squash", published, cwd=anchor)
    git("commit", "-m", "merge published change", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)
    git("--git-dir", str(remote), "update-ref", "-d", f"refs/heads/{branch}", cwd=wt)
    observe_terminal(
        monkeypatch, branch=branch, remote=remote, published=published, state="merged",
    )

    assert git_collab.sync_forward(wid, config)
    assert record(wid).pr.state == "merged"
    assert record(wid).status == "active"
    assert git("symbolic-ref", "--short", "HEAD", cwd=wt) == local_branch
    assert git("show", "HEAD:unpublished.txt", cwd=wt) == "keep this unpublished"
    assert pr_publish._tip("origin", branch, str(wt)) == ""
    assert wt.is_dir()


@pytest.mark.parametrize("state", ["open", "unavailable"])
def test_missing_remote_head_without_confirmed_merge_still_refuses(
    owned_publication, monkeypatch, state,
):
    config, wid, wt, remote, saved, branch, published = owned_publication
    source = git("rev-parse", "HEAD", cwd=wt)
    git("--git-dir", str(remote), "update-ref", "-d", f"refs/heads/{branch}", cwd=wt)
    observe_terminal(
        monkeypatch, branch=branch, remote=remote, published=published, state=state,
    )

    assert not git_collab.sync_forward(wid, config)
    assert record(wid).pr.state == "open"
    assert record(wid).pr.head_sha == saved
    assert git("rev-parse", "HEAD", cwd=wt) == source
    assert wt.is_dir()


@pytest.mark.parametrize("command", ["push-changes", "sync"])
def test_owned_fast_forward_after_lost_rebase_ack_needs_no_confirmation(
    owned_publication, monkeypatch, command,
):
    config, wid, wt, remote, saved, branch, published = owned_publication
    source = git("rev-parse", "HEAD", cwd=wt)
    assert not git_ops.is_commit_ancestor(saved, published, cwd=str(wt))
    assert git_ops.is_commit_ancestor(published, source, cwd=str(wt))
    assert not pr_recovery._path(str(wt)).exists()
    monkeypatch.setattr(
        pr_break_glass, "confirm",
        lambda *a, **k: pytest.fail("Routine fast-forward requested operator recovery."),
    )
    real_push = git_ops.push
    calls = []

    def push(*args, **kwargs):
        calls.append(kwargs)
        return real_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", push)
    if command == "push-changes":
        assert finalize.push_changes(wid, config)
        assert record(wid).pr.head_sha == source
        assert pr_publish._tip("origin", branch, str(wt)) == source
        assert len(calls) == 1
        assert not calls[0].get("allow_history_rewrite", False)
        assert not calls[0].get("force_with_lease", False)
        assert calls[0].get("force_with_lease_expect") is None
    else:
        assert git_collab.sync_forward(wid, config)
        assert record(wid).pr.head_sha == published
        assert pr_publish._tip("origin", branch, str(wt)) == published
        assert not calls
    audit = json.loads(next(metadata(wt).glob("*pr-reconciliation-*.json")).read_text())
    assert audit["outcome"] == "succeeded"
    assert audit["operation"]["evidence_status"] == "source-lineage"
    assert audit["saved_publication"]["head_sha"] == saved
    for name, commit in (("source", source), ("saved", saved), ("observed", published)):
        assert git("rev-parse", audit["recovery_refs"][name], cwd=wt) == commit
    assert not list(metadata(wt).glob("*break-glass*.json"))


@pytest.fixture
def completed_publication(owned_publication):
    config, wid, wt, remote, saved, branch, published = owned_publication
    current = record(wid)
    prefix = "refs/agent-worktrees/recovery/test-completed"
    point = pr_recovery.RecoveryPoint(
        version=1, worktree_id=wid, branch=current.branch,
        local_head=saved, local_ref=prefix + "/local",
        target_head=git("merge-base", published, "origin/master", cwd=wt),
        published_head=saved, published_ref=prefix + "/published",
        lineage_head=saved, lineage_ref=prefix + "/lineage", synced_head=published,
        pr_id=current.pr.pr_id, pr_branch=branch, pr_repo=current.pr.repo,
        pr_provider=current.pr.provider, association=pr_recovery._association(current.pr),
    )
    pr_recovery._retain_refs(point, cwd=str(wt))
    path = pr_recovery._path(str(wt))
    tracking._atomic_write(path, json.dumps({
        "version": 2, "latest": point.pr_id, "checkpoints": {point.pr_id: asdict(point)},
    }) + "\n")
    assert pr_recovery.synced(
        current, current.pr, saved, git("rev-parse", "HEAD", cwd=wt), cwd=str(wt),
    ) == point
    return owned_publication


@pytest.mark.parametrize("command", ["push-changes", "sync"])
@pytest.mark.parametrize("fault", ["missing-lineage", "empty-lineage", "repointed-ref", "missing-ref"])
def test_completed_checkpoint_corruption_cannot_be_absent_history(
    completed_publication, monkeypatch, command, fault,
):
    config, wid, wt, remote, saved, branch, published = completed_publication
    path = pr_recovery._path(str(wt))
    data = json.loads(path.read_text())
    point = data["checkpoints"][record(wid).pr.pr_id]
    if fault == "missing-lineage":
        point.pop("lineage_ref")
    elif fault == "empty-lineage":
        point["lineage_ref"] = ""
    elif fault == "repointed-ref":
        git("update-ref", point["published_ref"], published, cwd=wt)
    else:
        git("update-ref", "-d", point["published_ref"], cwd=wt)
    tracking._atomic_write(path, json.dumps(data) + "\n")
    source = git("rev-parse", "HEAD", cwd=wt)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Corrupt evidence must not publish."))
    function = finalize.push_changes if command == "push-changes" else git_collab.sync_forward
    assert not function(wid, config)
    assert record(wid).pr.head_sha == saved
    assert git("rev-parse", "HEAD", cwd=wt) == source
    assert pr_publish._tip("origin", branch, str(wt)) == published
    assert not list(metadata(wt).glob("*pr-reconciliation-*.json"))


@pytest.mark.parametrize("evidence", ["backed", "inapplicable"])
def test_completed_checkpoint_settlement_preserves_normal_authority(
    completed_publication, monkeypatch, evidence,
):
    config, wid, wt, remote, saved, branch, published = completed_publication
    if evidence == "inapplicable":
        path = pr_recovery._path(str(wt))
        data = json.loads(path.read_text())
        current = record(wid).pr
        previous = replace(current, url=current.url.replace("gitea.example.com", "GITEA.EXAMPLE.COM"))
        assert previous.url != current.url
        data["checkpoints"][current.pr_id]["association"] = pr_recovery._association(previous)
        tracking._atomic_write(path, json.dumps(data) + "\n")
    source = git("rev-parse", "HEAD", cwd=wt)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Settlement must not publish."))
    assert pr_routine_recovery.reconcile(config, record(wid), cwd=str(wt)) == source
    assert record(wid).pr.head_sha == published
    assert git("rev-parse", "HEAD", cwd=wt) == source
    assert pr_publish._tip("origin", branch, str(wt)) == published
    audit = json.loads(next(metadata(wt).glob("*pr-reconciliation-*.json")).read_text())
    assert audit["outcome"] == "succeeded"
    assert audit["saved_publication"]["head_sha"] == saved
    assert audit["operation"]["evidence_status"] == (
        "backed-sync" if evidence == "backed" else "source-lineage"
    )


@pytest.mark.timeout(300)
@pytest.mark.parametrize("owned_publication", ["upstream-merge"], indirect=True)
def test_source_lineage_settlement_then_sync_and_publish_across_upstream_merge(
    owned_publication, monkeypatch,
):
    config, wid, wt, remote, _saved, branch, published = owned_publication
    accepted_base = git("merge-base", published, "origin/master", cwd=wt)
    assert len(git("show", "-s", "--format=%P", accepted_base, cwd=wt).split()) == 2
    source = git("rev-parse", "HEAD", cwd=wt)
    monkeypatch.setattr(
        pr_break_glass, "confirm",
        lambda *a, **k: pytest.fail("Owned continuation must not request operator recovery."),
    )
    assert pr_routine_recovery.reconcile(config, record(wid), cwd=str(wt)) == source
    settled_base = record(wid).pr.base_sha
    anchor = Path(config.default_repo.anchor)
    (anchor / "later-upstream.txt").write_text("later upstream\n")
    git("add", "later-upstream.txt", cwd=anchor)
    git("commit", "-m", "advance merged upstream", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)
    assert git_collab.sync_forward(wid, config)
    assert finalize.push_changes(wid, config)
    assert pr_publish._tip("origin", branch, str(wt)) == git("rev-parse", "HEAD", cwd=wt)
    assert settled_base == accepted_base
    assert record(wid).pr.base_sha == git("rev-parse", "origin/master", cwd=wt)
    assert git("show", "HEAD:unpublished.txt", cwd=wt) == "keep this unpublished"
