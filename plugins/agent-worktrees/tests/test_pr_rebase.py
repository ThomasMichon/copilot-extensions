"""Owned-PR rebase publication against real bare Git remotes (#5796)."""

from __future__ import annotations

import dataclasses
import json
import stat
from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import finalize, git_collab, git_ops, pr_ops, pr_publish, tracking

# Each case exercises multiple real publications and bounded Git graph probes.
pytestmark = pytest.mark.timeout(180)


def _git(*args, cwd):
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _record(wid):
    return tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")


def _prepare(pr_repo, scheme="refspec", branch=None):
    config, wid, path, remote = pr_repo
    repo = dataclasses.replace(
        config.default_repo, pr=dataclasses.replace(config.default_repo.pr, head_scheme=scheme),
    )
    config = dataclasses.replace(config, repos={"ext": repo})
    first = pr_ops.create_pr(wid, config, title="Own change", branch=branch)
    assert first["success"], first
    return config, wid, path, remote, first["branch"], first["head_sha"]


def _advance(config, path, filename="upstream.txt"):
    anchor = Path(config.default_repo.anchor)
    (anchor / filename).write_text("upstream advance\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "advance base", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=path)
    return _git("rev-parse", "origin/master", cwd=path)


def _publish(command, config, wid):
    if command == "push-changes":
        return finalize.push_changes(wid, config)
    result = pr_ops.create_pr(wid, config, title="Own change")
    return result.get("success", False)


@pytest.mark.parametrize("scheme,command,flow", [
    ("snapshot", "push-changes", "sync"),
    ("snapshot", "create-pr", "manual"),
    ("refspec", "push-changes", "manual"),
    ("refspec", "create-pr", "sync"),
])
def test_owned_pr_rebase_publishes_with_original_lease(pr_repo, scheme, command, flow):
    config, wid, path, remote, branch, old = _prepare(pr_repo, scheme)
    (path / "feedback.txt").write_text("preserve unpublished source work\n")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "feedback before rebase", cwd=path)
    onto = _advance(config, path)
    if flow == "sync":
        assert git_collab.sync_forward(wid, config)
        metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
        proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
        assert proof["expected_head"] == old
        assert proof["new_base"] == onto
    else:
        _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert not git_ops.is_commit_ancestor(old, tip, cwd=path)
    assert _publish(command, config, wid)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    record = _record(wid)
    assert record.pr.head_sha == tip
    assert record.pr.base_sha == onto
    assert record.pr.patch_id == pr_ops._patch_id(onto, tip, cwd=str(path))
    assert (path / "feedback.txt").read_text() == "preserve unpublished source work\n"
    if scheme == "refspec" and command == "push-changes":
        # Reuse after the rewrite is incremental again, with a refreshed base/head.
        (path / "later.txt").write_text("later feedback\n")
        _git("add", "-A", cwd=path)
        _git("commit", "-m", "later feedback", cwd=path)
        assert _publish(command, config, wid)


@pytest.mark.parametrize("command", ["push-changes", "create-pr"])
def test_owned_pr_rebase_rejects_concurrent_reviewer_commit(pr_repo, command):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    other = remote.parent / "reviewer"
    _git("clone", str(remote), str(other), cwd=remote.parent)
    _git("config", "user.email", "reviewer@example.com", cwd=other)
    _git("config", "user.name", "Reviewer", cwd=other)
    _git("checkout", "-B", branch, f"origin/{branch}", cwd=other)
    (other / "reviewer.txt").write_text("must not drop\n")
    _git("add", "-A", cwd=other)
    _git("commit", "-m", "reviewer's commit", cwd=other)
    foreign = _git("rev-parse", "HEAD", cwd=other)
    _git("push", "origin", branch, cwd=other)
    # Caller fetches the moved remote, but must not refresh the expected lease.
    assert not _publish(command, config, wid)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == foreign
    assert _record(wid).pr.head_sha == old


@pytest.mark.parametrize("mutation", ["reset", "same-tree-reset", "amend", "drop"])
def test_owned_pr_rebase_refuses_unproved_source_rewrite(pr_repo, mutation):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    _advance(config, path)
    if mutation == "reset":
        _git("reset", "--hard", "origin/master", cwd=path)
    elif mutation == "same-tree-reset":
        _git("rebase", "origin/master", cwd=path)
        _git("reset", "--hard", "HEAD", cwd=path)
    elif mutation == "amend":
        _git("rebase", "origin/master", cwd=path)
        (path / "a.txt").write_text("silently changed\n")
        _git("add", "-A", cwd=path)
        _git("commit", "--amend", "--no-edit", cwd=path)
    else:
        (path / "feedback.txt").write_text("source work\n")
        _git("add", "-A", cwd=path)
        _git("commit", "-m", "source feedback", cwd=path)
        # A real rebase journal alone does not authorize skipping the published patch.
        _git("rebase", "--onto", "origin/master", old, cwd=path)
    assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


