"""Tests for fork_pr.py -- the durable (repo, account) fork-confirmation
registry (forks.yaml) plus the identity/credential/authority resolution
behind pr.fork's confirmation gate. See test_pr_ops.py's
TestCreatePRForkFlow for the integration-level tests exercising the full
create_pr flow through this module.
"""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import config as cfg
from agent_worktrees import fork_pr


def test_empty_registry_when_missing():
    assert fork_pr.list_forks() == []
    assert fork_pr.find_fork("octo-org/widgets") is None
    assert fork_pr.is_confirmed("octo-org/widgets") is False


def test_write_registry_is_atomic_no_temp_file_left_behind():
    """write_registry must leave the registry file whole (never a partial
    write visible to a lock-free reader) and never leak its temp file."""
    fork_pr.record_confirmation("owner/repo", "alice")
    path = fork_pr._forks_yaml_path()
    siblings = list(path.parent.iterdir())
    assert path in siblings
    assert not any(p.name.startswith(f"{path.name}.tmp-") for p in siblings)
    # A second write (e.g. a confirmation refresh) must also land atomically.
    fork_pr.record_confirmation("owner/repo2", "bob")
    names = {p.name for p in path.parent.iterdir()}
    assert names == {path.name, f"{path.name}.lock"}


def test_quote_round_trips_yaml_reserved_scalars_and_multiline_notes():
    """A value that LOOKS like a YAML bool/null/number, or a multiline note,
    must never corrupt the hand-written file or change type on read-back --
    the exact two classes of value a conditional quoter can mis-handle."""
    fork_pr.record_confirmation(
        "owner/repo", "yes", account="null", notes="line one\nline two\ttabbed",
    )
    e = fork_pr.find_fork("owner/repo", "null")
    assert e.owner == "yes"
    assert e.account == "null"
    assert e.notes == "line one\nline two\ttabbed"
    # The file itself must still be exactly one 'forks:' mapping entry -- a
    # raw (unescaped) embedded newline would have split it across more lines.
    raw = fork_pr._forks_yaml_path().read_text(encoding="utf-8")
    assert raw.count("owner/repo") == 1


def test_record_and_read_round_trip():
    fork_pr.record_confirmation(
        "octo-org/widgets", "octocat", remote="fork",
    )
    e = fork_pr.find_fork("octo-org/widgets")
    assert e is not None
    assert e.owner == "octocat"
    assert e.remote == "fork"
    assert e.confirmed_at  # non-empty timestamp was stamped
    assert fork_pr.is_confirmed("octo-org/widgets") is True


def test_find_fork_case_insensitive():
    fork_pr.record_confirmation("Octo-Org/Widgets", "octocat")
    assert fork_pr.is_confirmed("octo-org/widgets") is True
    assert fork_pr.find_fork("OCTO-ORG/widgets") is not None


def test_record_confirmation_is_idempotent_and_refreshes():
    first = fork_pr.record_confirmation("owner/repo", "alice")
    second = fork_pr.record_confirmation("owner/repo", "alice")
    assert fork_pr.list_forks() == [second]
    # Owner can change on a later confirmation (e.g. re-pointed at a
    # differently-owned fork) without leaving a stale duplicate entry.
    third = fork_pr.record_confirmation("owner/repo", "bob")
    assert third.owner == "bob"
    assert len(fork_pr.list_forks()) == 1
    assert first  # silence unused-var lint; documents the first call's shape


def test_record_confirmation_preserves_notes_unless_overridden():
    fork_pr.record_confirmation("owner/repo", "alice", notes="pre-seeded")
    refreshed = fork_pr.record_confirmation("owner/repo", "alice")
    assert refreshed.notes == "pre-seeded"
    overridden = fork_pr.record_confirmation(
        "owner/repo", "alice", notes="updated",
    )
    assert overridden.notes == "updated"


def test_is_confirmed_scoped_to_account():
    """A confirmation recorded under one account must not silently cover a
    later call resolved to a DIFFERENT account -- the repo's account mapping
    changing since confirmation is exactly the case this scoping exists to
    catch (a stale repo-only confirmation must not authorize forking/pushing
    under a newly-mapped identity without renewed consent)."""
    fork_pr.record_confirmation("owner/repo", "alice", account="account-a")
    assert fork_pr.is_confirmed("owner/repo", account="account-a") is True
    assert fork_pr.is_confirmed("owner/repo", account="account-b") is False
    assert fork_pr.is_confirmed("owner/repo") is False  # default account=""


