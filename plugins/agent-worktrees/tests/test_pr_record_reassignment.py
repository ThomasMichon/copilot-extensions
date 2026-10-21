"""Persisted PR record reassignment and stale-writer contracts."""

from __future__ import annotations

from pathlib import Path

import pytest
from pr_workflow_setup import PRWorkflowSetup

from agent_worktrees import config as cfg
from agent_worktrees import pr_ops, tracking


@pytest.fixture
def recorded_pr(record_repo):
    """Synthetic legacy fork record input, not evidence of a publication."""
    config, wid, worktree, remote = record_repo
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    record.prs = [tracking.PRRecord(
        state="open", branch="feature/recorded", repo="acme/ext",
        provider="gitea", remote="fork", head_repo="alice/ext",
        head_owner="alice",
        head_sha="published-head", base_sha="base", patch_id="patch",
    )]
    tracking.stamp_frozen_attribution(
        record.pr, attribution=config.default_repo.pr.source_attribution,
        explicit=config.default_repo.pr.source_attribution_configured,
    )
    tracking.save_record(record)
    return config, wid, worktree, remote


def test_recorded_pr_preserves_explicit_inputs_without_git(request, monkeypatch):
    from agent_worktrees import git_ops

    def unexpected_git(*args, **kwargs):
        raise AssertionError("recorded PR fixture must not invoke Git")

    monkeypatch.setattr(git_ops, "git", unexpected_git)
    config, wid, worktree, remote = request.getfixturevalue("recorded_pr")
    record = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
    pr = record.active_pr()

    assert len(record.prs) == 1
    assert (pr.state, pr.branch, pr.repo, pr.provider) == (
        "open", "feature/recorded", "acme/ext", "gitea",
    )
    assert (pr.remote, pr.head_repo, pr.head_identity, pr.head_owner) == (
        "fork", "alice/ext", "", "alice",
    )
    assert (pr.head_sha, pr.base_sha, pr.patch_id) == ("published-head", "base", "patch")
    assert pr.pr_id and pr.pr_revision == 1
    assert pr.attribution_mode == "codename" and pr.attribution_explicit is False
    assert cfg.load_config() is config
    assert not (worktree / ".git").exists() and not remote.exists()


