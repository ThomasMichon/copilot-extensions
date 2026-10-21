"""Isolation contracts for the reusable base PR repository seed."""

from pathlib import Path

import pytest

from agent_worktrees import git_ops, pr_publish, tracking
from conftest import _copy_pr_repo


def _files(root):
    return {
        path.relative_to(root): path.read_bytes()
        for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("iteration", range(2))
def test_pr_repo_copy_relocates_links_and_isolates_mutations(
    pr_repo, _pr_repo_seed, iteration,
):
    config, wid, worktree, remote = pr_repo
    anchor = Path(config.default_repo.anchor)
    seed_anchor = _pr_repo_seed / "anchor"
    seed_remote = _pr_repo_seed / "remote.git"
    seed_worktree = _pr_repo_seed / "worktrees" / wid
    seed_files = _files(_pr_repo_seed)
    seed_tip = git_ops.git("rev-parse", "HEAD", cwd=seed_worktree).stdout
    seed_config = (seed_anchor / ".git" / "config").read_bytes()
    seed_link = (seed_worktree / ".git").read_bytes()
    assert (anchor / ".git" / "hooks").is_dir()
    assert (remote / "hooks").is_dir()
    assert not list((anchor / ".git" / "hooks").iterdir())
    assert not list((remote / "hooks").iterdir())
    assert git_ops.git(
        "config", "--get", "maintenance.auto", cwd=anchor,
    ).stdout.strip() == "false"
    assert git_ops.git(
        "--git-dir", str(remote), "config", "--get", "maintenance.auto",
    ).stdout.strip() == "false"
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
    assert Path(git_ops.git(
        "rev-parse", "--path-format=absolute", "--git-common-dir", cwd=worktree,
    ).stdout.strip()) == anchor / ".git"
    for store in (seed_anchor / ".git", seed_remote):
        assert not (store / "objects" / "info" / "alternates").exists()
        for source in store.rglob("*"):
            if source.is_file():
                assert not source.samefile(remote.parent / source.relative_to(_pr_repo_seed))

    (worktree / "isolated.txt").write_text(f"copy {iteration}\n", encoding="utf-8")
    git_ops.git("add", "isolated.txt", cwd=worktree)
    git_ops.git("commit", "-m", "isolated change", cwd=worktree)
    git_ops.git("push", "origin", "HEAD:refs/heads/isolated", cwd=worktree)
    git_ops.git("config", "fixture.private", "only-copy", cwd=worktree)
    record = tracking.load_record_by_id(wid)
    assert record is not None
    assert record.worktree_path == str(worktree)
    assert record.prs == []
    record.title = "private record"
    tracking.save_record(record)

    assert not (seed_worktree / "isolated.txt").exists()
    assert git_ops.git("rev-parse", "HEAD", cwd=seed_worktree).stdout == seed_tip
    assert (seed_anchor / ".git" / "config").read_bytes() == seed_config
    assert (seed_worktree / ".git").read_bytes() == seed_link
    assert git_ops.git(
        "show-ref", "--verify", "refs/heads/isolated", cwd=seed_remote, check=False,
    ).returncode != 0
    assert _files(_pr_repo_seed) == seed_files


def test_sibling_copy_has_independent_stores_config_index_hooks_refs_and_records(
    _pr_repo_seed, tmp_path,
):
    seed_files = _files(_pr_repo_seed)
    with pytest.MonkeyPatch.context() as patch:
        config, wid, first, remote = _copy_pr_repo(_pr_repo_seed, tmp_path / "first", patch)
        anchor = Path(config.default_repo.anchor)
        (first / "only-first").write_text("private", encoding="utf-8")
        git_ops.git("add", "only-first", cwd=first)
        git_ops.git("commit", "-m", "first-only commit", cwd=first)
        git_ops.git("push", "origin", "HEAD:refs/heads/only-first", cwd=first)
        git_ops.git("config", "fixture.private", "only-first", cwd=first)
        (anchor / ".git" / "hooks" / "only-first").write_text("private hook")
        record = tracking.load_record_by_id(wid)
        record.title = "only-first"
        tracking.save_record(record)
    with pytest.MonkeyPatch.context() as patch:
        config, wid, second, remote = _copy_pr_repo(_pr_repo_seed, tmp_path / "second", patch)
        assert not (second / "only-first").exists()
        assert not (Path(config.default_repo.anchor) / ".git" / "hooks" / "only-first").exists()
        assert git_ops.git("config", "--get", "fixture.private", cwd=second, check=False).returncode != 0
        assert git_ops.git(
            "--git-dir", str(remote), "show-ref", "--verify",
            "refs/heads/only-first", check=False,
        ).returncode != 0
        assert git_ops.git("status", "--porcelain", cwd=second).stdout == ""
        assert tracking.load_record_by_id(wid).title is None
    assert _files(_pr_repo_seed) == seed_files


@pytest.mark.parametrize("rewrite_identity", [False, True])
def test_copy_relocates_saved_record_and_fork_without_changing_seed(
    _pr_repo_seed, tmp_path, rewrite_identity,
):
    """Synthetic persisted metadata exercises relocation, not publication authority."""
    seed = tmp_path / "record-seed"
    with pytest.MonkeyPatch.context() as patch:
        config, wid, worktree, remote = _copy_pr_repo(_pr_repo_seed, seed, patch)
        git_ops.git("clone", "--bare", str(remote), str(seed / "fork.git"))
        git_ops.git("remote", "add", "fork", str(seed / "fork.git"), cwd=worktree)
        record = tracking.load_record_by_id(wid)
        record.prs = [
            tracking.PRRecord(
                repo=git_ops.slug_from_url(str(remote)),
                branch="feature/example", state="open", pr_revision=4,
                rewrite_identity=(
                    pr_publish._identity(str(remote), str(worktree))
                    if rewrite_identity else ""
                ),
            ),
            tracking.PRRecord(repo="unrelated/repository", pr_revision=8),
        ]
        for pr in record.prs:
            tracking.stamp_frozen_attribution(
                pr, attribution=config.default_repo.pr.source_attribution,
                explicit=config.default_repo.pr.source_attribution_configured,
            )
        tracking.save_record(record)
        original = tracking.load_record_by_id(wid)
        assert all(pr.pr_id for pr in original.prs)
    seed_files = _files(seed)
    with pytest.MonkeyPatch.context() as patch:
        config, wid, worktree, remote = _copy_pr_repo(seed, tmp_path / "relocated", patch)
        relocated = tracking.load_record_by_id(wid)
        assert relocated.worktree_path == str(worktree)
        assert relocated.prs[0].repo == git_ops.slug_from_url(str(remote))
        assert relocated.prs[0].pr_revision == original.prs[0].pr_revision + 1
        assert relocated.prs[0].pr_id == original.prs[0].pr_id
        assert relocated.prs[0].rewrite_identity == (
            pr_publish._identity(str(remote), str(worktree)) if rewrite_identity else ""
        )
        assert relocated.prs[1].repo == "unrelated/repository"
        assert relocated.prs[1].pr_revision == original.prs[1].pr_revision
        assert git_ops.git("remote", "get-url", "fork", cwd=worktree).stdout.strip() == str(
            remote.parent / "fork.git"
        )
    assert _files(seed) == seed_files