def test_owned_pr_rebase_does_not_authorize_shared_default_or_foreign_heads(pr_repo):
    config, wid, path, remote, original_branch, old = _prepare(pr_repo)
    onto = _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    record = _record(wid)
    for branch in ("master", "feature/shared", "pr/someone-else"):
        record.pr.branch = branch
        if branch != "master":
            _git("push", "origin", f"{old}:refs/heads/{branch}", cwd=path)
        result = pr_publish.push_checked(
            record, "origin", f"{record.branch}:refs/heads/{branch}", cwd=str(path),
            force_with_lease_expect=old, repo=config.default_repo,
        )
        assert not result
        expected = onto if branch == "master" else old
        assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == expected
    assert _git("--git-dir", str(remote), "rev-parse", original_branch, cwd=path) == old


def test_owned_pr_rebase_keeps_generic_push_fail_closed(pr_repo):
    config, _, path, remote, branch, old = _prepare(pr_repo)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    result = git_ops.push(
        "origin", f"HEAD:refs/heads/{branch}", cwd=path, force_with_lease_expect=old,
    )
    assert not result
    assert "not an ancestor" in result.stderr
    missing = git_ops.push(
        "origin", f"HEAD:refs/heads/{branch}", cwd=path,
        force_with_lease_expect="1" * 40,
    )
    assert not missing
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


def test_owned_pr_rebase_publishes_legacy_checked_out_private_head(pr_repo):
    config, wid, path, remote, branch, old = _prepare(pr_repo, "snapshot")
    _git("checkout", branch, cwd=path)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert tip != old
    assert _publish("create-pr", config, wid)
    assert _record(wid).pr.head_sha == tip
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip


def test_owned_pr_rebase_recovers_legacy_stale_published_patch_cache(pr_repo):
    config, wid, path, remote, branch, _ = _prepare(pr_repo)
    original_patch = _record(wid).pr.patch_id
    (path / "reviewed-feedback.txt").write_text("already published\n")
    _git("add", "-A", cwd=path)
    _git("commit", "-m", "reviewed feedback", cwd=path)
    assert finalize.push_changes(wid, config)
    record = _record(wid)
    old = record.pr.head_sha
    # Reproduce older runtimes: their published head advanced but patch_id did not.
    record.pr.patch_id = original_patch
    tracking.save_record(record)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
    proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
    assert proof["expected_head"] == old
    assert proof["recorded_patch_id"] == original_patch
    assert proof["published_patch_id"] != original_patch
    assert (path / "reviewed-feedback.txt").read_text() == "already published\n"


@pytest.mark.parametrize("scheme", ["snapshot", "refspec"])
def test_owned_pr_rebase_honors_real_pre_push_rejection(pr_repo, scheme, capsys):
    config, wid, path, remote, branch, old = _prepare(pr_repo, scheme)
    _advance(config, path)
    _git("rebase", "origin/master", cwd=path)
    common = Path(_git("rev-parse", "--git-common-dir", cwd=path))
    if not common.is_absolute():
        common = path / common
    hook = common / "hooks" / "pre-push"
    hook.write_text("#!/bin/sh\necho 'BLOCKED: release guard'\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IEXEC)
    assert not finalize.push_changes(wid, config)
    output = capsys.readouterr()
    assert "BLOCKED: release guard" in output.out + output.err
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == old


@pytest.mark.parametrize("command", ["push-changes", "create-pr"])
def test_owned_pr_rebase_publishes_explicit_conflict_continuation(pr_repo, command):
    config, wid, path, remote, branch, old = _prepare(pr_repo)
    # The resolver's explicit continuation changes a patch; retain one-to-one
    # source identity, base lineage, and all other source work.
    anchor = Path(config.default_repo.anchor)
    (anchor / "a.txt").write_text("upstream's different content\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "conflicting base", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=path)
    result = git_ops.git("rebase", "origin/master", cwd=path, check=False)
    assert result.returncode
    (path / "a.txt").write_text("new resolution\n")
    _git("add", "-A", cwd=path)
    git_ops.git("-c", "core.editor=true", "rebase", "--continue", cwd=path)
    tip = _git("rev-parse", "HEAD", cwd=path)
    assert _publish(command, config, wid)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == tip
    assert (path / "b.txt").read_text() == "two\n"
    metadata = Path(_git("rev-parse", "--absolute-git-dir", cwd=path))
    proof = json.loads((metadata / "agent-worktrees-pr-rebase.json").read_text())
    assert proof["conflict_replays"] == [[old, tip]]