def test_record_confirmation_changed_account_adds_separate_entry():
    """Confirming under a DIFFERENT account must NOT overwrite an existing
    confirmation for another account on the same repo -- an operator
    switching which identity publishes a repo's PRs, then switching back,
    must not have to re-confirm the one that was never actually revoked."""
    fork_pr.record_confirmation("owner/repo", "alice", account="account-a")
    fork_pr.record_confirmation("owner/repo", "bob", account="account-b")
    assert fork_pr.is_confirmed("owner/repo", account="account-a") is True
    assert fork_pr.is_confirmed("owner/repo", account="account-b") is True
    a = fork_pr.find_fork("owner/repo", "account-a")
    b = fork_pr.find_fork("owner/repo", "account-b")
    assert a.owner == "alice"
    assert b.owner == "bob"
    assert {e.account for e in fork_pr.find_forks_for_repo("owner/repo")} == {
        "account-a", "account-b",
    }


def test_record_confirmation_is_locked(monkeypatch):
    """record_confirmation/remove_fork serialize via an interprocess lock
    file next to the registry, not just an in-process read-modify-write."""
    acquired = []

    real_locked = fork_pr._locked_registry_file

    from contextlib import contextmanager

    @contextmanager
    def _tracking_locked():
        acquired.append(True)
        with real_locked():
            yield

    monkeypatch.setattr(fork_pr, "_locked_registry_file", _tracking_locked)
    fork_pr.record_confirmation("owner/repo", "alice")
    fork_pr.remove_fork("owner/repo")
    assert acquired == [True, True]
    assert fork_pr._forks_yaml_path().with_suffix(".yaml.lock").exists()


def _mp_worker_record(repo: str, owner: str, agent_home: str) -> None:
    """Module-level (picklable) worker: record one confirmation.

    Sets ``AGENT_HOME`` for *this* (spawned) process explicitly, since a
    multiprocessing worker does not inherit the parent test process's
    monkeypatches/env mutations -- only a fresh interpreter importing this
    module from scratch (``registry_paths.registry_path`` resolves relative
    to ``AGENT_HOME`` when set, matching this suite's own autouse HOME
    isolation fixture).
    """
    import os

    os.environ["AGENT_HOME"] = agent_home
    from agent_worktrees import fork_pr as _fp
    _fp.record_confirmation(repo, owner)


def test_record_confirmation_locking_survives_concurrent_processes(tmp_path: Path):
    """A real cross-process race: N processes concurrently confirming
    DIFFERENT repos against the SAME registry file must not lose any
    entry to an unsynchronized read-modify-write -- the actual failure
    mode the in-process-only tracking test above cannot exercise."""
    import multiprocessing

    agent_home = tmp_path / "mp-home"
    agent_home.mkdir()
    repos = [(f"owner/repo-{i}", f"user-{i}") for i in range(8)]

    ctx = multiprocessing.get_context("spawn")
    procs = [
        ctx.Process(target=_mp_worker_record, args=(repo, owner, str(agent_home)))
        for repo, owner in repos
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=30)
        assert p.exitcode == 0

    import os
    old = os.environ.get("AGENT_HOME")
    os.environ["AGENT_HOME"] = str(agent_home)
    try:
        entries = {e.repo: e.owner for e in fork_pr.list_forks()}
    finally:
        if old is None:
            os.environ.pop("AGENT_HOME", None)
        else:
            os.environ["AGENT_HOME"] = old

    assert entries == {repo: owner for repo, owner in repos}


def test_remove_fork():
    fork_pr.record_confirmation("owner/repo", "alice")
    assert fork_pr.remove_fork("owner/repo") is True
    assert fork_pr.is_confirmed("owner/repo") is False
    assert fork_pr.remove_fork("owner/repo") is False


def test_list_forks_sorted_by_repo():
    fork_pr.record_confirmation("zeta/repo", "alice")
    fork_pr.record_confirmation("alpha/repo", "bob")
    entries = fork_pr.list_forks()
    assert [e.repo for e in entries] == ["alpha/repo", "zeta/repo"]


