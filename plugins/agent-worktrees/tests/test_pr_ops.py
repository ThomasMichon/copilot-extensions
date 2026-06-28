"""Tests for agent_worktrees.pr_ops -- PR-workflow git operations."""

from __future__ import annotations

import types
from pathlib import Path

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


class TestCreatePR:
    def test_disabled_errors(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
        import dataclasses
        disabled = dataclasses.replace(
            config.repos["ext"], pr=cfg.PRConfig(enabled=False)
        )
        config2 = dataclasses.replace(config, repos={"ext": disabled})
        res = pr_ops.create_pr(wid, config2)
        assert res["success"] is False
        assert "not enabled" in res["error"]

    def test_creates_and_pushes_feature_branch(self, pr_repo):
        config, wid, wt_path, _remote_dir = pr_repo
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
        config, wid, _wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.pr is not None
        assert rec.pr.state == "open"
        assert rec.pr.branch == "feature/add-feature-aaaa"
        assert rec.pr.provider == "gitea"

    def test_idempotent_rerun(self, pr_repo):
        config, wid, _wt_path, _ = pr_repo
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
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.pr_status(wid)
        assert res["has_pr"] is False

    def test_status_missing_record(self, pr_repo):
        res = pr_ops.pr_status("does-not-exist")
        assert res["has_pr"] is False
        assert "error" in res

    def test_set_pr_creates_block(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
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
        config, wid, _wt_path, _ = pr_repo
        created = pr_ops.create_pr(wid, config, title="Add feature")
        assert created["success"]
        res = pr_ops.set_pr(wid, url="https://example/pulls/9", number=9)
        assert res["success"] is True
        # create-pr's branch/head_sha preserved
        assert res["branch"] == "feature/add-feature-aaaa"
        assert res["head_sha"] == created["head_sha"]
        assert res["number"] == 9

    def test_set_pr_invalid_state(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
        res = pr_ops.set_pr(wid, state="bogus")
        assert res["success"] is False
        assert "Invalid PR state" in res["error"]

    def test_set_pr_state_transition(self, pr_repo):
        _config, wid, _wt_path, _ = pr_repo
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
        config, wid, wt_path, _remote_dir = pr_repo
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

    # --- #1045: finalize must not false-block once the PR is merged -----------

    def _simulate_squash_merge(self, config, wid, feature):
        """Squash-merge *feature* into origin/master (mimics a Gitea merge).

        Leaves ``origin/<feature>`` at its stale pre-merge head -- the exact
        condition that tripped the old precondition (#1045).
        """
        anchor = config.default_repo.anchor
        _git("fetch", "origin", cwd=anchor)
        _git("checkout", "master", cwd=anchor)
        _git("merge", "--squash", f"origin/{feature}", cwd=anchor)
        _git("commit", "-m", f"Squash merge {feature}", cwd=anchor)
        _git("push", "origin", "master", cwd=anchor)

    def test_precondition_ok_after_merge(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        # origin/<feature> is stale (pre-merge); the OLD check would false-block.
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        rec.pr.state = "merged"
        tracking.save_record(rec)
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err
        assert err is None

    def test_precondition_ok_after_merge_remote_branch_deleted(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        # Provider deleted the remote feature branch on merge.
        _git("push", "origin", "--delete", feature, cwd=config.default_repo.anchor)
        _git("fetch", "origin", "--prune", cwd=str(wt_path))
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        repo = config.default_repo
        ok, err = fin._pr_finalize_precondition(rec, repo, str(wt_path), repo.anchor)
        assert ok is True, err

    # --- #1106: reconcile merged branch pointers so the picker isn't diverged -

    def test_reconcile_aligns_worktree_base_after_merge(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        _git("fetch", "origin", cwd=str(wt_path))
        repo = config.default_repo
        wt_branch = f"worktree/{wid}"

        # HEAD is on the feature branch (drift); worktree/<id> is a free pointer.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == feature

        fin._reconcile_merged_pointers(repo, str(wt_path), repo.anchor, wt_branch)

        # worktree/<id> now aligns with origin/master -> 0 ahead in the picker.
        wt_sha = _git("rev-parse", wt_branch, cwd=wt_path)
        up_sha = _git("rev-parse", "origin/master", cwd=wt_path)
        assert wt_sha == up_sha
        # The live feature checkout is untouched.
        assert _git("rev-parse", "--abbrev-ref", "HEAD", cwd=wt_path) == feature

    def test_reconcile_fast_forwards_anchor_default_branch(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        feature = "feature/add-feature-aaaa"
        self._simulate_squash_merge(config, wid, feature)
        repo = config.default_repo
        anchor = repo.anchor
        # Rewind the anchor's local master behind origin to prove the FF.
        _git("reset", "--hard", "HEAD~1", cwd=anchor)
        assert _git("rev-parse", "master", cwd=anchor) != \
            _git("rev-parse", "origin/master", cwd=anchor)

        fin._reconcile_merged_pointers(repo, str(wt_path), anchor, f"worktree/{wid}")

        assert _git("rev-parse", "master", cwd=anchor) == \
            _git("rev-parse", "origin/master", cwd=anchor)


class TestPRRequiredEnforcement:
    """``pr.required`` blocks the direct-to-master path entirely."""

    def _required_config(self, config):
        repo = config.default_repo
        return cfg.Config(
            srcroot=config.srcroot, machine=config.machine,
            platform=config.platform, repo_name=config.repo_name,
            repos={config.repo_name: cfg.RepoConfig(
                anchor=repo.anchor, worktree_root=repo.worktree_root,
                default_branch=repo.default_branch, remote=repo.remote,
                pr=cfg.PRConfig(
                    enabled=True, required=True,
                    provider="gitea", branch_prefix="feature",
                ),
            )},
        )

    def test_push_changes_refuses_direct_to_master(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, remote_dir = pr_repo
        req_config = self._required_config(config)

        before = _git("ls-remote", str(remote_dir), "master", cwd=wt_path)
        # No create-pr was run -> no PR record -> direct push must be refused.
        ok = fin.push_changes(wid, req_config)
        assert ok is False
        after = _git("ls-remote", str(remote_dir), "master", cwd=wt_path)
        assert after == before  # remote master untouched

    def test_finalize_refuses_unmerged_direct(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, _wt_path, _ = pr_repo
        req_config = self._required_config(config)
        # Unmerged work, no PR -> finalize must refuse (not prune).
        ok = fin.validate_and_finalize(wid, req_config)
        assert ok is False

    def test_create_pr_path_still_works_when_required(self, pr_repo):
        from agent_worktrees import finalize as fin
        config, wid, wt_path, _ = pr_repo
        req_config = self._required_config(config)
        # The PR path remains available: create-pr then push-changes updates
        # the feature branch, never master.
        pr_ops.create_pr(wid, req_config, title="Add feature")
        (wt_path / "c.txt").write_text("feedback\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "address feedback", cwd=wt_path)
        ok = fin.push_changes(wid, req_config)
        assert ok is True




# ---------------------------------------------------------------------------
# Multi-PR worktree tracking (#1107)
# ---------------------------------------------------------------------------

class TestMultiPR:
    def test_serial_re_pr_after_merge_opens_fresh_pr(self, pr_repo):
        """The #1088->#1104 regression: a merged PR must NOT be reused."""
        config, wid, wt_path, _ = pr_repo
        r1 = pr_ops.create_pr(wid, config, title="Add feature")
        assert r1["success"], r1
        assert r1["branch"] == "feature/add-feature-aaaa"
        pr_ops.set_pr(wid, number=1, state="merged")

        # Back to the base branch; do new work for a second PR.
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "d.txt").write_text("second\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "second work", cwd=wt_path)

        r2 = pr_ops.create_pr(wid, config, title="Second feature")
        assert r2["success"], r2
        assert "rerun" not in r2  # NOT the reuse path
        assert r2["branch"] == "feature/second-feature-aaaa"

        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert rec.prs[0].state == "merged"
        assert rec.prs[0].branch == "feature/add-feature-aaaa"
        assert rec.prs[1].state == "open"
        assert rec.prs[1].branch == "feature/second-feature-aaaa"
        assert rec.active_pr().branch == "feature/second-feature-aaaa"
        # Fresh base_sha = current origin/master, not the first PR's stale base.
        assert rec.prs[1].base_sha == _git("rev-parse", "origin/master", cwd=wt_path)

    def test_new_flag_forces_parallel_pr_while_open(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")  # PR #1 open
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "e.txt").write_text("parallel\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "parallel work", cwd=wt_path)

        r = pr_ops.create_pr(wid, config, title="Parallel feature", new=True)
        assert r["success"], r
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert len(rec.prs) == 2
        assert {p.state for p in rec.prs} == {"open"}
        assert rec.prs[1].branch == "feature/parallel-feature-aaaa"

    def test_create_pr_records_target_repo(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        r = pr_ops.create_pr(wid, config, title="Add feature", target_repo="owner/other")
        assert r["success"], r
        assert r["repo"] == "owner/other"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.prs[0].repo == "owner/other"

    def test_create_pr_defaults_repo_to_remote_slug(self, pr_repo):
        # Default target repo = the remote's owner/name slug (what the provider
        # API needs), not the local project name.
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        expected = git_ops.remote_slug("origin", cwd=str(wt_path))
        assert expected  # the bare-remote path yields a two-part slug
        assert rec.prs[0].repo == expected

    def test_set_pr_selects_by_number_and_stamps_closed_at(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        pr_ops.set_pr(wid, number=42, state="open")
        res = pr_ops.set_pr(wid, select_number=42, state="merged")
        assert res["success"], res
        assert res["state"] == "merged"
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.prs[0].closed_at  # terminal -> stamped

    def test_set_pr_unknown_selector_errors(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        res = pr_ops.set_pr(wid, select_number=999, state="merged")
        assert res["success"] is False
        assert "999" in res["error"]

    def test_pr_status_all_lists_history(self, pr_repo):
        config, wid, wt_path, _ = pr_repo
        pr_ops.create_pr(wid, config, title="Add feature")
        pr_ops.set_pr(wid, number=1, state="merged")
        _git("checkout", f"worktree/{wid}", cwd=wt_path)
        (wt_path / "f.txt").write_text("again\n")
        _git("add", "-A", cwd=wt_path)
        _git("commit", "-m", "more", cwd=wt_path)
        pr_ops.create_pr(wid, config, title="Another feature")

        res = pr_ops.pr_status(wid, all_prs=True)
        assert res["pr_count"] == 2
        assert len(res["prs"]) == 2
        # active = the open one
        assert res["state"] == "open"
        assert res["branch"] == "feature/another-feature-aaaa"


# ---------------------------------------------------------------------------
# _worktree_to_dict PR exposure (#1107)
# ---------------------------------------------------------------------------

class TestWorktreeToDictPRs:
    def _rec(self, prs):
        return tracking.WorktreeRecord(
            worktree_id="wt-001", branch="worktree/wt-001",
            worktree_path="/tmp/wt", repo="ext", machine="m", platform="wsl",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            handoff_prompt=None, sessions=None, prs=prs,
        )

    def test_no_prs_omits_pr_keys(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        d = _worktree_to_dict(self._rec([]))
        assert "pr" not in d and "prs" not in d and "pr_count" not in d

    def test_prs_exposed_with_active_and_count(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.tracking import PRRecord
        rec = self._rec([
            PRRecord(state="merged", branch="a", number=1),
            PRRecord(state="open", branch="b", number=2),
        ])
        d = _worktree_to_dict(rec)
        assert d["pr_count"] == 2
        assert d["pr"]["number"] == 2  # active = the open one
        assert [p["number"] for p in d["prs"]] == [1, 2]


# ---------------------------------------------------------------------------
# _worktree_to_dict state exposure (list --json --classify, aperture-labs #1290)
# ---------------------------------------------------------------------------

class TestWorktreeToDictState:
    def _rec(self):
        return tracking.WorktreeRecord(
            worktree_id="wt-002", branch="worktree/wt-002",
            worktree_path="/tmp/wt2", repo="ext", machine="m", platform="wsl",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            handoff_prompt=None, sessions=None, prs=[],
        )

    def test_no_state_info_omits_state_keys(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        d = _worktree_to_dict(self._rec())
        for k in ("state", "ahead", "behind", "dirty"):
            assert k not in d

    def test_state_info_exposes_canonical_state(self):
        from agent_worktrees.__main__ import _worktree_to_dict
        from agent_worktrees.git_ops import WorktreeState, WorktreeStateInfo
        info = WorktreeStateInfo(
            state=WorktreeState.WIP, ahead=3, behind=5, dirty=0,
        )
        d = _worktree_to_dict(self._rec(), state_info=info)
        assert d["state"] == "wip"   # the canonical enum value the picker maps
        assert d["ahead"] == 3
        assert d["behind"] == 5
        assert d["dirty"] == 0


# ---------------------------------------------------------------------------
# _classify_records shares the status bar's CONVO refinement (aperture-labs #1290)
# ---------------------------------------------------------------------------

class TestClassifyRecordsConvo:
    """list --json --classify must report the same CONVO state the tmux status
    bar shows: a clean, commit-less worktree whose session held turns."""

    def _wire(self, monkeypatch, *, raw_state):
        from agent_worktrees import __main__ as m
        from agent_worktrees import git_ops
        monkeypatch.setattr(
            m.cfg, "load_config",
            lambda: types.SimpleNamespace(
                default_repo=types.SimpleNamespace(
                    remote="origin", default_branch="master",
                ),
            ),
        )
        monkeypatch.setattr(m, "_build_active_paths", lambda *a, **k: set())
        monkeypatch.setattr(
            m.git_ops, "classify_worktree",
            lambda *a, **k: git_ops.WorktreeStateInfo(state=raw_state),
        )
        monkeypatch.setattr(m, "_apply_tracking_override", lambda r, i: i)
        return m

    def _rec(self, path):
        return tracking.WorktreeRecord(
            worktree_id="wt-003", branch="worktree/wt-003",
            worktree_path=str(path), repo="ext", machine="m", platform="wsl",
            started_at="2026-06-01T10:00:00", last_resumed_at="2026-06-01T10:00:00",
            resume_count=0, title=None, status="active", completed_at=None,
            handoff_prompt=None, sessions=None, prs=[],
        )

    def test_unused_with_turns_classifies_convo(self, monkeypatch, tmp_path):
        from agent_worktrees import sessions
        m = self._wire(monkeypatch, raw_state=git_ops.WorktreeState.UNUSED)
        rec = self._rec(tmp_path)
        ctx = sessions.SessionContext()
        ctx.turn_count[m._normalize_path(str(tmp_path))] = 5
        out = m._classify_records([rec], ctx)
        assert out["wt-003"].state == git_ops.WorktreeState.CONVO

    def test_unused_without_turns_stays_unused(self, monkeypatch, tmp_path):
        from agent_worktrees import sessions
        m = self._wire(monkeypatch, raw_state=git_ops.WorktreeState.UNUSED)
        rec = self._rec(tmp_path)
        out = m._classify_records([rec], sessions.SessionContext())
        assert out["wt-003"].state == git_ops.WorktreeState.UNUSED

    def test_non_unused_unaffected_by_turns(self, monkeypatch, tmp_path):
        from agent_worktrees import sessions
        m = self._wire(monkeypatch, raw_state=git_ops.WorktreeState.WIP)
        rec = self._rec(tmp_path)
        ctx = sessions.SessionContext()
        ctx.turn_count[m._normalize_path(str(tmp_path))] = 9
        out = m._classify_records([rec], ctx)
        assert out["wt-003"].state == git_ops.WorktreeState.WIP
