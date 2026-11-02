"""External native-style rebase must not acquire the submitter's push lease."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import finalize, git_ops, pr_complete, pr_publish, tracking
from test_pr_rebase import _advance, _git, _prepare, _record


def _externally_rebased(pr_repo, *, unpublished: bool) -> SimpleNamespace:
    config, wid, path, remote, branch, published = _prepare(pr_repo)
    if unpublished:
        (path / "unpublished.txt").write_text("retain the submitter's local follow-up\n")
        _git("add", "unpublished.txt", cwd=path)
        _git("commit", "-m", "unpublished follow-up", cwd=path)
    local_tip = _git("rev-parse", "HEAD", cwd=path)
    base = _advance(config, path)

    # The provider changes only the published branch, with an exact old-head lease.
    steward = remote.parent / "external-steward"
    _git("clone", str(remote), str(steward), cwd=remote.parent)
    _git("checkout", "-B", branch, f"origin/{branch}", cwd=steward)
    identity = ("-c", "user.email=steward@example.com", "-c", "user.name=Completion Steward")
    _git(*identity, "rebase", "origin/master", cwd=steward)
    rewritten = _git("rev-parse", "HEAD", cwd=steward)
    assert rewritten != published
    assert not git_ops.is_commit_ancestor(published, rewritten, cwd=steward)
    _git(
        "push", f"--force-with-lease=refs/heads/{branch}:{published}",
        "origin", f"HEAD:refs/heads/{branch}", cwd=steward,
    )
    assert _git("rev-parse", "HEAD", cwd=path) == local_tip
    assert _record(wid).pr.head_sha == published
    return SimpleNamespace(
        config=config, wid=wid, path=path, remote=remote, branch=branch, published=published,
        local_tip=local_tip, rewritten=rewritten, base=base, steward=steward, identity=identity,
    )


def test_external_rebase_refuses_to_acquire_the_submitters_publication_lease(
    pr_repo, monkeypatch,
) -> None:
    state = _externally_rebased(pr_repo, unpublished=True)
    config, wid, path, remote = state.config, state.wid, state.path, state.remote
    branch, published, rewritten = state.branch, state.published, state.rewritten
    monkeypatch.setattr(pr_publish, "_provider_head", lambda *args, **kwargs: rewritten)

    # Provider agreement is not permission to overwrite a non-source rewrite.
    assert not finalize.push_changes(wid, config)
    assert _git("--git-dir", str(remote), "rev-parse", branch, cwd=path) == rewritten
    assert _git("rev-parse", "HEAD", cwd=path) == state.local_tip
    assert _record(wid).pr.head_sha == published
    assert (path / "unpublished.txt").read_text() == "retain the submitter's local follow-up\n"


@pytest.mark.parametrize("unpublished", [False, True])
def test_verified_merge_after_external_rebase_preserves_work_and_backup(pr_repo, unpublished) -> None:
    state = _externally_rebased(pr_repo, unpublished=unpublished)
    config, wid, path = state.config, state.wid, state.path

    # Model the later provider-confirmed squash merge, retaining source attribution.
    merged = _git(
        *state.identity, "commit-tree", f"{state.rewritten}^{{tree}}", "-p", state.base,
        "-m", "verified controller squash merge", cwd=state.steward,
    )
    _git("push", "origin", f"{merged}:refs/heads/master", cwd=state.steward)
    record = _record(wid)
    record.pr.state = "merged"
    tracking.save_record(record)

    result = pr_complete.complete_worktree(wid, config)

    assert result["success"], result
    assert _git("rev-parse", "origin/master", cwd=path) == merged
    assert _git("rev-parse", result["backup_ref"], cwd=path) == state.local_tip
    assert (path / "a.txt").read_text() == "one\n"
    assert (path / "b.txt").read_text() == "two\n"
    if unpublished:
        assert result["action"] == "rebased" and result["kept"] == 1
        assert (path / "unpublished.txt").read_text() == "retain the submitter's local follow-up\n"
    else:
        assert _git("rev-parse", "HEAD", cwd=path) == merged
    assert _git("cat-file", "-t", state.local_tip, cwd=path) == "commit"
    assert Path(path).exists()
