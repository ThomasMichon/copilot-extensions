"""Exact squash boundaries with independent edits in a shared file."""

from pathlib import Path

import pytest

from agent_worktrees import git_ops, pr_complete, pr_ops, tracking


def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


def _concurrent_squash(
    pr_repo, *, incomplete: bool = False, state: str = "merged",
):
    config, wid, worktree, _ = pr_repo
    anchor = Path(config.default_repo.anchor)
    branch = f"worktree/{wid}"
    _git("reset", "--hard", "origin/master", cwd=worktree)
    _git("config", "diff.interHunkContext", "20", cwd=anchor)
    original = (
        "left=old\n" + "unchanged\n" * 2 + "right=old\n"
        + "stable\n" * 4 + "tail=old\n"
    )
    (anchor / "manifest.txt").write_text(original)
    _git("add", "manifest.txt", cwd=anchor)
    _git("commit", "-m", "shared manifest", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=worktree)
    _git("reset", "--hard", "origin/master", cwd=worktree)
    base = _git("rev-parse", "HEAD", cwd=worktree)

    (worktree / "manifest.txt").write_text(
        original.replace("left=old", "left=pr").replace("tail=old", "tail=pr"),
    )
    (worktree / "feature.txt").write_text("complete PR content\n")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-m", "PR changes", cwd=worktree)
    head = _git("rev-parse", "HEAD", cwd=worktree)
    record = tracking.load_record_by_id(wid)
    assert record is not None
    record.pr = tracking.PRRecord(state=state, head_sha=head, base_sha=base)
    tracking.save_record(record)

    upstream = original.replace("right=old", "right=upstream")
    (anchor / "manifest.txt").write_text(upstream)
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "independent manifest change", cwd=anchor)
    parent = _git("rev-parse", "HEAD", cwd=anchor)
    (anchor / "manifest.txt").write_text(
        upstream.replace(
            "left=old", "left=conflicting" if incomplete else "left=pr",
        ).replace("tail=old", "tail=pr"),
    )
    (anchor / "feature.txt").write_text("complete PR content\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "squash PR", cwd=anchor)
    squash = _git("rev-parse", "HEAD", cwd=anchor)
    _git("push", "origin", "master", cwd=anchor)
    _git("fetch", "origin", cwd=worktree)
    assert pr_ops._patch_id(base, head, cwd=str(worktree)) != pr_ops._patch_id(
        parent, squash, cwd=str(worktree),
    )
    if not incomplete:
        context_free = pr_ops._patch_id(
            base, head, cwd=str(worktree), ignore_context=True,
        )
        assert context_free == pr_ops._patch_id(
            parent, squash, cwd=str(worktree), ignore_context=True,
        )
        assert squash in pr_ops._commit_patch_ids(
            base, "origin/master", cwd=str(worktree), ignore_context=True,
        )[context_free]
    return config, wid, worktree, branch, base, head, squash


@pytest.mark.parametrize("followups", [0, 4])
def test_concurrent_manifest_boundary_preserves_only_post_pr_commits(
    pr_repo, followups,
):
    config, wid, worktree, branch, base, head, squash = _concurrent_squash(pr_repo)
    assert pr_complete._is_exact_squash_result(
        base, head, squash, cwd=str(worktree),
    )
    for index in range(followups):
        (worktree / "journal.txt").write_text(f"follow-up {index}\n")
        _git("add", "journal.txt", cwd=worktree)
        _git("commit", "-m", f"journal follow-up {index}", cwd=worktree)
    if followups:
        anchor = Path(config.default_repo.anchor)
        manifest = anchor / "manifest.txt"
        manifest.write_text(manifest.read_text().replace("left=pr", "left=later"))
        _git("add", "manifest.txt", cwd=anchor)
        _git("commit", "-m", "later overlapping upstream edit", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)
        _git("fetch", "origin", cwd=worktree)
    before = _git("rev-parse", "HEAD", cwd=worktree)

    assert pr_complete._merged_pr_head(
        wid, branch, "origin/master", cwd=str(worktree),
    ) == (head, followups)
    preview = pr_complete.complete_worktree(wid, config, dry_run=True)
    assert preview["success"] is True, preview
    assert preview["kept"] == followups
    assert _git("rev-parse", "HEAD", cwd=worktree) == before

    result = pr_complete.complete_worktree(wid, config)

    assert result["success"] is True, result
    assert result["action"] == ("rebased" if followups else "reset-past-squash")
    assert result.get("kept", 0) == followups
    assert result["dropped"] == 1
    assert _git("rev-parse", pr_complete.BACKUP_REF, cwd=worktree) == before
    assert _git("rev-list", "--count", "origin/master..HEAD", cwd=worktree) == str(followups)
    assert _git("show", "HEAD:manifest.txt", cwd=worktree) == _git(
        "show", "origin/master:manifest.txt", cwd=worktree,
    )
    if followups:
        assert (worktree / "journal.txt").read_text() == "follow-up 3\n"
        assert _git("log", "--format=%s", "origin/master..HEAD", cwd=worktree).splitlines() == [
            f"journal follow-up {index}" for index in reversed(range(followups))
        ]


def test_context_free_boundary_requires_terminal_state_and_readable_candidates(
    pr_repo, monkeypatch,
):
    _config, wid, worktree, branch, _base, head, _squash = _concurrent_squash(
        pr_repo, state="open",
    )
    record = tracking.load_record_by_id(wid)
    assert record is not None
    assert record.pr is not None
    assert record.pr.state == "open"
    assert pr_complete._merged_pr_head(
        wid, branch, "origin/master", cwd=str(worktree),
    ) is None

    record.pr.state = "merged"
    tracking.save_record(record)
    monkeypatch.setattr(pr_ops, "_commit_patch_ids", lambda *_args, **_kwargs: {})
    assert pr_complete._merged_pr_head(
        wid, branch, "origin/master", cwd=str(worktree),
    ) is None
    assert _git("rev-parse", "HEAD", cwd=worktree) == head


def test_merged_label_cannot_prove_incomplete_concurrent_squash(pr_repo):
    config, wid, worktree, branch, base, head, squash = _concurrent_squash(
        pr_repo, incomplete=True,
    )
    before = _git("rev-parse", "HEAD", cwd=worktree)

    assert not pr_complete._is_exact_squash_result(
        base, head, squash, cwd=str(worktree),
    )
    assert pr_complete._merged_pr_head(
        wid, branch, "origin/master", cwd=str(worktree),
    ) is None
    result = pr_complete.complete_worktree(wid, config)

    assert result["success"] is False, result
    assert result["action"] == "error"
    assert "aborted" in result["error"]
    assert _git("rev-parse", "HEAD", cwd=worktree) == before
    assert _git("rev-parse", pr_complete.BACKUP_REF, cwd=worktree) == before
