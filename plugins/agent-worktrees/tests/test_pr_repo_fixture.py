"""Isolation contracts for the reusable PR repository seed."""

from pathlib import Path

import pytest

from agent_worktrees import git_ops, tracking


@pytest.mark.parametrize("iteration", range(2))
def test_pr_repo_copy_relocates_links_and_isolates_mutations(
    pr_repo, _pr_repo_seed, iteration
):
    config, wid, worktree, remote = pr_repo
    anchor = Path(config.default_repo.anchor)
    seed_anchor = _pr_repo_seed / "anchor"
    seed_remote = _pr_repo_seed / "remote.git"
    seed_worktree = _pr_repo_seed / "worktrees" / wid
    seed_tip = git_ops.git("rev-parse", "HEAD", cwd=seed_worktree).stdout
    seed_config = (seed_anchor / ".git" / "config").read_bytes()
    seed_link = (seed_worktree / ".git").read_bytes()
    assert (anchor / ".git" / "hooks").is_dir()
    assert (remote / "hooks").is_dir()
    hook = anchor / ".git" / "hooks" / "fixture-only"
    hook.write_text("independent hook state\n", encoding="utf-8")
    assert not (seed_anchor / ".git" / "hooks" / hook.name).exists()

    listing = git_ops.git("worktree", "list", "--porcelain", cwd=anchor).stdout
    assert str(worktree).replace("\\", "/") in listing
    assert str(_pr_repo_seed).replace("\\", "/") not in listing
    assert git_ops.git("remote", "get-url", "origin", cwd=anchor).stdout.strip() == str(
        remote
    )
    assert git_ops.git("rev-list", "--count", "master..HEAD", cwd=worktree).stdout.strip() == "2"

    (worktree / "isolated.txt").write_text(f"copy {iteration}\n", encoding="utf-8")
    git_ops.git("add", "isolated.txt", cwd=worktree)
    git_ops.git("commit", "-m", "isolated change", cwd=worktree)
    git_ops.git("push", "origin", "HEAD:refs/heads/isolated", cwd=worktree)

    assert not (seed_worktree / "isolated.txt").exists()
    assert git_ops.git("rev-parse", "HEAD", cwd=seed_worktree).stdout == seed_tip
    assert (seed_anchor / ".git" / "config").read_bytes() == seed_config
    assert (seed_worktree / ".git").read_bytes() == seed_link
    assert git_ops.git(
        "show-ref", "--verify", "refs/heads/isolated", cwd=seed_remote, check=False
    ).returncode != 0


@pytest.mark.parametrize("iteration", range(2))
def test_published_pr_copy_preserves_state_and_isolates_updates(
    published_pr_repo, _published_pr_seed, iteration
):
    config, wid, worktree, remote = published_pr_repo
    record = tracking.load_record_by_id(wid)
    assert record is not None
    assert record.worktree_path == str(worktree)
    assert record.pr is not None and record.pr.state == "open"
    assert record.pr.repo == git_ops.slug_from_url(str(remote))
    branch = record.pr.branch
    original_head = record.pr.head_sha
    assert git_ops.git("rev-parse", branch, cwd=worktree).stdout.strip() == original_head
    assert git_ops.git(
        "--git-dir", str(remote), "rev-parse", branch
    ).stdout.strip() == original_head
    seed_files = {
        path.relative_to(_published_pr_seed): path.read_bytes()
        for path in _published_pr_seed.rglob("*") if path.is_file()
    }

    (worktree / "published-only.txt").write_text(f"copy {iteration}\n", encoding="utf-8")
    git_ops.git("add", "published-only.txt", cwd=worktree)
    git_ops.git("commit", "-m", "isolated publication update", cwd=worktree)
    git_ops.git("push", "origin", f"HEAD:refs/heads/{branch}", cwd=worktree)
    record.pr.head_sha = git_ops.git("rev-parse", "HEAD", cwd=worktree).stdout.strip()
    tracking.save_record(record)
    assert record.pr.head_sha != original_head
    assert git_ops.git(
        "--git-dir", str(remote), "rev-parse", branch
    ).stdout.strip() == record.pr.head_sha
    assert {
        path.relative_to(_published_pr_seed): path.read_bytes()
        for path in _published_pr_seed.rglob("*") if path.is_file()
    } == seed_files
