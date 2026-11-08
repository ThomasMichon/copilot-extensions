"""Tests for :mod:`agent_worktrees.pr_reviewer_ops` (the ``pr-diff`` /
``pr-comment`` / ``pr-review`` primitives -- Phase 3 of the
``pull-request-capability`` effort)."""

from __future__ import annotations

from agent_worktrees import pr_ops, pr_reviewer_ops


class TestPRDiff:
    def test_no_tracked_pr_reports_has_pr_false(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        res = pr_reviewer_ops.pr_diff("nonexistent-worktree-id", config=config)
        assert res["has_pr"] is False
        assert "error" in res

    def test_no_open_pr_reports_has_pr_false(self, pr_repo):
        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, url="https://h/pulls/x", provider="gitea")  # no number
        res = pr_reviewer_ops.pr_diff(wid, config=config)
        assert res["has_pr"] is False

    def test_reads_diff_from_provider(self, pr_repo, monkeypatch):
        from agent_worktrees import providers
        from agent_worktrees.pr_contract import PRDiff

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")

        class _Prov:
            name = "gitea"

            def get_diff(self, repo, number, *, api_base="", token=None):
                return PRDiff(diff="diff --git a/x b/x\n")

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: "tok")
        res = pr_reviewer_ops.pr_diff(wid, config=config)
        assert res["supported"] is True
        assert res["diff"] == "diff --git a/x b/x\n"
        assert res["number"] == 7

    def test_provider_error_is_reported_not_raised(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")

        def _boom(name):
            raise RuntimeError("provider unreachable")

        monkeypatch.setattr(providers, "get_provider", _boom)
        res = pr_reviewer_ops.pr_diff(wid, config=config)
        assert res["supported"] is False
        assert "provider unreachable" in res["error"]


class TestPRComment:
    def test_no_tracked_pr_reports_has_pr_false(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        res = pr_reviewer_ops.pr_comment(
            "nonexistent-worktree-id", "hello", config=config
        )
        assert res["has_pr"] is False

    def test_posts_comment_via_provider(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        seen = {}

        class _Prov:
            name = "gitea"

            def post_comment(self, repo, number, body, *, api_base="", token=None):
                seen["repo"] = repo
                seen["number"] = number
                seen["body"] = body
                return ""

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: "tok")
        res = pr_reviewer_ops.pr_comment(wid, "nice work", config=config)
        assert "error" not in res
        assert seen["number"] == 7
        assert seen["body"] == "nice work"

    def test_provider_failure_is_reported(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")

        class _Prov:
            name = "gitea"

            def post_comment(self, repo, number, body, *, api_base="", token=None):
                return "gh pr comment failed"

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: "tok")
        res = pr_reviewer_ops.pr_comment(wid, "nice work", config=config)
        assert res["error"] == "gh pr comment failed"


class TestPRReview:
    def test_no_tracked_pr_reports_has_pr_false(self, pr_repo):
        config, _wid, _wt, _ = pr_repo
        res = pr_reviewer_ops.pr_review(
            "nonexistent-worktree-id", event="APPROVED", config=config
        )
        assert res["has_pr"] is False

    def test_submits_review_via_provider(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo
        pr_ops.set_pr(wid, number=7, state="open", provider="gitea")
        seen = {}

        class _Prov:
            name = "gitea"

            def submit_review(self, repo, number, *, event, body="", api_base="", token=None):
                seen["event"] = event
                seen["body"] = body
                return ""

        monkeypatch.setattr(providers, "get_provider", lambda name: _Prov())
        monkeypatch.setattr(providers, "account_token_for_slug", lambda repo, prcfg: "tok")
        res = pr_reviewer_ops.pr_review(
            wid, event="APPROVED", body="LGTM", config=config
        )
        assert "error" not in res
        assert res["event"] == "APPROVED"
        assert seen["event"] == "APPROVED"
        assert seen["body"] == "LGTM"

    def test_provider_mismatch_refuses_before_touching_provider(self, pr_repo, monkeypatch):
        from agent_worktrees import providers

        config, wid, _wt, _ = pr_repo  # default_repo.pr.provider == "gitea"
        pr_ops.set_pr(wid, number=7, state="open", provider="github")

        def boom(name):
            raise AssertionError("provider must not be consulted on a mismatch")

        monkeypatch.setattr(providers, "get_provider", boom)
        res = pr_reviewer_ops.pr_review(wid, event="APPROVED", config=config)
        assert res["has_pr"] is True
        assert "differs from configured provider" in res["error"]


def test_selected_consumer_is_optional_and_never_resolves_legacy_credentials(pr_repo, monkeypatch):
    from agent_worktrees import providers
    from agent_worktrees.pr_selected_publication import PROTOCOL

    config, wid, _, _ = pr_repo
    pr_ops.set_pr(wid, number=7, state="open", provider="gitea")

    class Selected:
        protocol = PROTOCOL
        phase = "preview"

        def execute(
            self, *, worktree_id: str, target: dict | None, command: str, body: str, event: str,
        ) -> dict:
            assert worktree_id == wid and target["provider"] == "gitea" and target["number"] == 7
            assert command == "pr-comment" and body == "selected text" and event == ""
            return {"publication": {"phase": "preview", "effects": "none"}}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("selected consumer entered legacy provider/credential/reconcile")

    monkeypatch.setattr(pr_ops, "_reconcile_active_pr", forbidden)
    monkeypatch.setattr(providers, "get_provider", forbidden)
    monkeypatch.setattr(providers, "account_token_for_slug", forbidden)
    result = pr_reviewer_ops.pr_comment(wid, "selected text", config=config, publication=Selected())
    assert result["publication"] == {"phase": "preview", "effects": "none"}


def test_original_selected_recovery_needs_neither_current_tracking_nor_config(monkeypatch):
    from agent_worktrees import config as cfg
    from agent_worktrees.pr_selected_publication import PROTOCOL

    class Recovery:
        protocol = PROTOCOL
        phase = "recover"

        def execute(
            self, *, worktree_id: str, target: dict | None, command: str, body: str, event: str,
        ) -> dict:
            assert target is None and worktree_id == "original" and command == "pr-review"
            return {"publication": {"phase": "recover", "original": True}}

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("recovery depends on current tracking or config")

    monkeypatch.setattr(cfg, "load_config", forbidden)
    monkeypatch.setattr(pr_ops, "_load_record_or_none", forbidden)
    result = pr_reviewer_ops.pr_review("original", event="APPROVED", publication=Recovery())
    assert result["publication"] == {"phase": "recover", "original": True}