def test_malformed_registry_file_is_ignored(monkeypatch, tmp_path: Path):
    path = tmp_path / "forks.yaml"
    path.write_text("not: [valid, {structure", encoding="utf-8")
    monkeypatch.setattr(fork_pr, "_forks_yaml_path", lambda: path)
    assert fork_pr.list_forks() == []


def test_entry_missing_owner_is_skipped(monkeypatch, tmp_path: Path):
    path = tmp_path / "forks.yaml"
    path.write_text(
        "forks:\n  owner/repo:\n    '':\n      remote: fork\n", encoding="utf-8",
    )
    monkeypatch.setattr(fork_pr, "_forks_yaml_path", lambda: path)
    assert fork_pr.list_forks() == []


class TestResolveForkCredential:
    """_resolve_fork_credential must reflect the identity that actually
    authenticates (mirroring providers.account_token_for_slug), not the bare
    account mapping -- an unmintable mapping silently falls back to ambient
    auth, and a confirmation scoped to the mapping alone would miss that.
    Returns (token, scope): the SAME token must flow to the real fork
    operation, and scope=="" means "unresolvable -- fail closed"."""

    def _cfg(self, provider="github"):
        import dataclasses
        return dataclasses.replace(cfg.PRConfig(enabled=True), provider=provider)

    def test_non_github_provider_returns_empty(self, monkeypatch):
        assert fork_pr._resolve_fork_credential("o/r", self._cfg("gitea")) == (None, "")

    def test_unmapped_repo_falls_back_to_active_account(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda s: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.active_gh_account", lambda: "whoami",
        )
        assert fork_pr._resolve_fork_credential("o/r", self._cfg()) == (None, "whoami")

    def test_unresolvable_identity_fails_closed_to_empty_scope(self, monkeypatch):
        """Neither a mapped account nor an active gh login -- the identity
        is genuinely unknown, so the scope must be '' (never a value two
        different unresolvable callers could collide on)."""
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda s: None,
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.active_gh_account", lambda: None,
        )
        assert fork_pr._resolve_fork_credential("o/r", self._cfg()) == (None, "")

    def test_mapping_equal_to_active_uses_active(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda s: "Same",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.active_gh_account", lambda: "same",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.gh_token_for_account", lambda a: "should-not-be-called",
        )
        assert fork_pr._resolve_fork_credential("o/r", self._cfg()) == (None, "same")

    def test_mintable_cross_account_mapping_wins(self, monkeypatch):
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda s: "mapped",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.active_gh_account", lambda: "active",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.gh_token_for_account",
            lambda a: "tok" if a == "mapped" else None,
        )
        assert fork_pr._resolve_fork_credential("o/r", self._cfg()) == ("tok", "mapped")

    def test_unmintable_cross_account_mapping_falls_back_to_active(self, monkeypatch):
        """The exact gap this helper exists to close: a mapped account that
        cannot actually be minted silently resolves to ambient auth, so the
        confirmation scope must reflect 'active', not 'mapped'."""
        monkeypatch.setattr(
            "agent_worktrees.repos.account_for_github_slug", lambda s: "mapped",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.active_gh_account", lambda: "active",
        )
        monkeypatch.setattr(
            "agent_worktrees.git_ops.gh_token_for_account", lambda a: None,
        )
        assert fork_pr._resolve_fork_credential("o/r", self._cfg()) == (None, "active")

    def test_explicit_token_binding_takes_priority_and_is_scoped_by_value(self, monkeypatch):
        """pr.token_command/token_env is account_token_for_slug's FIRST
        priority -- a repo using it can authenticate as an identity no
        mapping/ambient lookup would ever reveal, so it must win here too.
        Scoped by the token's own value (not a guessed login) so a
        changed/rotated token safely re-prompts instead of silently trusting
        whichever identity the new token happens to belong to -- and the
        SAME token value is returned for the real fork operation to use."""
        import dataclasses
        config_with_token = dataclasses.replace(
            self._cfg(), token_env="SOME_TOKEN_ENV_VAR_UNSET",
        )
        monkeypatch.setattr(
            "agent_worktrees.providers.base.resolve_token", lambda prcfg: "secret-token-abc",
        )
        first_token, first_scope = fork_pr._resolve_fork_credential("o/r", config_with_token)
        assert first_token == "secret-token-abc"
        assert first_scope.startswith("token:")

        monkeypatch.setattr(
            "agent_worktrees.providers.base.resolve_token", lambda prcfg: "secret-token-xyz",
        )
        _second_token, second_scope = fork_pr._resolve_fork_credential("o/r", config_with_token)
        assert second_scope.startswith("token:")
        assert second_scope != first_scope  # a different token value -> a different scope


