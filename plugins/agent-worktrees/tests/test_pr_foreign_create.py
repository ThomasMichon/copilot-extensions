"""``pr_foreign_create.create_foreign_pr_from_branch`` -- opening a PR on a
registered-but-foreign repo from an already-pushed branch (no local checkout
of the target), and auto-journaling the resulting claim onto the CALLING
worktree (``pull-request-capability`` effort, Phase 2d)."""

from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import pr_config, pr_foreign_create, tracking
from agent_worktrees.providers import base as providers


class _FakePull:
    def __init__(self, *, url, number, state="open", label_error=""):
        self.url = url
        self.number = number
        self.state = state
        self.label_error = label_error


class _FakeProvider:
    name = "gitea"

    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error
        self.calls: list = []

    def create_pull(self, scope, *, token=None):
        self.calls.append((scope, token))
        if self._error:
            raise self._error
        return self._result


@pytest.fixture
def _tracking_setup(tmp_path, monkeypatch):
    tracking_d = tmp_path / "tracking"
    tracking_d.mkdir()
    monkeypatch.setattr(cfg, "tracking_dir", lambda name=None: tracking_d)
    worktree_id = "caller-wt-20261004-aaaa"
    tracking.create_new_record(
        worktree_id, f"worktree/{worktree_id}", str(tmp_path / "caller"),
        "caller-repo", "test", "linux", tracking_d,
    )
    return tracking_d, worktree_id


def _config(worktree_repo="caller-repo"):
    return cfg.Config(
        srcroot="/tmp", machine="test", platform="linux",
        repo_name=worktree_repo,
        repos={worktree_repo: cfg.RepoConfig(
            anchor="/tmp/anchor", worktree_root="/tmp/wt",
            default_branch="master", remote="origin",
            pr=cfg.PRConfig(enabled=True, provider="gitea"),
        )},
    )


def _foreign_resolution(
    *, same_as_active=False, provider="gitea", labels=(),
    required_body_sections=(), source_attribution="codename",
):
    repo_cfg = cfg.RepoConfig(
        anchor="/tmp/other-anchor", worktree_root="/tmp/other-wt",
        default_branch="dev", remote="origin",
        pr=cfg.PRConfig(
            enabled=True, provider=provider, labels=labels,
            required_body_sections=required_body_sections,
            source_attribution=source_attribution,
        ),
    )
    return pr_config.ForeignRepoResolution(
        repo_cfg, "owner/other-repo", same_as_active=same_as_active,
    )


class TestCreateForeignPrFromBranch:
    def test_unregistered_target_fails_honestly(self, monkeypatch, _tracking_setup):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: pr_config.ForeignRepoResolution(None),
        )
        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/ghost", from_branch="topic",
            title="x",
        )
        assert "error" in result
        assert "not a registered repo" in result["error"]

    def test_same_as_active_is_refused(self, monkeypatch, _tracking_setup):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(same_as_active=True),
        )
        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert "error" in result
        assert "own active repo" in result["error"]

    def test_opens_pr_and_claims_it_onto_the_calling_worktree(
        self, monkeypatch, _tracking_setup,
    ):
        tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        fake_pull = _FakePull(url="https://example/owner/other-repo/pull/9", number=9)
        fake_provider = _FakeProvider(result=fake_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo",
            from_branch="user/topic-wt", title="Fix thing",
        )

        assert result["pr_opened"] is True
        assert result["number"] == 9
        assert result["url"] == fake_pull.url
        assert result["base"] == "dev"  # the FOREIGN repo's own default branch
        assert result["claimed"] is True

        # The scope passed to the provider targets the foreign repo/branch,
        # never anything derived from the calling worktree's own git state.
        scope, _token = fake_provider.calls[0]
        assert scope.repo == "owner/other-repo"
        assert scope.head == "user/topic-wt"
        assert scope.base == "dev"

        # The claim landed on the CALLING worktree's own record, not any
        # record for the foreign repo (which this worktree has no checkout
        # of and no tracking record for).
        record = tracking.load_record(tracking_d / f"{wid}.yaml")
        refs = [c.ref for c in record.resources if c.kind == "pr"]
        assert fake_pull.url in refs

    def test_provider_failure_degrades_to_an_error_result(
        self, monkeypatch, _tracking_setup,
    ):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        fake_provider = _FakeProvider(error=providers.ProviderError("boom"))
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo",
            from_branch="topic", title="x",
        )
        assert result["error"] == "boom"
        assert "pr_opened" not in result

    def test_missing_tracking_record_warns_instead_of_crashing(
        self, monkeypatch, tmp_path,
    ):
        tracking_d = tmp_path / "tracking"
        tracking_d.mkdir()
        import agent_worktrees.config as cfg_mod

        monkeypatch.setattr(cfg_mod, "tracking_dir", lambda name=None: tracking_d)
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        fake_pull = _FakePull(url="https://example/owner/other-repo/pull/1", number=1)
        monkeypatch.setattr(
            providers, "get_provider", lambda name: _FakeProvider(result=fake_pull),
        )
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            "nonexistent-wt", _config(), target_repo="owner/other-repo",
            from_branch="topic", title="x",
        )
        assert result["pr_opened"] is True
        assert result["claimed"] is False
        assert "claim_warning" in result

    def test_attribution_disabled_strips_any_marker(self, monkeypatch, _tracking_setup):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        fake_pull = _FakePull(url="https://example/owner/other-repo/pull/2", number=2)
        fake_provider = _FakeProvider(result=fake_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="hello <!-- agent-worktrees:source worktree=leaked -->",
            attribution=False,
        )
        scope, _token = fake_provider.calls[0]
        assert "agent-worktrees:source" not in scope.body
        assert "hello" in scope.body

    def test_respects_the_target_repos_required_body_sections(
        self, monkeypatch, _tracking_setup,
    ):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(
                required_body_sections=("## Intent",),
            ),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/3", number=3),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="no intent section here",
        )
        assert "error" in result
        assert "required non-empty section" in result["error"]
        assert fake_provider.calls == []  # never reached the provider

    def test_defaults_attribution_from_the_target_repos_own_config(
        self, monkeypatch, _tracking_setup,
    ):
        """``attribution=None`` (the default, no ``--no-attribution``) must
        resolve from the TARGET repo's own ``pr.source_attribution``, not an
        unconditional True -- a target configured ``source_attribution:
        false`` must never get a marker just because the caller didn't pass
        --no-attribution."""
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(source_attribution=False),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/4", number=4),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="hello",
        )
        scope, _token = fake_provider.calls[0]
        assert "agent-worktrees:source" not in scope.body

    def test_a_claim_persistence_exception_degrades_to_a_warning(
        self, monkeypatch, _tracking_setup,
    ):
        """The PR already exists on the provider once ``_ensure_pr_claim``/
        ``save_record``/``record_pr_event`` run -- any exception there must
        never escape and read as the whole create failing."""
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        fake_pull = _FakePull(url="https://example/pr/5", number=5)
        monkeypatch.setattr(
            providers, "get_provider", lambda name: _FakeProvider(result=fake_pull),
        )
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)
        monkeypatch.setattr(
            tracking, "save_record",
            lambda record: (_ for _ in ()).throw(OSError("disk full")),
        )

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert result["pr_opened"] is True
        assert result["url"] == fake_pull.url
        assert result["claimed"] is False
        assert "claim_warning" in result
        assert fake_pull.url in result["claim_warning"]