class TestPRRecordReassignment(PRWorkflowSetup):
    def test_a_stale_writer_never_erases_the_recorded_fork(self, recorded_pr):
        """A process that loaded the record before create-pr recorded the PR's
        fork saves an unrelated change later: the fork stays recorded."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        stale = tracking.load_record(path)
        stale.pr.remote = stale.pr.head_repo = ""
        fresh = tracking.load_record(path)
        fresh.pr.remote, fresh.pr.head_repo = "fork", "alice/ext"
        tracking.save_record(fresh)
        stale.title = "an unrelated update"
        tracking.save_record(stale)
        rec = tracking.load_record(path)
        assert (rec.pr.remote, rec.pr.head_repo, rec.title) == ("fork", "alice/ext", "an unrelated update")

    def test_a_stale_writer_never_erases_a_backfilled_fork_identity(self, recorded_pr):
        """A legacy record (fork recorded, no host identity yet) loaded by one process;
        another push backfills the identity; the first saves an unrelated change: the
        identity stays."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        legacy = tracking.load_record(path)
        legacy.pr.remote, legacy.pr.head_repo, legacy.pr.head_identity = "fork", "alice/ext", ""
        tracking.save_record(legacy)
        stale = tracking.load_record(path)
        assert stale.pr.head_identity == ""
        fresh = tracking.load_record(path)
        assert fresh.pr.head_identity == ""
        fresh.pr.head_identity = "github.com/alice/ext"
        tracking.save_record(fresh)
        stale.title = "an unrelated update"
        tracking.save_record(stale)
        rec = tracking.load_record(path)
        assert (rec.pr.remote, rec.pr.head_repo, rec.pr.head_identity) == ("fork", "alice/ext", "github.com/alice/ext")

    def test_set_pr_reassigning_a_record_clears_its_old_fork_target(self, recorded_pr):
        """Correcting a tracked entry to a different PR drops the old PR's fork target;
        attaching the first number keeps it."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner = None, "fork", "alice/ext", "alice"
        original_tip = (rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id)
        tracking.save_record(rec)
        pr_ops.set_pr(wid, number=7, config=config)  # first number: still that PR
        rec = tracking.load_record(path)
        assert (rec.pr.number, rec.pr.remote, rec.pr.head_owner) == (7, "fork", "alice")
        assert (rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id) == original_tip
        pr_ops.set_pr(wid, number=7, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id) == original_tip
        # A different PR: set_pr saves over the on-disk record that still names the old fork;
        # the save's field merge must not put it back.
        pr_ops.set_pr(wid, number=99, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.number, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == (99, "", "", "")

    @pytest.mark.parametrize("identity", ["number", "repo", "provider"])
    @pytest.mark.parametrize("competing_revision_increment", [0, 1])
    def test_set_pr_reassignment_discards_previous_tip_and_lifecycle(
        self, recorded_pr: tuple[cfg.Config, str, Path, Path], identity: str,
        competing_revision_increment: int,
    ) -> None:
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.repo, rec.pr.provider = 7, "acme/ext", "github"
        rec.pr.base_sha, rec.pr.head_sha, rec.pr.patch_id = "old-base", "old-head", "old-patch"
        rec.pr.state, rec.pr.opened_at, rec.pr.closed_at = "merged", "old-open", "old-close"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        stale = tracking.load_record(path)
        stale.pr.pr_revision += competing_revision_increment
        kwargs = {
            "number": {"number": 99},
            "repo": {"url": "https://github.com/other-org/other-repo/pull/7", "number": 7},
            "provider": {"provider": "ado"},
        }[identity]
        assert pr_ops.set_pr(wid, config=config, **kwargs)["success"]
        updated = tracking.load_record(path).pr
        assert (updated.base_sha, updated.head_sha, updated.patch_id) == ("", "", "")
        assert updated.state == "open"
        assert updated.opened_at and updated.opened_at != "old-open"
        assert updated.closed_at == ""
        stale.title = "unrelated stale update"
        tracking.save_record(stale)
        updated = tracking.load_record(path).pr
        assert (updated.base_sha, updated.head_sha, updated.patch_id) == ("", "", "")
        assert updated.state == "open"
        assert updated.opened_at and updated.opened_at != "old-open"
        assert updated.closed_at == ""

    def test_set_pr_reassigning_a_numberless_record_to_another_repo_clears_its_fork(self, recorded_pr):
        """A numberless record moved to a PR in another repository: the on-disk copy still
        has no number, but it's a different PR -- the save must not merge its fork back."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.repo = None, "acme/ext"
        rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner = "fork", "alice/ext", "alice"
        tracking.save_record(rec)
        pr_ops.set_pr(wid, url="https://github.com/other-org/other-repo/pull/7", number=7, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.repo, rec.pr.number) == ("other-org/other-repo", 7)
        assert (rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == ("", "", "")

    def test_set_pr_correcting_an_established_provider_clears_its_fork(self, recorded_pr):
        """Another provider is another PR: its old fork target is dropped (and the
        save's field merge doesn't restore it), while attaching a first provider
        keeps the PR's fork."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.provider, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner = "", "fork", "alice/ext", "alice"
        tracking.save_record(rec)
        pr_ops.set_pr(wid, provider="github", config=config)  # first provider: still that PR
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.remote, rec.pr.head_owner) == ("github", "fork", "alice")
        pr_ops.set_pr(wid, provider="ado", config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == ("ado", "", "", "")

    def test_a_stale_writer_of_another_provider_never_restores_its_fork(self, recorded_pr):
        """The save's field merge only fills fork fields from the on-disk copy while both
        describe the same PR: one under another established provider doesn't."""
        _config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.provider, rec.pr.head_repo = "github", "alice/ext"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        moved = tracking.load_record(path)
        moved.pr.provider = "ado"
        moved.pr.remote = moved.pr.head_repo = moved.pr.head_identity = moved.pr.head_owner = ""
        moved.pr.pr_revision += 1
        tracking.save_record(moved)
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.remote, rec.pr.head_repo) == ("ado", "", "")

    @pytest.mark.parametrize("change", [{"provider": "ado"}, {"number": 99}])
    def test_a_stale_writer_saving_after_a_reassignment_never_restores_the_old_pr(self, recorded_pr, change):
        """A process that loaded the record before set_pr reassigned it saves an unrelated
        change afterwards: the reassignment (a newer revision) wins, fork target included."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.provider, rec.pr.number, rec.pr.head_repo = "github", 7, "alice/ext"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        stale = tracking.load_record(path)
        pr_ops.set_pr(wid, config=config, **change)
        stale.title = "an unrelated update"
        tracking.save_record(stale)
        rec = tracking.load_record(path)
        assert (rec.pr.provider, rec.pr.number) == (change.get("provider", "github"), change.get("number", 7))
        assert (rec.pr.remote, rec.pr.head_repo, rec.title) == ("", "", "an unrelated update")

    def test_set_pr_correcting_only_the_repo_slug_case_keeps_the_fork(self, recorded_pr):
        """GitHub slugs are case-insensitive: re-entering the same PR's URL in another case
        is the same PR, so its fork target and its head observation stay."""
        config, wid, _wt_path, _remote = recorded_pr
        path = cfg.tracking_dir() / f"{wid}.yaml"
        rec = tracking.load_record(path)
        rec.pr.number, rec.pr.repo, rec.pr.head_repo, rec.pr.head_owner = 7, "acme/ext", "alice/ext", "alice"
        rec.pr.head_observed_at = "2026-10-06T00:00:00Z"
        rec.pr.pr_revision += 1
        tracking.save_record(rec)
        pr_ops.set_pr(wid, url="https://github.com/ACME/Ext/pull/7", number=7, config=config)
        rec = tracking.load_record(path)
        assert (rec.pr.remote, rec.pr.head_repo, rec.pr.head_owner) == ("fork", "alice/ext", "alice")
        assert rec.pr.head_observed_at == "2026-10-06T00:00:00Z"
