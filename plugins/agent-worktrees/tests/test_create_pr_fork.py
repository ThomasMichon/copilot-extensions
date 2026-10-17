"""Fork confirmation and role-resolved PR creation."""

from __future__ import annotations

import pytest
from pathlib import Path
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, pr_ops

pytestmark = pytest.mark.contract("agent_worktrees.pr_ops.create_fork")


class TestCreatePRForkFlow:
    def _fork_config(self, config, tmp_path: Path, **fork_overrides):
        import dataclasses
        repo = config.repos["ext"]
        fork = cfg.ForkConfig(enabled=True, remote="fork", **fork_overrides)
        pr = dataclasses.replace(repo.pr, provider="github", fork=fork)
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)},
        )

    def _fake_provider(self, fork_owner: str, fork_clone_url: str):
        class _FakeProvider:
            name = "github"

            def authority_endpoint(self, api_base=""):
                return "github.com"

            def ensure_fork(self, repo, *, api_base="", token=None):
                return (fork_owner, fork_clone_url)

            def resolve_fork_owner(self, *, api_base="", token=None):
                return fork_owner

        return _FakeProvider()

    def test_fork_mode_needs_confirmation_and_does_nothing(self, pr_repo, tmp_path):
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)

        res = pr_ops.create_pr(wid, config)

        assert res["success"] is False
        assert res["needs_confirmation"] == "fork_setup"
        assert "fork" in res["message"].lower()
        assert res["fork_remote"] == "fork"
        # Nothing was mutated: no fork remote configured, no branch pushed.
        assert not git_ops.has_remote("fork", cwd=str(wt_path))
        assert not git_ops.local_branch_exists(
            "feature/work-2-aaaa", cwd=str(wt_path)
        )

    def test_fork_mode_rejects_a_fork_remote_named_like_the_repo_remote(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A fork's publication target is recognized by its remote name: one sharing
        the repo's own remote would record no fork identity, so a later repoint of that
        remote would go unchecked. Refused before anything changes, even confirmed."""
        import dataclasses

        from agent_worktrees import providers

        config, wid, wt_path, remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, fork=dataclasses.replace(repo.pr.fork, remote=repo.remote))
        config = dataclasses.replace(config, repos={"ext": dataclasses.replace(repo, pr=pr)})
        monkeypatch.delenv("GH_HOST", raising=False)
        monkeypatch.setattr(providers, "get_provider", lambda _name: pytest.fail("no provider call"))
        url_before = git_ops.git("remote", "get-url", repo.remote, cwd=str(wt_path)).stdout

        res = pr_ops.create_pr(wid, config, confirm_fork=True)

        assert res.get("success") is not True, res
        assert "the repository's own remote" in res["error"]
        assert git_ops.git("remote", "get-url", repo.remote, cwd=str(wt_path)).stdout == url_before

    def test_fork_mode_rejects_non_default_gh_host(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """pr.fork's durable registry isn't scoped by GitHub authority, so a
        non-default GH_HOST must be refused outright -- even on a caller's
        very first, explicitly-confirmed call -- rather than risk a later
        confirmation silently reusing across github.com vs. an Enterprise
        host with the same owner/repo slug."""
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)
        monkeypatch.setenv("GH_HOST", "github.example.com")

        res = pr_ops.create_pr(wid, config, confirm_fork=True)

        assert res["success"] is False, res
        assert "non-default GitHub authority" in res["error"]
        assert not git_ops.has_remote("fork", cwd=str(wt_path))

    def test_fork_mode_rejects_non_default_api_base_even_without_gh_host(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """The exact gap this check used to miss: an explicit pr.api_base
        pointing at a GitHub Enterprise host, with ambient GH_HOST entirely
        UNSET, must still be refused -- the effective authority GitHub role
        resolution actually uses comes from pr.api_base first, not GH_HOST."""
        import dataclasses

        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)
        repo = config.repos["ext"]
        pr = dataclasses.replace(repo.pr, api_base="https://github.example.com/api/v3")
        config = dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)},
        )
        monkeypatch.delenv("GH_HOST", raising=False)

        res = pr_ops.create_pr(wid, config, confirm_fork=True)

        assert res["success"] is False, res
        assert "non-default GitHub authority" in res["error"]
        assert "github.example.com" in res["error"]
        assert not git_ops.has_remote("fork", cwd=str(wt_path))

    def test_fork_mode_confirmed_pushes_to_fork_not_origin(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)

        fork_dir = tmp_path / "fork.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("theirfork", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )

        res = pr_ops.create_pr(wid, config, confirm_fork=True)

        assert res["success"] is True, res
        assert res["remote"] == "fork"
        assert res["pr_head"] == "theirfork:feature/work-2-aaaa"
        # The local 'fork' remote now points at the fake fork's clone URL.
        assert git_ops.remote_url("fork", cwd=str(wt_path)) == str(fork_dir)
        # The branch landed on the FORK, never on origin.
        assert git_ops.remote_branch_exists(
            "fork", "feature/work-2-aaaa", cwd=str(wt_path)
        )
        origin_refs = git_ops.git(
            "ls-remote", "origin", cwd=str(wt_path)
        ).stdout
        assert "feature/work-2-aaaa" not in origin_refs

    def test_fork_owner_override_wins_over_provider_login(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path, owner="explicit-owner")

        fork_dir = tmp_path / "fork2.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("provider-login", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )

        res = pr_ops.create_pr(wid, config, confirm_fork=True)

        assert res["success"] is True, res
        assert res["pr_head"] == "explicit-owner:feature/work-2-aaaa"

    def test_fork_mode_off_by_default(self, pr_repo):
        """A repo that never sets pr.fork/pr.roles is fully unaffected."""
        config, wid, _wt_path, _remote_dir = pr_repo
        res = pr_ops.create_pr(wid, config)
        assert res["success"] is True, res
        assert "needs_confirmation" not in res
        assert "pr_head" not in res
        assert res["remote"] == "origin"

    def test_confirmed_fork_is_remembered_for_next_call(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A confirmed fork is durable: once create_pr(confirm_fork=True)
        succeeds for a repo, a LATER create_pr call for that same repo (a
        fresh PR iteration, no confirm_fork) must not re-ask -- the approval
        was recorded in fork_pr's registry, not just honored for this call."""
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)

        fork_dir = tmp_path / "fork-remembered.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("theirfork", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "remembered-login"),
        )

        from agent_worktrees import fork_pr
        assert fork_pr.list_forks() == []

        first = pr_ops.create_pr(
            wid, config, confirm_fork=True, target_repo="acme/remembered-repo",
        )
        assert first["success"] is True, first
        assert fork_pr.is_confirmed(
            "acme/remembered-repo", account="remembered-login",
        ) is True

        # Simulate a later call (e.g. a different worktree of the same repo,
        # or a fresh PR after the first merged) WITHOUT confirm_fork -- it
        # must proceed straight to publishing, not re-ask. Give it a distinct
        # branch so it doesn't collide with the first call's feature branch.
        second = pr_ops.create_pr(
            wid, config, new=True, target_repo="acme/remembered-repo",
            branch="feature/work-2-aaaa-2",
        )
        assert second.get("needs_confirmation") is None, second
        assert second["success"] is True, second
        assert second["remote"] == "fork"

    def test_preseeded_fork_skips_confirmation_entirely(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A repo pre-approved via the 'forks set' flow (e.g. during machine/
        harness setup) never triggers needs_confirmation, even on the very
        first create_pr call -- confirm_fork=True is never passed."""
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)

        fork_dir = tmp_path / "fork-preseeded.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("preseeded-owner", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "preseeded-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/preseeded-repo"
        fork_pr.record_confirmation(
            repo, "preseeded-owner", account="preseeded-login",
        )

        res = pr_ops.create_pr(
            wid, config, target_repo=repo,
        )  # no confirm_fork at all

        assert "needs_confirmation" not in res, res
        assert res["success"] is True, res
        assert res["pr_head"] == "preseeded-owner:feature/work-2-aaaa"

    def test_owner_override_does_not_bypass_live_identity_check(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """pr.fork.owner only overrides the login used to build the PR
        head -- it must NOT exempt the underlying authenticated identity
        from the live-owner pre-check. If the real identity silently
        switches (e.g. ambient `gh` auth re-authenticates as someone else)
        while the override keeps displaying the ORIGINALLY-approved name,
        a silent skip must still catch that and re-prompt -- otherwise a
        fork/push could go to an unapproved account while the PR head
        keeps naming the approved one."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path, owner="alice")

        fork_dir = tmp_path / "fork-override-identity.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))

        class _MutableIdentityProvider:
            name = "github"
            real_owner = "alice-real-login"

            def authority_endpoint(self, api_base=""):
                return "github.com"

            def ensure_fork(self, repo, *, api_base="", token=None):
                return (self.real_owner, str(fork_dir))

            def resolve_fork_owner(self, *, api_base="", token=None):
                return self.real_owner

        identity_provider = _MutableIdentityProvider()
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: identity_provider,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "override-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/override-identity-repo"

        first = pr_ops.create_pr(
            wid, config, confirm_fork=True, target_repo=repo,
        )
        assert first["success"] is True, first
        assert first["pr_head"] == "alice:feature/work-2-aaaa"
        entry = fork_pr.find_fork(repo, "override-login")
        assert entry.owner == "alice"
        assert entry.real_owner == "alice-real-login"

        # A later call with no confirm_fork, identity unchanged -- the
        # override must NOT skip the check so aggressively that a genuine
        # match still works.
        second = pr_ops.create_pr(
            wid, config, target_repo=repo,
            branch="feature/work-2-aaaa-override-2",
        )
        assert "needs_confirmation" not in second, second
        assert second["success"] is True, second
        assert second["pr_head"] == "alice:feature/work-2-aaaa-override-2"

        # Now the REAL authenticated identity silently switches -- the
        # override still names "alice" for display, but the actual account
        # that would authenticate/fork/push is now someone else entirely.
        identity_provider.real_owner = "bob-real-login"

        third = pr_ops.create_pr(
            wid, config, target_repo=repo,
            branch="feature/work-2-aaaa-override-3",
        )
        assert third["success"] is False, third
        assert third["needs_confirmation"] == "fork_setup", third
        assert "alice-real-login" in third["message"]
        assert "bob-real-login" in third["message"]
        # The stored entry must still name the ORIGINAL identity -- nothing
        # was silently re-approved for Bob.
        entry2 = fork_pr.find_fork(repo, "override-login")
        assert entry2.real_owner == "alice-real-login"

    def test_clearing_owner_override_requires_reconfirmation(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A prior approval confirmed WITH an explicit pr.fork.owner
        override (owner='alias', real_owner='alice') is a DIFFERENT
        approval than a later call with that override CLEARED -- the PR
        head will now display the real identity ('alice') instead of the
        approved alias. This must re-prompt BEFORE any mutation (the
        normal needs_confirmation path), not run the fork/remote bootstrap
        first and only then fail with a misleading 'concurrent identity
        change' error -- the real_owner itself never actually changed."""
        config_with_override, wid, _wt_path, _remote_dir = pr_repo
        config_with_override = self._fork_config(
            config_with_override, tmp_path, owner="alias",
        )

        fork_dir = tmp_path / "fork-clear-override.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("alice", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "clear-override-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/clear-override-repo"
        first = pr_ops.create_pr(
            wid, config_with_override, confirm_fork=True, target_repo=repo,
        )
        assert first["success"] is True, first
        entry = fork_pr.find_fork(repo, "clear-override-login")
        assert entry.owner == "alias"
        assert entry.real_owner == "alice"

        # Same repo/account, override now cleared.
        config_no_override = self._fork_config(config_with_override, tmp_path)
        res = pr_ops.create_pr(
            wid, config_no_override, target_repo=repo,
            branch="feature/clear-override-2",
        )  # no confirm_fork -- must re-ask, not mutate then error

        assert res["success"] is False, res
        assert res["needs_confirmation"] == "fork_setup", res
        # Nothing was mutated: the original approval is untouched.
        entry2 = fork_pr.find_fork(repo, "clear-override-login")
        assert entry2.owner == "alias"
        assert entry2.real_owner == "alice"

    def test_configured_owner_mismatch_forces_reconfirmation(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """An explicit pr.fork.owner deterministically decides the real fork
        owner independent of identity (see _ensure_fork_and_remote) -- so a
        stored confirmation recorded under a DIFFERENT owner is a different
        approval, not the same one under a new name, and must re-prompt
        rather than silently publish under the newly-configured owner."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path, owner="new-explicit-owner")

        fork_dir = tmp_path / "fork-owner-mismatch.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("new-explicit-owner", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "mismatch-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/owner-mismatch-repo"
        # A prior confirmation exists for this repo+account, but under a
        # DIFFERENT owner than what pr.fork.owner now configures.
        fork_pr.record_confirmation(
            repo, "stale-owner", account="mismatch-login",
        )

        res = pr_ops.create_pr(
            wid, config, target_repo=repo,
        )  # no confirm_fork -- must re-ask, not reuse the stale owner

        assert res["success"] is False, res
        assert res["needs_confirmation"] == "fork_setup", res

        # Confirming explicitly now proceeds, and re-records under the
        # NEW owner (not silently kept at the old stale one).
        res2 = pr_ops.create_pr(
            wid, config, confirm_fork=True, target_repo=repo,
            branch="feature/work-2-aaaa-mismatch",
        )
        assert res2["success"] is True, res2
        assert res2["pr_head"] == "new-explicit-owner:feature/work-2-aaaa-mismatch"
        entry = fork_pr.find_fork(repo, "mismatch-login")
        assert entry.owner == "new-explicit-owner"

    def test_live_resolved_owner_mismatch_forces_reconfirmation(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """Even with NO pr.fork.owner override configured, a stored
        confirmation's owner can still diverge from the actually resolved
        fork owner (a typo at 'forks set' time, or a genuine upstream
        change). A silent skip must re-validate against the LIVE result
        rather than trusting the stale stored owner -- silently publishing
        wherever the provider now resolves to is exactly the 'approved X,
        got Y' gap a pre-approval contract exists to prevent."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)  # no owner override

        fork_dir = tmp_path / "fork-live-mismatch.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("real-owner", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "live-mismatch-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/live-owner-mismatch-repo"
        # Stored confirmation names a DIFFERENT owner than the provider will
        # actually resolve for this (correctly matched) account/scope.
        fork_pr.record_confirmation(
            repo, "stale-typo-owner", account="live-mismatch-login",
        )

        res = pr_ops.create_pr(
            wid, config, target_repo=repo,
        )  # no confirm_fork -- must re-ask, not trust the stale stored owner

        assert res["success"] is False, res
        assert res["needs_confirmation"] == "fork_setup", res
        assert "real-owner" in res["message"]
        assert "stale-typo-owner" in res["message"]
        # Nothing was mutated: the mismatch was caught BEFORE
        # _ensure_fork_and_remote's mutating fork-create/remote-repoint ran.
        assert not git_ops.has_remote("fork", cwd=str(_wt_path))

        # Explicit re-confirmation proceeds and self-heals the stored entry
        # to the real, live-resolved owner.
        res2 = pr_ops.create_pr(
            wid, config, confirm_fork=True, target_repo=repo,
            branch="feature/work-2-aaaa-live-mismatch",
        )
        assert res2["success"] is True, res2
        assert res2["pr_head"] == "real-owner:feature/work-2-aaaa-live-mismatch"
        entry = fork_pr.find_fork(repo, "live-mismatch-login")
        assert entry.owner == "real-owner"

        # And now a THIRD call, with no confirm_fork, proceeds silently --
        # the stored entry matches the live result again.
        res3 = pr_ops.create_pr(
            wid, config, target_repo=repo,
            branch="feature/work-2-aaaa-live-mismatch-2",
        )
        assert "needs_confirmation" not in res3, res3
        assert res3["success"] is True, res3

    def test_login_comparisons_are_case_insensitive(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """GitHub logins are case-insensitive ('octocat' and 'OctoCat' name
        the same account) -- a stored confirmation recorded under one
        casing must still silently match when the live provider (or a
        pr.fork.owner override) later resolves/names the SAME login with
        different casing. A casing-only difference must never be treated
        as a changed/unapproved identity."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)  # no owner override

        fork_dir = tmp_path / "fork-case-insensitive.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        # The live provider resolves a DIFFERENT casing than what's stored.
        fake = self._fake_provider("OctoCat", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "case-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/case-insensitive-repo"
        fork_pr.record_confirmation(repo, "octocat", account="case-login")

        res = pr_ops.create_pr(
            wid, config, target_repo=repo,
        )  # no confirm_fork -- a casing-only difference must still match

        assert "needs_confirmation" not in res, res
        assert res["success"] is True, res

    def test_inconclusive_live_owner_check_fails_closed(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A FAILED/inconclusive non-mutating owner lookup (e.g. a transient
        API error) must ALSO fail closed, not silently trust the stored
        approval -- ensure_fork's own (separate) lookup moments later could
        legitimately resolve a DIFFERENT owner and persist it without this
        call ever having actually confirmed that was intended."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)  # no owner override

        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "inconclusive-login"),
        )
        # The non-mutating pre-check is inconclusive (simulates a transient
        # failure), regardless of what ensure_fork's own lookup might do.
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_live_fork_owner", lambda prcfg, token: None,
        )

        from agent_worktrees import fork_pr
        repo = "acme/inconclusive-owner-repo"
        fork_pr.record_confirmation(
            repo, "stored-owner", account="inconclusive-login",
        )

        res = pr_ops.create_pr(
            wid, config, target_repo=repo,
        )  # no confirm_fork -- an unverifiable owner must still re-prompt

        assert res["success"] is False, res
        assert res["needs_confirmation"] == "fork_setup", res
        assert not git_ops.has_remote("fork", cwd=str(_wt_path))

    def test_concurrent_identity_switch_between_precheck_and_bootstrap_fails_closed(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A TOCTOU gap with ambient `gh` auth (token=None): the non-mutating
        pre-check and the mutating fork bootstrap are two SEPARATE calls, so
        if another process switches the active `gh` identity between them,
        the pre-check can validate the OLD (stored) owner while bootstrap
        actually creates/repoints the fork for a NEW, different owner. That
        divergence must error out rather than silently re-persist the new
        owner under the old (unconfirmed-this-call) approval."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)  # no owner override

        fork_dir = tmp_path / "fork-race.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        # The pre-check (_resolve_live_fork_owner) and the actual bootstrap
        # (_ensure_fork_and_remote -> provider.ensure_fork) both go through
        # this SAME fake provider -- but resolve to DIFFERENT owners,
        # simulating the identity having switched in between.
        race_fake = self._fake_provider("race-condition-owner", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: race_fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "race-login"),
        )

        from agent_worktrees import fork_pr
        repo = "acme/race-condition-repo"
        fork_pr.record_confirmation(repo, "original-owner", account="race-login")
        # Pre-check reports the ORIGINAL (stored) owner still matches --
        # a silent skip is about to be granted...
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_live_fork_owner",
            lambda prcfg, token: "original-owner",
        )

        res = pr_ops.create_pr(
            wid, config, target_repo=repo,
        )  # no confirm_fork -- the pre-check alone must not be trusted if
        # the actual bootstrap below disagrees with it

        assert res["success"] is False, res
        assert "race-condition-owner" in res["error"]
        assert "original-owner" in res["error"]
        # The stored entry must still name the ORIGINAL owner -- the
        # race-resolved owner must never be silently persisted without an
        # explicit confirm_fork=True.
        entry = fork_pr.find_fork(repo, "race-login")
        assert entry.owner == "original-owner"

    def test_forks_set_default_scope_matches_create_pr_gate(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """'forks set' (no --account) must default to the SAME scope
        create_pr's gate checks against -- both go through
        _resolve_fork_credential, exercised here with NO mocking of that
        resolver itself (only its collaborators), so a real divergence
        between the two call sites would show up as a spurious re-prompt."""
        from agent_worktrees import forks_cli

        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)

        fork_dir = tmp_path / "fork-consistency.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("consistency-owner", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        # Pin the ambient collaborators (not _resolve_fork_credential
        # itself) so the test is deterministic across machines/CI.
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda slug: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.active_gh_account", lambda: "ambient-user",
        )

        repo = "acme/consistency-repo"
        rc = forks_cli.cmd_forks_dispatch(["set", repo, "--owner", "consistency-owner"])
        assert rc == 0

        res = pr_ops.create_pr(wid, config, target_repo=repo)  # no confirm_fork
        assert "needs_confirmation" not in res, res
        assert res["success"] is True, res

    def test_confirmation_does_not_cross_logins(
        self, pr_repo, tmp_path, monkeypatch,
    ):
        """A confirmation recorded under one effective login must not be
        honored once the repo resolves to a DIFFERENT one -- otherwise a
        stale confirmation would silently authorize forking/pushing under an
        identity the human never actually approved (e.g. an account-mapping
        change, or ambient gh auth switching users)."""
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._fork_config(config, tmp_path)

        fork_dir = tmp_path / "fork-account-a.git"
        git_ops.git("init", "--bare", "-b", "master", str(fork_dir))
        fake = self._fake_provider("account-a-owner", str(fork_dir))
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "login-a"),
        )

        confirmed = pr_ops.create_pr(
            wid, config, confirm_fork=True, target_repo="acme/cross-account-repo",
        )
        assert confirmed["success"] is True, confirmed

        # Now simulate the resolved login changing (an account-mapping edit,
        # or ambient `gh auth switch` to a different user).
        monkeypatch.setattr(
            "agent_worktrees.fork_pr._resolve_fork_credential",
            lambda slug, prcfg: (None, "login-b"),
        )
        asked_again = pr_ops.create_pr(
            wid, config, new=True, target_repo="acme/cross-account-repo",
            branch="feature/work-2-aaaa-cross",
        )
        assert asked_again.get("needs_confirmation") == "fork_setup", asked_again


class TestCreatePRRoleResolution:
    def _roles_config(self, config, **role_overrides):
        import dataclasses
        repo = config.repos["ext"]
        pr = dataclasses.replace(
            repo.pr, provider="github", roles=dict(role_overrides),
        )
        return dataclasses.replace(
            config, repos={"ext": dataclasses.replace(repo, pr=pr)},
        )

    def _fake_permission_provider(self, viewer_permission: str, *, supported=True):
        from types import SimpleNamespace

        class _FakeProvider:
            name = "github"

            def __init__(self):
                self.policy_calls = 0

            def authority_endpoint(self, api_base=""):
                return "github.com"

            def get_repo_policy(self, repo, *, api_base="", token=None):
                self.policy_calls += 1
                return SimpleNamespace(
                    supported=supported, viewer_permission=viewer_permission,
                )

            def create_pull(self, scope, *, token=None):
                return SimpleNamespace(
                    url="https://example/pulls/17",
                    number=17,
                    state="open",
                    label_error="",
                )

            def pull_review_gate(
                self, repo, number, *, api_base="", token=None,
            ):
                return True, True

        return _FakeProvider()

    def _patch_live_permission(self, monkeypatch, viewer_permission: str, **kw):
        fake = self._fake_permission_provider(viewer_permission, **kw)
        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: fake,
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.account_token_for_slug",
            lambda *a, **k: None,
        )
        return fake

    def test_live_write_permission_resolves_to_roles_write_fork(
        self, pr_repo, monkeypatch,
    ):
        """A caller whose live GitHub permission reads 'write' gets the
        `pr.roles.write` override applied -- including its `fork.enabled` --
        purely from the live permission read, with no `pr.fork.enabled` set
        anywhere on the base config."""
        config, wid, wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
        )
        self._patch_live_permission(monkeypatch, "write")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is False
        assert res["needs_confirmation"] == "fork_setup"
        assert res["repo"] == "acme/ext"
        # Nothing mutated: the base config never set pr.fork.enabled itself.
        assert not git_ops.has_remote("fork", cwd=str(wt_path))

    def test_live_maintain_permission_keeps_direct_push(
        self, pr_repo, monkeypatch,
    ):
        """A caller whose live permission reads 'maintain' resolves to the
        `pr.roles.maintain` override (fork disabled, direct push unchanged) --
        proceeds without any fork confirmation."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
            maintain=cfg.PRRoleOverride(
                merge_actor="submitter-direct",
                fork=cfg.ForkConfig(enabled=False),
            ),
        )
        self._patch_live_permission(monkeypatch, "maintain")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is True, res
        assert "needs_confirmation" not in res
        assert res["remote"] == "origin"

    def test_maintain_auto_open_uses_effective_self_merge_flow(
        self, pr_repo, monkeypatch,
    ):
        """Auto-open must use the already-resolved Maintain flow for its live
        bypass note, not reclassify the conservative base config."""
        import dataclasses

        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            maintain=cfg.PRRoleOverride(
                merge_actor="submitter-direct",
            ),
        )
        repo = config.repos["ext"]
        config = dataclasses.replace(
            config,
            repos={
                "ext": dataclasses.replace(
                    repo,
                    pr=dataclasses.replace(repo.pr, auto_open=True),
                ),
            },
        )
        fake = self._patch_live_permission(monkeypatch, "maintain")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["pr_opened"] is True
        assert "Maintainer bypass" in res["self_merge_note"]
        assert fake.policy_calls == 1

    def test_live_permission_with_no_matching_role_is_unaffected(
        self, pr_repo, monkeypatch,
    ):
        """A live permission that resolves to a role absent from `pr.roles`
        (e.g. 'read') leaves the base PRConfig untouched -- no fork, no
        confirmation gate -- exactly like an unconfigured repo."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
        )
        self._patch_live_permission(monkeypatch, "read")

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is True, res
        assert "needs_confirmation" not in res

    def test_unresolvable_live_permission_falls_back_to_base_config(
        self, pr_repo, monkeypatch,
    ):
        """A provider read that can't determine `viewer_permission`
        (`supported=False`) must fail OPEN to the base, non-role-scoped
        config -- never treated as a denial or as any particular role."""
        config, wid, _wt_path, _remote_dir = pr_repo
        config = self._roles_config(
            config,
            write=cfg.PRRoleOverride(
                merge_actor="", fork=cfg.ForkConfig(enabled=True),
            ),
        )
        self._patch_live_permission(monkeypatch, "", supported=False)

        res = pr_ops.create_pr(wid, config, target_repo="acme/ext")

        assert res["success"] is True, res
        assert "needs_confirmation" not in res
