"""Tests for agent_worktrees.pr_ops -- PR-workflow git operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops, tracking

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

class TestSlugify:
    def test_basic(self):
        assert pr_ops.slugify("Fix the auth bug") == "fix-the-auth-bug"

    def test_strips_special_chars(self):
        assert pr_ops.slugify("Fix: handle #42 & more!") == "fix-handle-42-more"

    def test_collapses_and_trims_dashes(self):
        assert pr_ops.slugify("  --Hello---World--  ") == "hello-world"

    def test_truncates(self):
        s = pr_ops.slugify("a" * 100, max_len=10)
        assert len(s) <= 10

    def test_empty_falls_back(self):
        assert pr_ops.slugify("!!!") == "change"


class TestFeatureBranchName:
    def test_uses_suffix_and_slug(self):
        name = pr_ops.feature_branch_name(
            "feature", "Fix auth", "lambda-core-win-20260618-173440-ac0d"
        )
        assert name == "feature/fix-auth-ac0d"

    def test_default_prefix(self):
        name = pr_ops.feature_branch_name("", "Title", "wt-abcd")
        assert name.startswith("feature/")
        assert name.endswith("-abcd")


# ---------------------------------------------------------------------------
# create_pr -- git-level integration
# ---------------------------------------------------------------------------

def _git(*args: str, cwd: Path) -> str:
    return git_ops.git(*args, cwd=str(cwd)).stdout.strip()


@pytest.fixture
def pr_repo(tmp_path: Path, monkeypatch):
    """A bare 'remote' + anchor + a worktree branch with two commits.

    Returns (config, worktree_id, worktree_path).  Patches tracking_dir so
    records land in a tmp directory.
    """
    remote_dir = tmp_path / "remote.git"
    anchor = tmp_path / "anchor"
    wt_root = tmp_path / "worktrees"
    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()

    # Bare remote
    git_ops.git("init", "--bare", "-b", "master", str(remote_dir))

    # Anchor repo
    git_ops.git("init", "-b", "master", str(anchor))
    _git("config", "user.email", "t@example.com", cwd=anchor)
    _git("config", "user.name", "Test", cwd=anchor)
    (anchor / "README.md").write_text("base\n")
    _git("add", "-A", cwd=anchor)
    _git("commit", "-m", "initial", cwd=anchor)
    _git("remote", "add", "origin", str(remote_dir), cwd=anchor)
    _git("push", "-u", "origin", "master", cwd=anchor)

    # Worktree on a worktree/<id> branch with two commits ahead of master
    worktree_id = "test-wt-20260618-aaaa"
    wt_path = wt_root / worktree_id
    wt_root.mkdir(parents=True, exist_ok=True)
    git_ops.git(
        "worktree", "add", str(wt_path), "-b", f"worktree/{worktree_id}",
        "origin/master", cwd=str(anchor),
    )
    _git("config", "user.email", "t@example.com", cwd=wt_path)
    _git("config", "user.name", "Test", cwd=wt_path)
    (wt_path / "a.txt").write_text("one\n")
    _git("add", "-A", cwd=wt_path)
    _git("commit", "-m", "work 1", cwd=wt_path)
    (wt_path / "b.txt").write_text("two\n")
    _git("add", "-A", cwd=wt_path)
    _git("commit", "-m", "work 2", cwd=wt_path)

    # Config pointing at the anchor with PR mode enabled
    config = cfg.Config(
        srcroot=str(tmp_path), machine="test", platform="linux",
        repo_name="ext",
        repos={"ext": cfg.RepoConfig(
            anchor=str(anchor), worktree_root=str(wt_root),
            default_branch="master", remote="origin",
            pr=cfg.PRConfig(enabled=True, provider="gitea", branch_prefix="feature"),
        )},
    )

    monkeypatch.setattr("agent_worktrees.config.tracking_dir", lambda: tracking_d)

    # Seed a tracking record
    tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(wt_path),
        "ext", "test", "linux", tracking_d,
    )

    return config, worktree_id, wt_path, remote_dir


class TestCreatePR:
    def test_disabled_errors(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        import dataclasses
        disabled = dataclasses.replace(
            config.repos["ext"], pr=cfg.PRConfig(enabled=False)
        )
        config2 = dataclasses.replace(config, repos={"ext": disabled})
        res = pr_ops.create_pr(wid, config2)
        assert res["success"] is False
        assert "not enabled" in res["error"]

    def test_creates_and_pushes_feature_branch(self, pr_repo):
        config, wid, wt_path, remote_dir = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature")

        assert res["success"] is True, res
        assert res["state"] == "open"
        assert res["branch"] == "feature/add-feature-aaaa"
        assert res["provider"] == "gitea"
        assert res["head_sha"]

        # HEAD is now on the feature branch
        head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path)
        assert head == "feature/add-feature-aaaa"

        # Feature branch is on the remote
        assert git_ops.remote_branch_exists(
            "origin", "feature/add-feature-aaaa", cwd=str(wt_path)
        )

        # worktree base branch was reset to upstream (clean base == master)
        wt_sha = _git("rev-parse", f"worktree/{wid}", cwd=wt_path)
        up_sha = _git("rev-parse", "origin/master", cwd=wt_path)
        assert wt_sha == up_sha

        # Feature branch is exactly one commit ahead of master (squashed)
        ahead = git_ops.get_commits_ahead(
            "feature/add-feature-aaaa", "origin/master", cwd=str(wt_path)
        )
        assert len(ahead) == 1

    def test_records_pr_state_in_tracking(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr is not None
        assert rec.pr.state == "open"
        assert rec.pr.branch == "feature/add-feature-aaaa"
        assert rec.pr.provider == "gitea"

    def test_idempotent_rerun(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        first = pr_ops.create_pr(wid, config, title="Add feature")
        assert first["success"]
        # Re-run -- now HEAD is on the feature branch; should re-push cleanly.
        second = pr_ops.create_pr(wid, config, title="Add feature")
        assert second["success"] is True
        assert second.get("rerun") is True
        assert second["branch"] == "feature/add-feature-aaaa"

    def test_dirty_worktree_blocks(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        (wt_path / "dirty.txt").write_text("uncommitted\n")
        res = pr_ops.create_pr(wid, config, title="x")
        assert res["success"] is False
        assert "uncommitted" in res["error"]

    def test_dry_run_no_side_effects(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.create_pr(wid, config, title="Add feature", dry_run=True)
        assert res["success"] is True
        assert res["dry_run"] is True
        # Still on the worktree branch -- nothing happened
        head = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path)
        assert head == f"worktree/{wid}"


# ---------------------------------------------------------------------------
# set_pr / pr_status
# ---------------------------------------------------------------------------

class TestSetPRAndStatus:
    def test_status_no_pr(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.pr_status(wid)
        assert res["has_pr"] is False

    def test_status_missing_record(self, pr_repo):
        res = pr_ops.pr_status("does-not-exist")
        assert res["has_pr"] is False
        assert "error" in res

    def test_set_pr_creates_block(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.set_pr(
            wid, url="https://example/pulls/7", number=7, provider="gitea"
        )
        assert res["success"] is True
        assert res["number"] == 7
        assert res["state"] == "open"  # defaulted
        # Persisted
        st = pr_ops.pr_status(wid)
        assert st["has_pr"] is True
        assert st["url"] == "https://example/pulls/7"
        assert st["number"] == 7

    def test_set_pr_merges_with_create_pr(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        created = pr_ops.create_pr(wid, config, title="Add feature")
        assert created["success"]
        res = pr_ops.set_pr(wid, url="https://example/pulls/9", number=9)
        assert res["success"] is True
        # create-pr's branch/head_sha preserved
        assert res["branch"] == "feature/add-feature-aaaa"
        assert res["head_sha"] == created["head_sha"]
        assert res["number"] == 9

    def test_set_pr_invalid_state(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        res = pr_ops.set_pr(wid, state="bogus")
        assert res["success"] is False
        assert "Invalid PR state" in res["error"]

    def test_set_pr_state_transition(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        pr_ops.set_pr(wid, url="u", number=1)
        res = pr_ops.set_pr(wid, state="merged")
        assert res["success"] is True
        assert res["state"] == "merged"
        assert res["number"] == 1  # preserved

    def test_set_pr_missing_record(self, pr_repo):
        res = pr_ops.set_pr("does-not-exist", number=1)
        assert res["success"] is False
        assert "No tracking record" in res["error"]


# ---------------------------------------------------------------------------
# PR-aware finalize + push-changes (#586)
# ---------------------------------------------------------------------------

class TestPRFinalizeAndPush:
    def test_precondition_fails_before_push(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        # Record a pr.branch that was never pushed.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr = tracking.PRRecord(state="creating", branch="feature/never-pushed-aaaa")
        tracking.save_record(rec)
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is False
        assert "not on" in err

    def test_precondition_ok_after_create_pr(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_precondition_detects_unpushed(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        # Add a local commit on the feature branch without pushing.
        (wt_path / "c.txt").write_text("more\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "feedback", cwd=wt_path)
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is False
        assert "unpushed" in err

    def test_push_changes_updates_feature_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")

        before = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)

        # New feedback commit on the feature branch
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)

        ok = fin.push_changes(wid, config)
        assert ok is True

        after = _git("rev-parse", "origin/feature/add-feature-aaaa", cwd=wt_path)
        assert after != before  # remote feature branch advanced

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        local_head = _git("rev-parse", "HEAD", cwd=wt_path)
        assert rec.pr.head_sha == local_head
        assert rec.pr.state == "open"

    def test_push_changes_rejects_wrong_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        # Switch back to the worktree base branch -- push-changes should refuse.
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        ok = fin.push_changes(wid, config)
        assert ok is False