class TestResolveLiveForkOwner:
    """_resolve_live_fork_owner must be the NON-mutating half the
    confirmation gate's pre-check relies on -- it must never reach any
    mutating provider call, and must fail soft (None) rather than raise."""

    def _cfg(self, provider="github"):
        import dataclasses
        return dataclasses.replace(cfg.PRConfig(enabled=True), provider=provider)

    def test_non_github_provider_returns_none(self):
        assert fork_pr._resolve_live_fork_owner(self._cfg("gitea"), None) is None

    def test_delegates_to_provider_resolve_fork_owner(self, monkeypatch):
        class _FakeProvider:
            def resolve_fork_owner(self, *, api_base="", token=None):
                assert token == "tok"
                return "live-owner"

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: _FakeProvider(),
        )
        assert fork_pr._resolve_live_fork_owner(self._cfg(), "tok") == "live-owner"

    def test_provider_error_is_swallowed_to_none(self, monkeypatch):
        from agent_worktrees import providers

        class _FailingProvider:
            def resolve_fork_owner(self, *, api_base="", token=None):
                raise providers.ProviderError("boom")

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: _FailingProvider(),
        )
        assert fork_pr._resolve_live_fork_owner(self._cfg(), None) is None


class TestNonDefaultAuthority:
    """pr.fork's durable registry isn't scoped by GitHub authority, so it
    must refuse to operate at all when the EFFECTIVE authority (an explicit
    pr.api_base, else ambient GH_HOST, else github.com -- the SAME
    precedence GitHubProvider.authority_endpoint uses for every other call)
    is non-default -- rather than risk a confirmation recorded under
    github.com silently authorizing a fork/push against an unrelated
    same-named repo on a GitHub Enterprise instance."""

    def _cfg(self, provider="github", api_base=""):
        import dataclasses
        return dataclasses.replace(
            cfg.PRConfig(enabled=True), provider=provider, api_base=api_base,
        )

    def test_non_github_provider_returns_empty(self):
        assert fork_pr._non_default_authority(self._cfg("gitea")) == ""

    def test_default_host_returns_empty(self, monkeypatch):
        monkeypatch.delenv("GH_HOST", raising=False)
        assert fork_pr._non_default_authority(self._cfg()) == ""

    def test_ambient_gh_host_enterprise_is_non_default(self, monkeypatch):
        monkeypatch.setenv("GH_HOST", "github.example.com")
        assert fork_pr._non_default_authority(self._cfg()) == "github.example.com"

    def test_explicit_api_base_enterprise_is_non_default_even_without_gh_host(
        self, monkeypatch,
    ):
        """The exact gap this helper used to miss: pr.api_base alone (no
        GH_HOST at all) must still be caught as a non-default authority."""
        monkeypatch.delenv("GH_HOST", raising=False)
        assert fork_pr._non_default_authority(
            self._cfg(api_base="https://github.example.com/api/v3"),
        ) == "github.example.com"

    def test_explicit_api_base_github_com_overrides_differing_ambient_gh_host(
        self, monkeypatch,
    ):
        """pr.api_base takes priority over ambient GH_HOST (matching
        authority_endpoint's own precedence) -- an explicit github.com
        override must resolve to the default even if GH_HOST differs."""
        monkeypatch.setenv("GH_HOST", "github.example.com")
        assert fork_pr._non_default_authority(self._cfg(api_base="github.com")) == ""

    def test_provider_error_is_swallowed_to_empty(self, monkeypatch):
        from agent_worktrees import providers

        class _FailingProvider:
            def authority_endpoint(self, api_base=""):
                raise providers.ProviderError("boom")

        monkeypatch.setattr(
            "agent_worktrees.providers.get_provider", lambda name: _FailingProvider(),
        )
        assert fork_pr._non_default_authority(self._cfg()) == ""
