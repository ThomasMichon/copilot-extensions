"""Tests for the PR provider plugins (agent_worktrees.providers)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import pr_ops, tracking
from agent_worktrees.providers import (
    ProviderError,
    PRScope,
    PullResult,
    attribution,
    base,
)

# ---------------------------------------------------------------------------
# base: credential resolution + registry + scope builder
# ---------------------------------------------------------------------------

class TestResolveToken:
    def test_token_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "secret123")
        prcfg = cfg.PRConfig(token_env="MY_TOKEN")
        assert base.resolve_token(prcfg) == "secret123"

    def test_token_command_precedence(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "from-env")
        prcfg = cfg.PRConfig(token_env="MY_TOKEN", token_command="printf cmd-tok")
        assert base.resolve_token(prcfg) == "cmd-tok"

    def test_none_when_unset(self):
        assert base.resolve_token(cfg.PRConfig()) is None

    def test_command_failure_falls_back_to_env(self, monkeypatch):
        monkeypatch.setenv("MY_TOKEN", "env-tok")
        prcfg = cfg.PRConfig(token_env="MY_TOKEN", token_command="exit 3")
        assert base.resolve_token(prcfg) == "env-tok"


class TestGetProvider:
    def test_known_providers(self):
        assert base.get_provider("gitea").name == "gitea"
        assert base.get_provider("github").name == "github"
        assert base.get_provider("azure-devops").name == "azure-devops"

    def test_unknown_raises(self):
        with pytest.raises(ProviderError, match="Unknown PR provider"):
            base.get_provider("bitbucket")


class TestScopeFromResult:
    def test_builds_scope_and_templates_labels(self):
        prcfg = cfg.PRConfig(api_base="https://h/gitea", labels=("auto-merge", "source:{machine}"))
        scope = base.scope_from_create_result(
            {"repo": "o/r", "branch": "feature/x", "default_branch": "master"},
            title="T", body="B", prcfg=prcfg, machine="lambda-core",
        )
        assert scope.repo == "o/r"
        assert scope.head == "feature/x"
        assert scope.base == "master"
        assert scope.api_base == "https://h/gitea"
        assert scope.labels == ("auto-merge", "source:lambda-core")


# ---------------------------------------------------------------------------
# attribution markers
# ---------------------------------------------------------------------------

class TestAttribution:
    def test_build_and_parse_round_trip(self):
        marker = attribution.build_marker(
            "wt-123", machine="lambda-core", session="sess-9", head="abc123",
        )
        fields = attribution.parse_marker(f"Some body\n\n{marker}\n")
        assert fields == {
            "worktree": "wt-123", "machine": "lambda-core",
            "session": "sess-9", "head": "abc123",
        }

    def test_append_replaces_existing_marker(self):
        m1 = attribution.build_marker("wt-1")
        m2 = attribution.build_marker("wt-2")
        body = attribution.append_marker("Hello", m1)
        body = attribution.append_marker(body, m2)
        assert body.count("agent-worktrees:source") == 1
        assert attribution.parse_marker(body)["worktree"] == "wt-2"

    def test_parse_none_when_absent(self):
        assert attribution.parse_marker("no marker here") is None


# ---------------------------------------------------------------------------
# Gitea provider (curl seam mocked)
# ---------------------------------------------------------------------------

def _proc(stdout="", returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode,
                                       stdout=stdout, stderr=stderr)


class TestGiteaProvider:
    def test_create_pull_parses_url_and_number(self, monkeypatch):
        from agent_worktrees.providers import gitea
        body = json.dumps({"html_url": "https://h/gitea/o/r/pulls/42",
                            "number": 42, "state": "open"})
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return _proc(stdout=body + "\n201")

        monkeypatch.setattr(gitea, "run_cli", fake_run)
        prov = gitea.GiteaProvider()
        scope = PRScope(repo="o/r", head="feature/x", base="master",
                        title="T", body="B", api_base="https://h/gitea")
        res = prov.create_pull(scope, token="tok")
        assert res.number == 42
        assert res.url == "https://h/gitea/o/r/pulls/42"
        # POSTs to the pulls endpoint with the token header.
        assert any("/repos/o/r/pulls" in a for call in calls for a in call)

    def test_create_pull_requires_token(self):
        from agent_worktrees.providers import gitea
        scope = PRScope(repo="o/r", head="h", base="b", title="T",
                        api_base="https://h/gitea")
        with pytest.raises(ProviderError, match="needs a token"):
            gitea.GiteaProvider().create_pull(scope, token=None)

    def test_create_pull_requires_api_base(self):
        from agent_worktrees.providers import gitea
        scope = PRScope(repo="o/r", head="h", base="b", title="T")
        with pytest.raises(ProviderError, match="api_base"):
            gitea.GiteaProvider().create_pull(scope, token="tok")

    def test_http_error_raises(self, monkeypatch):
        from agent_worktrees.providers import gitea
        monkeypatch.setattr(gitea, "run_cli",
                            lambda args, **kw: _proc(stdout="boom\n422"))
        scope = PRScope(repo="o/r", head="h", base="b", title="T",
                        api_base="https://h/gitea")
        with pytest.raises(ProviderError, match="HTTP 422"):
            gitea.GiteaProvider().create_pull(scope, token="tok")


# ---------------------------------------------------------------------------
# GitHub provider (gh seam mocked)
# ---------------------------------------------------------------------------

class TestGitHubProvider:
    def test_create_pull_parses_number_from_url(self, monkeypatch):
        from agent_worktrees.providers import github
        monkeypatch.setattr(
            github, "run_cli",
            lambda args, **kw: _proc(stdout="https://github.com/o/r/pull/7\n"),
        )
        scope = PRScope(repo="o/r", head="h", base="master", title="T")
        res = github.GitHubProvider().create_pull(scope, token=None)
        assert res.url == "https://github.com/o/r/pull/7"
        assert res.number == 7


# ---------------------------------------------------------------------------
# create_pr auto-open wiring (fake provider)
# ---------------------------------------------------------------------------

@dataclass
class _FakeProvider:
    name: str = "gitea"
    captured: dict | None = None

    def create_pull(self, scope, *, token=None):
        self.captured = {"scope": scope, "token": token}
        return PullResult(url="https://h/gitea/ext/pulls/99", number=99, state="open")

    def get_pull(self, repo, number, *, api_base="", token=None):
        return PullResult(url="", number=number)


class TestCreatePRAutoOpen:
    def _enable_open(self, config):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, auto_open=True, api_base="https://h/gitea",
            token_env="EXT_TOKEN", labels=("auto-merge", "source:{machine}"),
        )
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)}
        )

    def test_auto_open_records_pr_and_embeds_marker(self, pr_repo, monkeypatch):
        from agent_worktrees import providers
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr(providers, "get_provider", lambda name: fake)
        # pr_ops imports providers lazily inside _open_via_provider
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True
        assert res["pr_opened"] is True
        assert res["number"] == 99
        assert res["url"] == "https://h/gitea/ext/pulls/99"

        # The worktree auto-recorded the PR (no manual set-pr).
        rec = tracking.load_record(cfg.tracking_dir() / f"{wid}.yaml")
        assert rec.active_pr().number == 99
        assert rec.active_pr().url == "https://h/gitea/ext/pulls/99"

        # The PR body carries the source-worktree attribution marker.
        scope = fake.captured["scope"]
        fields = attribution.parse_marker(scope.body)
        assert fields["worktree"] == wid
        assert fields["machine"] == "test"
        # Labels templated with the machine.
        assert "source:test" in scope.labels
        assert fake.captured["token"] == "tok"

    def test_no_open_skips_provider(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)

        def _boom(name):
            raise AssertionError("provider should not be called with open_pr=False")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _boom)
        res = pr_ops.create_pr(wid, config, title="Add feature", open_pr=False)
        assert res["success"] is True
        assert "pr_opened" not in res

    def test_provider_failure_is_non_fatal(self, pr_repo, monkeypatch):
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")

        def _fail(name):
            raise ProviderError("gitea exploded")

        monkeypatch.setattr("agent_worktrees.providers.get_provider", _fail)
        res = pr_ops.create_pr(wid, config, title="Add feature")
        assert res["success"] is True  # branch still pushed
        assert res["pr_opened"] is False
        assert "gitea exploded" in res["pr_open_error"]

    def test_no_attribution_omits_marker(self, pr_repo, monkeypatch):
        from agent_worktrees.providers import attribution as attr
        config, wid, _wt, _ = pr_repo
        config = self._enable_open(config)
        monkeypatch.setenv("EXT_TOKEN", "tok")
        fake = _FakeProvider()
        monkeypatch.setattr("agent_worktrees.providers.get_provider", lambda name: fake)

        pr_ops.create_pr(wid, config, title="Add feature", body="Hello",
                         attribution=False)
        scope = fake.captured["scope"]
        assert attr.parse_marker(scope.body) is None
        assert scope.body == "Hello"
