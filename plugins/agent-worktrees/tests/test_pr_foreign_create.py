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

    def __init__(self, result=None, error=None, existing=None):
        self._result = result
        self._error = error
        self._existing = existing
        self.calls: list = []
        self.find_calls: list = []

    def find_pull_by_head(self, repo, head, *, api_base="", token=None):
        self.find_calls.append((repo, head, api_base, token))
        return self._existing

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
    enabled=True, source_attribution_configured=True,
):
    repo_cfg = cfg.RepoConfig(
        anchor="/tmp/other-anchor", worktree_root="/tmp/other-wt",
        default_branch="dev", remote="origin",
        pr=cfg.PRConfig(
            enabled=enabled, provider=provider, labels=labels,
            required_body_sections=required_body_sections,
            source_attribution=source_attribution,
            source_attribution_configured=source_attribution_configured,
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

    def test_refuses_when_the_target_repo_has_pr_mode_disabled(
        self, monkeypatch, _tracking_setup,
    ):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(enabled=False),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/6", number=6),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert "error" in result
        assert "not enabled" in result["error"]
        assert fake_provider.calls == []

    def test_normalizes_azure_devops_active_state_to_open_for_the_claim(
        self, monkeypatch, _tracking_setup,
    ):
        """ADO's own create_pull() returns its native status ("active"),
        never the cross-provider "open" literal _ensure_pr_claim requires --
        an unnormalized ADO PR would otherwise silently open unclaimed."""
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(provider="azure-devops"),
        )
        fake_pull = _FakePull(
            url="https://example/pr/7", number=7, state="active",
        )
        monkeypatch.setattr(
            providers, "get_provider", lambda name: _FakeProvider(result=fake_pull),
        )
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert result["state"] == "open"
        assert result["claimed"] is True
        record = tracking.load_record(_tracking_d / f"{wid}.yaml")
        refs = [c.ref for c in record.resources if c.kind == "pr"]
        assert fake_pull.url in refs

    def test_codename_from_a_custom_wordlist_never_publishes_unconfigured(
        self, monkeypatch, _tracking_setup,
    ):
        """Mirrors the local path's provenance gate: an implicit (never
        explicitly reviewed) custom-wordlist codename must never publish,
        even though it's a syntactically valid handle."""
        _tracking_d, wid = _tracking_setup
        record = tracking.load_record(_tracking_d / f"{wid}.yaml")
        record.codename = "shimmering-quartz"
        record.codename_source = "custom"
        tracking.save_record(record)

        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(
                source_attribution="codename", source_attribution_configured=False,
            ),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/8", number=8),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="hello",
        )
        scope, _token = fake_provider.calls[0]
        assert "agent-worktrees:source" not in scope.body
        assert "shimmering-quartz" not in scope.body

    def test_codename_from_a_custom_wordlist_publishes_once_configured(
        self, monkeypatch, _tracking_setup,
    ):
        _tracking_d, wid = _tracking_setup
        record = tracking.load_record(_tracking_d / f"{wid}.yaml")
        record.codename = "shimmering-quartz"
        record.codename_source = "custom"
        tracking.save_record(record)

        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(
                source_attribution="codename", source_attribution_configured=True,
            ),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/9", number=9),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="hello",
        )
        scope, _token = fake_provider.calls[0]
        assert "shimmering-quartz" in scope.body

    def test_codename_marker_is_folded_with_the_root_chain_owner(
        self, monkeypatch, _tracking_setup,
    ):
        """The foreign-PR path must apply the SAME root-aware codename
        composition as the local `create_pr` path (review finding on PR
        #5458): a calling worktree with an `owner_ref` chain must publish
        `root=<root-codename>` alongside its own codename here too."""
        tracking_d, wid = _tracking_setup
        record = tracking.load_record(tracking_d / f"{wid}.yaml")
        record.codename = "shimmering-quartz"
        record.codename_source = "built-in"
        record.owner_ref = "test/root-repo/root-wt#s1"
        tracking.save_record(record)

        root_project_dir = tracking_d.parent / "root-project"
        root_tracking_d = root_project_dir / "worktrees"
        root_tracking_d.mkdir(parents=True)
        tracking.create_new_record(
            "root-wt", "worktree/root-wt", str(tracking_d.parent / "root"),
            "root-repo", "test", "linux", root_tracking_d,
            codename="amber-thicket", codename_source="built-in",
        )
        monkeypatch.setattr(
            cfg, "project_dir",
            lambda name=None: (
                root_project_dir if name == "root-repo" else tracking_d.parent
            ),
        )
        monkeypatch.setattr(cfg, "load_config", lambda *a, **k: _config())
        monkeypatch.setattr(
            cfg, "load_project_config", lambda name, **k: _config(name),
        )

        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(
                source_attribution="codename", source_attribution_configured=True,
            ),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/10", number=10),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="hello",
        )
        scope, _token = fake_provider.calls[0]
        assert "codename=shimmering-quartz" in scope.body
        assert "root=amber-thicket" in scope.body

    def test_raw_marker_uses_the_records_own_machine_and_latest_live_session(
        self, monkeypatch, _tracking_setup,
    ):
        """The raw (non-codename) marker must identify the CALLING worktree's
        own record (machine + latest live session), matching the local
        path's selection -- never this process's own config.machine, and
        never parent_session (the session that originally spawned the
        worktree, which may not be the one driving this PR)."""
        tracking_d, wid = _tracking_setup
        record = tracking.load_record(tracking_d / f"{wid}.yaml")
        record.machine = "record-own-machine"
        record.parent_session = "spawning-session-should-be-ignored"
        record.sessions = [
            tracking.SessionEntry(
                session_id="old-ended-session", started_at="2026-01-01T00:00:00",
                ended_at="2026-01-01T01:00:00",
            ),
            tracking.SessionEntry(
                session_id="latest-live-session", started_at="2026-01-02T00:00:00",
                ended_at=None,
            ),
        ]
        tracking.save_record(record)

        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(source_attribution=True),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/10", number=10),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        import dataclasses as _dc
        config = _dc.replace(_config(), machine="process-own-machine-should-be-ignored")
        pr_foreign_create.create_foreign_pr_from_branch(
            wid, config, target_repo="owner/other-repo", from_branch="topic",
            title="x", body="hello",
        )
        scope, _token = fake_provider.calls[0]
        assert "machine=record-own-machine" in scope.body
        assert "session=latest-live-session" in scope.body
        assert "spawning-session-should-be-ignored" not in scope.body
        assert "process-own-machine-should-be-ignored" not in scope.body

    def test_reuses_an_existing_open_pr_on_the_same_head_instead_of_duplicating(
        self, monkeypatch, _tracking_setup,
    ):
        """Matches create_pr's own idempotent "safe to re-run" contract: a
        retried --repo/--from-branch call for the SAME head must not ask
        the forge to open a duplicate PR."""
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        existing_pull = _FakePull(url="https://example/pr/11", number=11, state="open")
        fake_provider = _FakeProvider(existing=existing_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert result["reused"] is True
        assert result["number"] == 11
        assert result["claimed"] is True
        assert fake_provider.calls == []  # create_pull was never called
        assert len(fake_provider.find_calls) == 1

    def test_ignores_a_terminal_existing_pr_and_opens_a_fresh_one(
        self, monkeypatch, _tracking_setup,
    ):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        terminal_pull = _FakePull(url="https://example/pr/12", number=12, state="merged")
        fresh_pull = _FakePull(url="https://example/pr/13", number=13, state="open")
        fake_provider = _FakeProvider(result=fresh_pull, existing=terminal_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert result.get("reused", False) is False
        assert result["number"] == 13
        assert len(fake_provider.calls) == 1

    def test_new_flag_skips_the_reuse_lookup_entirely(self, monkeypatch, _tracking_setup):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        existing_pull = _FakePull(url="https://example/pr/14", number=14, state="open")
        fresh_pull = _FakePull(url="https://example/pr/15", number=15, state="open")
        fake_provider = _FakeProvider(result=fresh_pull, existing=existing_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", new=True,
        )
        assert result["number"] == 15
        assert fake_provider.find_calls == []
        assert len(fake_provider.calls) == 1

    def test_raw_marker_never_passes_the_branch_name_as_a_sha(
        self, monkeypatch, _tracking_setup,
    ):
        """build_marker's `head=` is documented as a commit SHA; this
        no-checkout path has no verified SHA before creation and must omit
        the field rather than passing the branch name as if it were one."""
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(source_attribution=True),
        )
        fake_provider = _FakeProvider(
            result=_FakePull(url="https://example/pr/16", number=16),
        )
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo",
            from_branch="my-unverified-branch-name", title="x", body="hello",
        )
        scope, _token = fake_provider.calls[0]
        assert "head=my-unverified-branch-name" not in scope.body
        assert "worktree=" in scope.body

    def test_required_body_sections_check_is_skipped_on_an_idempotent_reuse(
        self, monkeypatch, _tracking_setup,
    ):
        """An idempotent retry (an existing open PR found) must not fail
        the required_body_sections check just because the caller didn't
        resupply the original body -- no NEW PR is being opened at all."""
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(
                required_body_sections=("## Intent",),
            ),
        )
        existing_pull = _FakePull(url="https://example/pr/17", number=17, state="open")
        fake_provider = _FakeProvider(existing=existing_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", body="no intent section here",
        )
        assert "error" not in result
        assert result["reused"] is True
        assert result["number"] == 17

    def test_reused_pr_reports_draft_false_regardless_of_the_callers_request(
        self, monkeypatch, _tracking_setup,
    ):
        _tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        existing_pull = _FakePull(url="https://example/pr/18", number=18, state="open")
        fake_provider = _FakeProvider(existing=existing_pull)
        monkeypatch.setattr(providers, "get_provider", lambda name: fake_provider)
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x", draft=True,
        )
        assert result["reused"] is True
        assert result["draft"] is False

    def test_an_idempotent_retry_reports_claimed_true_not_a_bogus_warning(
        self, monkeypatch, _tracking_setup,
    ):
        """_ensure_pr_claim returns None both when a claim genuinely fails
        AND when it's already active (nothing new to journal) -- a retry
        whose ledger is already correct must report claimed: true, not a
        false "not claimed" warning."""
        tracking_d, wid = _tracking_setup
        monkeypatch.setattr(
            pr_config, "resolve_repo_config_for_slug",
            lambda config, slug: _foreign_resolution(),
        )
        fake_pull = _FakePull(url="https://example/pr/19", number=19)
        monkeypatch.setattr(
            providers, "get_provider", lambda name: _FakeProvider(result=fake_pull),
        )
        monkeypatch.setattr(providers, "account_token_for_slug", lambda slug, prcfg: None)

        # First call: journals the claim for real.
        pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        # Second call against the SAME PR (simulating a retry/duplicate
        # claim attempt) -- _ensure_pr_claim now returns None because the
        # claim is already active, not because anything failed.
        result = pr_foreign_create.create_foreign_pr_from_branch(
            wid, _config(), target_repo="owner/other-repo", from_branch="topic",
            title="x",
        )
        assert result["claimed"] is True
        assert "claim_warning" not in result
        record = tracking.load_record(tracking_d / f"{wid}.yaml")
        refs = [c.ref for c in record.resources if c.kind == "pr"]
        assert refs.count(fake_pull.url) == 1  # never duplicated
