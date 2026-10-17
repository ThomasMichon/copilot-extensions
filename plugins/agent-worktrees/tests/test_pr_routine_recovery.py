"""Normal entrypoints reconcile backed publication without override consent."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_pr_rebase import _advance, _prepare
from test_pr_recovery_advanced_head import backed_publication, git, metadata, record

from agent_worktrees import (
    finalize,
    git_collab,
    git_ops,
    pr_publish,
    pr_ops,
    pr_recovery,
    pr_rewrite,
    pr_routine_recovery,
    tracking,
)
from agent_worktrees.providers.base import ProviderError, PullResult

pytestmark = [
    pytest.mark.contract("agent_worktrees.pr.operator_recovery"),
    pytest.mark.timeout(120),
]

@pytest.fixture
def owned_publication(pr_repo, monkeypatch):
    real_slug = pr_publish.push_slug
    publication = backed_publication(pr_repo, monkeypatch, with_base=True)
    _config, wid, wt, _remote, _saved, branch, _published = publication
    monkeypatch.setattr(git_ops, "slug_from_url", lambda *a: "example/project")
    monkeypatch.setattr(pr_publish, "push_slug", real_slug)
    current = record(wid)
    current.pr.rewrite_owner = f"{wid}:{branch}"
    current.pr.rewrite_identity = pr_publish.push_identity("origin", cwd=str(wt))
    tracking.save_record(current)
    return publication


@pytest.mark.parametrize("scheme", ["refspec", "snapshot"])
def test_push_changes_reconciles_then_publishes_local_followup(
    owned_publication, monkeypatch, scheme,
) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication
    config = replace(config, repos={config.repo_name: replace(
        config.default_repo, pr=replace(config.default_repo.pr, head_scheme=scheme),
    )})
    source = git("rev-parse", "HEAD", cwd=wt)
    real_push = git_ops.push
    calls = []

    def push(*args, **kwargs):
        calls.append(kwargs)
        return real_push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", push)
    assert finalize.push_changes(wid, config)
    assert record(wid).pr.head_sha == source != published != saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == source
    assert len(calls) == 1
    assert not calls[0].get("force", False)
    assert not calls[0].get("allow_history_rewrite", False)
    assert not calls[0].get("force_with_lease", False)
    assert calls[0].get("force_with_lease_expect") is None
    assert calls[0]["timeout"] == config.default_repo.pr.push_timeout_seconds
    audit = json.loads(next(metadata(wt).glob("*pr-reconciliation-*.json")).read_text())
    assert audit["outcome"] == "succeeded"
    assert audit["operation"]["overrides"] == []
    assert audit["saved_publication"]["head_sha"] == saved
    for name, commit in (("source", source), ("saved", saved), ("observed", published)):
        assert git("rev-parse", audit["recovery_refs"][name], cwd=wt) == commit
    assert not list(metadata(wt).glob("*break-glass*.json"))


def test_sync_forward_reconciles_before_retaining_next_transition(owned_publication) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication
    anchor = Path(config.default_repo.anchor)
    (anchor / "next-upstream.txt").write_text("next upstream\n")
    git("add", "next-upstream.txt", cwd=anchor)
    git("commit", "-m", "next upstream", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)

    assert git_collab.sync_forward(wid, config)
    assert record(wid).pr.head_sha == published != saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
    assert git("show", "HEAD:unpublished.txt", cwd=wt) == "keep this unpublished"
    assert finalize.push_changes(wid, config)
    assert record(wid).pr.head_sha == git("rev-parse", "HEAD", cwd=wt)


def invoke(command, config, wid, **kwargs):
    function = finalize.push_changes if command == "push-changes" else git_collab.sync_forward
    return function(wid, config, **kwargs)


@pytest.mark.parametrize("recovery", ["ordinary", "after-reconciliation", "source-rebase"])
def test_numbered_pr_preserves_verified_lost_ack_fallbacks(
    pr_repo, monkeypatch, recovery,
) -> None:
    if recovery == "after-reconciliation":
        real_slug = pr_publish.push_slug
        config, wid, wt, remote, _saved, branch, _published = backed_publication(
            pr_repo, monkeypatch, with_base=True,
        )
        monkeypatch.setattr(git_ops, "slug_from_url", lambda *a: "example/project")
        monkeypatch.setattr(pr_publish, "push_slug", real_slug)
        current = record(wid)
        current.pr.rewrite_owner = f"{wid}:{branch}"
        current.pr.rewrite_identity = pr_publish.push_identity("origin", cwd=str(wt))
        tracking.save_record(current)
        assert pr_routine_recovery.reconcile(config, record(wid), cwd=str(wt))
    else:
        config, wid, wt, remote, branch, _published = _prepare(pr_repo)
        current = record(wid)
        current.pr.number = 42
        current.pr.repo = "example/project"
        current.pr.url = "https://gitea.example.com/example/project/pulls/42"
        tracking.save_record(current)
        monkeypatch.setattr(git_ops, "remote_slug", lambda *a, **k: "example/project")
        monkeypatch.setattr(git_ops, "slug_from_url", lambda *a: "example/project")
        monkeypatch.setattr(pr_ops, "refresh_head_observation", lambda *a: "")
        monkeypatch.setattr(pr_rewrite, "observe_pr", lambda config, pr: PullResult(
            number=42, head_sha=pr_publish._tip("origin", branch, str(wt)),
            head_ref=branch, head_url=str(remote),
        ))
    saved = record(wid).pr.head_sha
    (wt / "accepted-feedback.txt").write_text("accepted source feedback\n")
    git("add", "accepted-feedback.txt", cwd=wt)
    git("commit", "-m", "accepted source feedback", cwd=wt)
    accepted = git("rev-parse", "HEAD", cwd=wt)
    push = git_ops.push

    def lose_ack(*args, **kwargs):
        assert push(*args, **kwargs)
        return git_ops.PushResult(ok=False, stderr="transport acknowledgement lost")

    with monkeypatch.context() as patch:
        patch.setattr(git_ops, "push", lose_ack)
        assert not finalize.push_changes(wid, config)
    assert record(wid).pr.number == 42
    assert record(wid).pr.head_sha == saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == accepted
    (wt / "later-feedback.txt").write_text("retain unpublished feedback\n")
    git("add", "later-feedback.txt", cwd=wt)
    git("commit", "-m", "later feedback", cwd=wt)
    if recovery == "source-rebase":
        _advance(config, wt)
        git("rebase", "origin/master", cwd=wt)
        assert not git_ops.is_commit_ancestor(accepted, git("rev-parse", "HEAD", cwd=wt), cwd=str(wt))
        monkeypatch.setattr(pr_publish, "_provider_head", lambda *a, **k: accepted)
    source = git("rev-parse", "HEAD", cwd=wt)
    assert finalize.push_changes(wid, config)
    assert record(wid).pr.head_sha == source
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == source
    assert git("show", "HEAD:accepted-feedback.txt", cwd=wt) == "accepted source feedback"
    assert git("show", "HEAD:later-feedback.txt", cwd=wt) == "retain unpublished feedback"


@pytest.mark.parametrize("command", ["push-changes", "sync"])
@pytest.mark.parametrize("failure", [
    "provider-error", "provider-head", "provider-closed", "provider-number", "provider-branch",
    "provider-destination", "destination", "ownership", "duplicate", "evidence", "mutable-target",
    "unrelated-source",
])
def test_normal_entrypoints_refuse_unproved_reconciliation(
    owned_publication, monkeypatch, command, failure,
) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication
    if failure.startswith("provider"):
        observed = PullResult(
            number=42, head_sha=published, head_ref=branch, head_url=str(remote),
        )
        changes = {
            "provider-head": {"head_sha": saved},
            "provider-closed": {"state": "closed"},
            "provider-number": {"number": 43},
            "provider-branch": {"head_ref": "pr/other"},
            "provider-destination": {"head_url": str(remote.parent / "other.git")},
        }

        def observe(*args):
            if failure == "provider-error":
                raise ProviderError("provider unavailable")
            return replace(observed, **changes[failure])

        monkeypatch.setattr(pr_rewrite, "observe_pr", observe)
    elif failure == "destination":
        git("config", "remote.origin.pushurl", str(remote.parent / "elsewhere.git"), cwd=wt)
    elif failure in ("ownership", "duplicate"):
        current = record(wid)
        if failure == "ownership":
            current.pr.rewrite_owner = f"another-owner:{branch}"
        else:
            current.prs.append(replace(current.pr, pr_id="another-live-pr"))
        tracking.save_record(current)
    elif failure == "evidence":
        pr_recovery._path(str(wt)).write_text("invalid checkpoint\n")
    elif failure == "mutable-target":
        data = json.loads(pr_recovery._path(str(wt)).read_text())
        data["checkpoints"][record(wid).pr.pr_id]["target_head"] = "origin/master"
        pr_recovery._path(str(wt)).write_text(json.dumps(data))
    else:
        git("reset", "--hard", saved, cwd=wt)
    source = git("rev-parse", "HEAD", cwd=wt)
    evidence = pr_recovery._path(str(wt)).read_bytes()
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Refused recovery must not push."))
    assert not invoke(command, config, wid)
    assert record(wid).pr.head_sha == saved
    assert git("rev-parse", "HEAD", cwd=wt) == source
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
    assert pr_recovery._path(str(wt)).read_bytes() == evidence
    assert not list(metadata(wt).glob("*pr-reconciliation-*.json"))


@pytest.mark.parametrize("command", ["push-changes", "sync"])
@pytest.mark.parametrize("changed", ["provider", "remote", "source", "tracking", "destination"])
def test_reconciliation_rechecks_after_retaining_objects(
    owned_publication, monkeypatch, command, changed,
) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication
    retain = pr_recovery.retain_rewrite

    def interpose(*args, **kwargs):
        point = retain(*args, **kwargs)
        if changed == "provider":
            monkeypatch.setattr(pr_rewrite, "observe_pr", lambda *a: PullResult(
                number=42, head_sha=saved, head_ref=branch, head_url=str(remote),
            ))
        elif changed == "remote":
            git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", saved, published, cwd=wt)
        elif changed == "source":
            (wt / "interposed.txt").write_text("later source\n")
            git("add", "interposed.txt", cwd=wt)
            git("commit", "-m", "interposed source", cwd=wt)
        elif changed == "tracking":
            current = record(wid)
            current.pr.base_sha = published
            tracking.save_record(current)
        else:
            git("config", "remote.origin.pushurl", str(remote.parent / "elsewhere.git"), cwd=wt)
        return point

    monkeypatch.setattr(pr_recovery, "retain_rewrite", interpose)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Changed proof must not push."))
    assert not invoke(command, config, wid)
    assert record(wid).pr.head_sha == saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == (
        saved if changed == "remote" else published
    )
    audit = json.loads(next(metadata(wt).glob("*pr-reconciliation-*.json")).read_text())
    assert audit["outcome"] == "failed"
    assert audit["saved_publication"]["head_sha"] == saved


@pytest.mark.parametrize("command", ["push-changes", "sync"])
def test_dry_run_does_not_reconcile(owned_publication, command) -> None:
    config, wid, wt, _remote, saved, _branch, _published = owned_publication
    before = git("show-ref", cwd=wt), pr_recovery._path(str(wt)).read_bytes()
    assert invoke(command, config, wid, dry_run=True)
    assert record(wid).pr.head_sha == saved
    assert (git("show-ref", cwd=wt), pr_recovery._path(str(wt)).read_bytes()) == before
    assert not list(metadata(wt).glob("*pr-reconciliation-*.json"))


def test_nonforcing_transport_preserves_racing_remote(owned_publication, monkeypatch) -> None:
    config, wid, wt, remote, _saved, branch, published = owned_publication
    racing = git(
        "commit-tree", f"{published}^{{tree}}", "-p", published, "-m", "concurrent remote work",
        cwd=wt,
    )
    git("--git-dir", str(remote), "fetch", "--no-tags", str(wt), racing, cwd=wt)
    push = git_ops.push
    calls = []

    def race(*args, **kwargs):
        calls.append(kwargs)
        git("--git-dir", str(remote), "update-ref", f"refs/heads/{branch}", racing, published, cwd=wt)
        return push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", race)
    assert not finalize.push_changes(wid, config)
    assert len(calls) == 1
    assert not calls[0].get("force_with_lease", False)
    assert calls[0].get("force_with_lease_expect") is None
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == racing
    assert record(wid).pr.head_sha == published


@pytest.mark.parametrize("command", ["push-changes", "sync"])
def test_real_managed_publication_with_lost_persistence_ack_recovers(
    owned_publication, monkeypatch, command,
) -> None:
    config, wid, wt, remote, _saved, branch, published = owned_publication
    assert pr_routine_recovery.reconcile(config, record(wid), cwd=str(wt))
    anchor = Path(config.default_repo.anchor)
    (anchor / "more-upstream.txt").write_text("next base\n")
    git("add", "more-upstream.txt", cwd=anchor)
    git("commit", "-m", "next base", cwd=anchor)
    git("push", "origin", "master", cwd=anchor)
    assert git_collab.sync_forward(wid, config)
    accepted = git("rev-parse", "HEAD", cwd=wt)

    def lose_ack(*args, **kwargs):
        raise OSError("publication persistence acknowledgement lost")

    with monkeypatch.context() as patch:
        patch.setattr(pr_publish, "record_pushed_head", lose_ack)
        with pytest.raises(OSError, match="acknowledgement lost"):
            finalize.push_changes(wid, config)
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == accepted
    assert record(wid).pr.head_sha == published != accepted
    (wt / "after-ack-loss.txt").write_text("later source\n")
    git("add", "after-ack-loss.txt", cwd=wt)
    git("commit", "-m", "source after lost ack", cwd=wt)
    assert invoke(command, config, wid)
    expected = git("rev-parse", "HEAD", cwd=wt) if command == "push-changes" else accepted
    assert record(wid).pr.head_sha == expected
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == expected


def test_reconciled_push_runs_normal_hook_with_pinned_source(owned_publication, monkeypatch) -> None:
    config, wid, wt, remote, _saved, branch, _published = owned_publication
    source = git("rev-parse", "HEAD", cwd=wt)
    hooks = remote.parent / "hooks"
    hooks.mkdir()
    marker = remote.parent / "hook-ran"
    hook = hooks / "pre-push"
    hook.write_text(f"#!/bin/sh\nprintf ran > '{marker.as_posix()}'\n")
    hook.chmod(0o755)
    git("config", "core.hooksPath", str(hooks), cwd=wt)
    push = git_ops.push

    def move_source(*args, **kwargs):
        (wt / "during-push.txt").write_text("do not substitute this source\n")
        git("add", "during-push.txt", cwd=wt)
        git("commit", "-m", "later source during publication", cwd=wt)
        assert args[1] == f"{source}:refs/heads/{branch}"
        return push(*args, **kwargs)

    monkeypatch.setattr(git_ops, "push", move_source)
    assert finalize.push_changes(wid, config)
    assert marker.read_text() == "ran"
    assert record(wid).pr.head_sha == source != git("rev-parse", "HEAD", cwd=wt)
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == source


def test_unrelated_branch_sync_does_not_reconcile_active_pr(owned_publication) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication
    checkpoint = pr_recovery._path(str(wt)).read_bytes()
    git("checkout", "-b", "unrelated", "origin/master", cwd=wt)
    assert git_collab.sync_forward(wid, config)
    assert record(wid).pr.head_sha == saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
    assert pr_recovery._path(str(wt)).read_bytes() == checkpoint
    assert not list(metadata(wt).glob("*pr-reconciliation-*.json"))


def test_failed_tracking_settlement_is_explicit_and_does_not_push(
    owned_publication, monkeypatch, capfd,
) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication

    def deny(*args, **kwargs):
        raise OSError("tracking unavailable")

    monkeypatch.setattr(pr_publish, "record_reconciled_head", deny)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Unsettled tracking must not push."))
    assert not finalize.push_changes(wid, config)
    assert "tracking not confirmed" in capfd.readouterr().out
    assert record(wid).pr.head_sha == saved
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
    audit = json.loads(next(metadata(wt).glob("*pr-reconciliation-*.json")).read_text())
    assert audit["outcome"] == "unknown"


def test_unwritable_audit_refuses_before_retaining_or_settling(owned_publication, monkeypatch) -> None:
    config, wid, wt, remote, saved, branch, published = owned_publication
    write = tracking._atomic_write
    before = git("show-ref", cwd=wt)

    def deny(path, content):
        if "pr-reconciliation-" in str(path):
            raise OSError("audit unavailable")
        return write(path, content)

    monkeypatch.setattr(tracking, "_atomic_write", deny)
    monkeypatch.setattr(git_ops, "push", lambda *a, **k: pytest.fail("Unaudited recovery must not push."))
    assert not finalize.push_changes(wid, config)
    assert record(wid).pr.head_sha == saved
    assert git("show-ref", cwd=wt) == before
    assert git("--git-dir", str(remote), "rev-parse", branch, cwd=wt) == published
