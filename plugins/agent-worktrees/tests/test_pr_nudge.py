"""Tests for :mod:`agent_worktrees.pr_nudge_ops` (the ``pr-nudge`` primitive)."""

from __future__ import annotations

from agent_worktrees import config as cfg, pr_nudge_ops, pr_ops


def _config_with_reviewer(base_config, reviewer: str):
    """Clone the fixture config with ``pr.reviewer`` set."""
    repo = base_config.default_repo
    pr = cfg.PRConfig(
        enabled=True, provider="github", branch_prefix="feature",
        head_scheme="snapshot", auto_open=False,
        api_base="https://api.github.com",
        reviewer=reviewer,
    )
    new_repo = cfg.RepoConfig(
        anchor=repo.anchor, worktree_root=repo.worktree_root,
        default_branch=repo.default_branch, remote=repo.remote, pr=pr,
    )
    return cfg.Config(
        srcroot=base_config.srcroot, machine=base_config.machine,
        platform=base_config.platform, repo_name=base_config.repo_name,
        repos={base_config.repo_name: new_repo},
    )


class TestPRNudge:
    def test_no_tracked_pr_reports_has_pr_false(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "copilot")
        res = pr_nudge_ops.pr_nudge("nonexistent-worktree-id", config=config)
        assert res["has_pr"] is False
        assert "error" in res

    def test_no_open_pr_reports_unsupported_false_positive_free(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "copilot")
        pr_ops.set_pr(wid, url="https://h/pulls/x", provider="github")  # no number
        res = pr_nudge_ops.pr_nudge(wid, config=config)
        assert res["has_pr"] is False
        assert res["supported"] is False

    def test_merged_pr_is_not_targeted(self, pr_repo, monkeypatch):
        """``active_pr()`` falls back to the most recent PR overall once every
        tracked PR is terminal -- a nudge must never target that fallback."""
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "copilot")
        pr_ops.set_pr(wid, number=7, state="merged", provider="github")

        def boom(name):
            raise AssertionError("provider must not be consulted for a terminal PR")

        monkeypatch.setattr(providers, "get_provider", boom)
        res = pr_nudge_ops.pr_nudge(wid, config=config)
        assert res["has_pr"] is False
        assert res["supported"] is False

    def test_externally_merged_pr_is_reconciled_before_nudging(self, pr_repo, monkeypatch):
        """A record still saying ``open`` (merged externally, e.g. via the
        auto-merge label bypassing finalize/pr-watch) must be reconciled
        against the provider first -- nudging it would otherwise send a
        review request to an already-merged PR (#4355 review finding)."""
        from agent_worktrees import providers
        from agent_worktrees.providers.base import PullResult

        config, wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "copilot")
        pr_ops.set_pr(wid, number=7, state="open", provider="github")
        nudge_calls = []

        class _Prov:
            name = "github"

            def get_pull(self, repo, number, *, api_base="", token=None):
                return PullResult(number=number, state="closed", merged=True)

            def request_review(self, repo, number, *, reviewer="", api_base="", token=None):
                nudge_calls.append(number)
                from agent_worktrees.pr_contract import ReviewNudgeResult
                return ReviewNudgeResult(supported=True, requested=True)

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: "tok")
        res = pr_nudge_ops.pr_nudge(wid, config=config)
        assert nudge_calls == []
        assert res["has_pr"] is False
        assert res["supported"] is False

    def test_unconfigured_reviewer_reports_unsupported(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "")
        pr_ops.set_pr(wid, number=7, state="open", provider="github")

        class _Prov:
            name = "github"

            def request_review(self, repo, number, *, reviewer="", api_base="", token=None):
                from agent_worktrees.pr_contract import ReviewNudgeResult
                return ReviewNudgeResult(supported=False, detail="nothing to nudge")

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: None)
        res = pr_nudge_ops.pr_nudge(wid, config=config)
        assert res["has_pr"] is True
        assert res["supported"] is False
        assert res["requested"] is False

    def test_configured_reviewer_requests_via_provider(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "copilot")
        pr_ops.set_pr(wid, number=7, state="open", provider="github")
        seen = {}

        class _Prov:
            name = "github"

            def request_review(self, repo, number, *, reviewer="", api_base="", token=None):
                from agent_worktrees.pr_contract import ReviewNudgeResult
                seen["repo"] = repo
                seen["number"] = number
                seen["reviewer"] = reviewer
                return ReviewNudgeResult(
                    supported=True, requested=True,
                    reviewer="copilot-pull-request-reviewer[bot]",
                    detail="requested",
                )

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: "tok")
        res = pr_nudge_ops.pr_nudge(wid, config=config)
        assert res["supported"] is True
        assert res["requested"] is True
        assert res["reviewer"] == "copilot-pull-request-reviewer[bot]"
        assert seen["number"] == 7
        assert seen["reviewer"] == "copilot"

    def test_provider_error_is_reported_not_raised(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        config = _config_with_reviewer(config, "copilot")
        pr_ops.set_pr(wid, number=7, state="open", provider="github")

        def _boom(name):
            raise RuntimeError("provider unreachable")

        monkeypatch.setattr(providers, "get_provider", _boom)
        res = pr_nudge_ops.pr_nudge(wid, config=config)
        assert res["supported"] is False
        assert res["requested"] is False
        assert "provider unreachable" in res["error"]
