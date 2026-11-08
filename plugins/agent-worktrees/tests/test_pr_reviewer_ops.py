"""Tests for :mod:`agent_worktrees.pr_reviewer_ops` (the ``pr-diff`` /
``pr-comment`` / ``pr-review`` primitives -- Phase 3 of the
``pull-request-capability`` effort)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("phase", ["preview", "submit"])
def test_selected_main_resolves_real_project_config_and_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit: bool, phase: str,
) -> None:
    import yaml
    from conftest import _pr_git
    from agent_worktrees import __main__ as cli, config as cfg, installer, output, repos, tracking
    from agent_worktrees.pr_selected_publication import PROTOCOL

    home, anchor = tmp_path / "home", tmp_path / "anchor"
    home.mkdir()
    anchor.mkdir()
    _pr_git("init", "--initial-branch=main", cwd=anchor)
    monkeypatch.setattr(cfg, "_home", lambda: home)
    monkeypatch.setattr(repos, "_repos_yaml_path", lambda: tmp_path / "repos.yaml")
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: tmp_path / "projects.yaml")
    repos.write_registry(repos.ReposRegistry(repos={
        "selected-project": repos.RepoEntry(
            name="selected-project", repo_class="worktree", agent=True,
            paths={platform: str(anchor) for platform in ("windows", "wsl", "linux")},
        ),
    }))
    installer.write_projects_registry({"schema_version": 2, "projects": {"selected-project": {}}})
    local = cfg.project_dir("selected-project")
    local.mkdir(parents=True)
    (local / "config.yaml").write_text(yaml.safe_dump({
        "srcroot": str(tmp_path), "machine": "example", "platform": cfg.detect_platform(),
        "repos": {"selected-project": {
            "anchor": str(anchor), "worktree_root": str(tmp_path / "worktrees"),
            "pr": {"enabled": True, "provider": "gitea", "api_base": "https://example.com"},
        }},
    }), encoding="utf-8")
    tracking.create_new_record(
        "selected-worktree", "worktree/example", str(anchor), "selected-project",
        "example", "linux", cfg.tracking_dir("selected-project"),
    )
    path = cfg.tracking_dir("selected-project") / "selected-worktree.yaml"
    record = tracking.load_record(path)
    record.prs.append(tracking.PRRecord(state="open", number=7, provider="gitea", repo="owner/repo"))
    tracking.save_record(record, path)
    monkeypatch.chdir(tmp_path if explicit else anchor)
    cfg.set_active_project(None)
    seen = []

    class Selected:
        protocol = PROTOCOL

        def __init__(self) -> None:
            self.phase = phase

        def execute(
            self, *, worktree_id: str, target: dict | None, command: str, body: str, event: str,
        ) -> dict:
            seen.append(target)
            assert cfg.active_project() == "selected-project"
            assert worktree_id == "selected-worktree" and command == "pr-comment"
            return {"publication": {"phase": phase}}

    argv = ["pr-comment", "selected-worktree", "selected text", "--json"]
    if explicit:
        argv = ["--project", "selected-project", *argv]
    with output.capture_json_output() as stream:
        code = cli.main(argv, publication=Selected())
    assert code == 0, stream.getvalue()
    assert json.loads(stream.getvalue())["publication"]["phase"] == phase
    assert seen == [{"provider": "gitea", "api_base": "https://example.com", "repo": "owner/repo", "number": 7}]
