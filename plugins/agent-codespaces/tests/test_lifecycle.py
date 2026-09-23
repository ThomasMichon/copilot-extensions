"""Tests for CodeSpace lifecycle management."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from agent_codespaces import lifecycle
from agent_codespaces.lifecycle import (
    CodespaceInfo,
    cleanup_stale,
    create_codespace,
    delete_codespace,
    list_codespaces,
    list_devcontainers,
    resolve_devcontainer_path,
    stop_codespace,
)
from agent_codespaces.config import CodespacesConfig, RepoConfig


@pytest.fixture(autouse=True)
def _isolate_account_binding(monkeypatch):
    """Keep lifecycle tests off real account maps/binding state by default."""
    monkeypatch.setattr("agent_codespaces.gh_account.account_for_repo", lambda repo: None)
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: None,
    )
    monkeypatch.setattr("agent_codespaces.account_binding.bound_accounts", lambda: ())
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bind", lambda *args, **kwargs: None,
    )
    monkeypatch.setattr("agent_codespaces.account_binding.unbind", lambda name: False)


class TestListCodespaces:
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_parses_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=json.dumps([{
                "name": "fluffy-parakeet-abc",
                "displayName": "My CS",
                "repository": "org/repo",
                "gitStatus": {"ref": "main"},
                "state": "Available",
                "machine": "largePremiumLinux",
            }]),
        )
        result = list_codespaces()
        assert len(result) == 1
        assert result[0].name == "fluffy-parakeet-abc"
        assert result[0].branch == "main"
        assert result[0].state == "Available"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_empty_list(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="[]")
        result = list_codespaces()
        assert result == []

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_gh_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="auth failed")
        with pytest.raises(RuntimeError, match="auth failed"):
            list_codespaces()

    def test_includes_bound_accounts_not_in_mapped_set(self, monkeypatch):
        seen = []

        def fake_under(login):
            seen.append(login)
            return []

        monkeypatch.setattr("agent_codespaces.gh_account.mapped_accounts", lambda: ())
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ("acct-x",),
        )
        monkeypatch.setattr(lifecycle, "_list_codespaces_under", fake_under)

        assert lifecycle.list_codespaces() == []
        assert seen == ["acct-x", None]


class TestCreateCodespace:
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_uses_config_defaults(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="new-codespace-name\n"
        )
        config = CodespacesConfig(
            default_machine_type="bigMachine",
            default_location="WestUs2",
        )
        result = create_codespace("org/repo", config)
        assert result.name == "new-codespace-name"

        call_args = mock_run.call_args[0][0]
        assert "--machine" in call_args
        idx = call_args.index("--machine")
        assert call_args[idx + 1] == "bigMachine"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_per_repo_overrides(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="cs-name\n"
        )
        config = CodespacesConfig(
            default_machine_type="small",
            repos={"org/repo": RepoConfig(machine_type="huge")},
        )
        create_codespace("org/repo", config)

        call_args = mock_run.call_args[0][0]
        idx = call_args.index("--machine")
        assert call_args[idx + 1] == "huge"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_no_dotfiles_flag_and_default_permissions(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="cs-name\n")
        config = CodespacesConfig(dotfiles_repo="owner/dotfiles")
        create_codespace("org/repo", config, display_name="my-cs")

        call_args = mock_run.call_args[0][0]
        # gh codespace create has no --dotfiles flag
        assert "--dotfiles" not in call_args
        assert "--default-permissions" in call_args
        idx = call_args.index("--display-name")
        assert call_args[idx + 1] == "my-cs"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_persists_binding_and_sets_account(self, mock_run, monkeypatch):
        mock_run.return_value = MagicMock(returncode=0, stdout="cs-name\n")
        bind = MagicMock()
        monkeypatch.setattr(
            "agent_codespaces.gh_account.account_for_repo", lambda repo: "acct-a",
        )
        monkeypatch.setattr(
            "agent_codespaces.gh_account.env_for_account",
            lambda account, base=None: {},
        )
        monkeypatch.setattr("agent_codespaces.account_binding.bind", bind)

        info = create_codespace("owner/repo", CodespacesConfig())

        assert info.account == "acct-a"
        bind.assert_called_once_with("cs-name", "acct-a", "owner/repo")

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_create_does_not_bind_without_account(self, mock_run, monkeypatch):
        mock_run.return_value = MagicMock(returncode=0, stdout="cs-name\n")
        bind = MagicMock()
        monkeypatch.setattr(
            "agent_codespaces.gh_account.account_for_repo", lambda repo: None,
        )
        monkeypatch.setattr("agent_codespaces.account_binding.bind", bind)

        info = create_codespace("owner/repo", CodespacesConfig())

        assert info.account == ""
        bind.assert_not_called()

    @patch("agent_codespaces.lifecycle.resolve_devcontainer_path", return_value=None)
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_rest_fallback_on_no_terminal_prompt(self, mock_run, _mock_dc):
        # First call: gh codespace create aborts on the headless billing prompt.
        # Second call: the REST-API fallback succeeds and returns the name.
        mock_run.side_effect = [
            MagicMock(
                returncode=1, stdout="",
                stderr="failed to prompt: no terminal",
            ),
            MagicMock(returncode=0, stdout="rest-created-cs\n", stderr=""),
        ]
        info = create_codespace(
            "org/repo",
            CodespacesConfig(default_machine_type="m", default_location="EastUs"),
            display_name="my-cs",
        )
        assert info.name == "rest-created-cs"
        assert mock_run.call_count == 2
        rest_args = mock_run.call_args_list[1][0][0]
        assert rest_args[:4] == ["gh", "api", "--method", "POST"]
        assert "repos/org/repo/codespaces" in rest_args
        assert "multi_repo_permissions_opt_out=true" in rest_args
        assert "display_name=my-cs" in rest_args

    @patch("agent_codespaces.lifecycle.resolve_devcontainer_path", return_value=None)
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_no_rest_fallback_on_other_failure(self, mock_run, _mock_dc):
        # A non-prompt failure must still hard-fail (no REST retry).
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="HTTP 404: repo not found",
        )
        with pytest.raises(RuntimeError, match="gh codespace create failed"):
            create_codespace("org/repo", CodespacesConfig())
        assert mock_run.call_count == 1

    @patch("agent_codespaces.lifecycle.resolve_devcontainer_path", return_value=None)
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_rest_fallback_failure_raises(self, mock_run, _mock_dc):
        mock_run.side_effect = [
            MagicMock(returncode=1, stdout="", stderr="failed to prompt"),
            MagicMock(returncode=1, stdout="", stderr="HTTP 403: forbidden"),
        ]
        with pytest.raises(RuntimeError, match="REST API fallback also failed"):
            create_codespace("org/repo", CodespacesConfig())

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_delete_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        delete_codespace("my-cs")  # should not raise

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_delete_force(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        delete_codespace("my-cs", force=True)
        call_args = mock_run.call_args[0][0]
        assert "--force" in call_args

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_delete_unbinds_on_success(self, mock_run, monkeypatch):
        mock_run.return_value = MagicMock(returncode=0)
        unbind = MagicMock()
        monkeypatch.setattr(
            "agent_codespaces.lifecycle.account_for_codespace", lambda name: "acct-a",
        )
        monkeypatch.setattr(
            "agent_codespaces.gh_account.env_for_account",
            lambda account, base=None: {},
        )
        monkeypatch.setattr("agent_codespaces.account_binding.unbind", unbind)

        delete_codespace("my-cs")

        unbind.assert_called_once_with("my-cs")


class TestAccountForCodespace:
    def test_returns_bound_account_without_listing(self, monkeypatch):
        listing = MagicMock(side_effect=AssertionError("should not list"))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_account", lambda name: "acct-a",
        )
        monkeypatch.setattr(lifecycle, "list_codespaces", listing)

        assert lifecycle.account_for_codespace("cs-one") == "acct-a"
        listing.assert_not_called()

    def test_backfills_from_listing(self, monkeypatch):
        bind = MagicMock()
        monkeypatch.setattr("agent_codespaces.account_binding.bound_account", lambda name: None)
        monkeypatch.setattr("agent_codespaces.account_binding.bind", bind)
        monkeypatch.setattr(
            lifecycle,
            "list_codespaces",
            lambda: [
                CodespaceInfo(
                    name="cs-one",
                    display_name="cs-one",
                    repository="owner/repo",
                    branch="main",
                    state="Available",
                    machine="large",
                    account="acct-a",
                ),
            ],
        )

        assert lifecycle.account_for_codespace("cs-one") == "acct-a"
        bind.assert_called_once_with("cs-one", "acct-a", "owner/repo")


class TestGetCodespaceStatus:
    """Strict, targeted single-CodeSpace lookup (claim-provider-pattern
    effort) -- unlike ``list_codespaces()``, unambiguous about existence and
    never silently drops a live CodeSpace to an incomplete listing."""

    @pytest.fixture(autouse=True)
    def _mint_token_by_default(self, monkeypatch):
        """An explicit-account lookup now requires a genuine minted token
        (claim-provider-pattern effort review finding: "Require
        account-specific authentication for strict lookups") -- default to
        a successful mint so tests not exercising THAT behavior specifically
        don't all need to mock it."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.token_for_account", lambda login: "fake-token"
        )

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_exists(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout=json.dumps({"state": "Available"}), stderr="",
        )
        exists, state = lifecycle.get_codespace_status("cs-a", account="acct-a")
        assert exists is True and state == "Available"

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_confirmed_absent(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="gh: Not Found (HTTP 404)",
        )
        exists, state = lifecycle.get_codespace_status("cs-missing", account="acct-a")
        assert exists is False and state is None

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_backend_error_raises_not_false_absence(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="HTTP 503: service unavailable",
        )
        with pytest.raises(RuntimeError, match="503"):
            lifecycle.get_codespace_status("cs-a", account="acct-a")

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_malformed_json_raises(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="not json", stderr="")
        with pytest.raises(RuntimeError, match="invalid JSON"):
            lifecycle.get_codespace_status("cs-a", account="acct-a")

    @patch("agent_codespaces.lifecycle.subprocess.run", side_effect=FileNotFoundError)
    def test_gh_missing_raises(self, mock_run):
        with pytest.raises(RuntimeError, match="gh CLI not found"):
            lifecycle.get_codespace_status("cs-a", account="acct-a")

    def test_failed_token_mint_raises_never_falls_back_to_ambient(self, monkeypatch):
        """A named account that CANNOT mint a token must fail closed --
        never silently query (and later reclaim!) under whatever account
        happens to be ambient, misreporting that as a confirmed result for
        the named candidate."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.token_for_account", lambda login: None)
        with pytest.raises(RuntimeError, match="could not mint"):
            lifecycle.get_codespace_status("cs-a", account="acct-a")

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_ambiguous_not_found_text_is_not_confirmed_absence(self, mock_run):
        """Only an explicit HTTP 404 confirms absence -- a non-404 error
        that merely mentions "not found" in its own message text (e.g. a
        malformed-request or auth error) must NOT be treated the same."""
        mock_run.return_value = MagicMock(
            returncode=1, stdout="", stderr="422: Validation failed: repository not found",
        )
        with pytest.raises(RuntimeError, match="422"):
            lifecycle.get_codespace_status("cs-a", account="acct-a")

    def test_default_account_tries_every_candidate_before_absent(self, monkeypatch):
        """Without an explicit account, a live CodeSpace under a
        non-ambient candidate account must still be found -- never
        misreported absent because only the FIRST/ambient account was
        tried (the exact gap `account_for_codespace`'s own single
        best-effort guess has)."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.mapped_accounts", lambda: ("acct-a", "acct-b"))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ())

        def fake_under(name, account, **_kwargs):
            if account == "acct-b":
                return True, "Available"
            raise RuntimeError(f"gh api codespace lookup for {name} failed: HTTP 404: Not Found")

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", fake_under)
        exists, state = lifecycle.get_codespace_status("cs-a")
        assert exists is True and state == "Available"

    def test_default_account_confirms_absence_only_when_every_candidate_404s(self, monkeypatch):
        monkeypatch.setattr(
            "agent_codespaces.gh_account.mapped_accounts", lambda: ("acct-a",))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ())
        monkeypatch.setattr(
            lifecycle, "_get_codespace_status_under",
            lambda name, account, **_kwargs: (False, None))
        exists, state = lifecycle.get_codespace_status("cs-missing")
        assert exists is False and state is None

    def test_exact_binding_tried_before_the_generic_candidate_scan(self, monkeypatch):
        """Two DIFFERENT GitHub accounts can each have a CodeSpace with the
        SAME name -- the exact per-name binding (authoritative, mirroring
        account_for_codespace's own precedence) must be tried FIRST, not
        merely as one candidate among the generic mapped/bound-accounts
        scan, or a reclaim could confirm/delete the WRONG account's
        same-named CodeSpace (claim-provider-pattern effort review
        finding: "Resolve exact CodeSpace binding before scanning
        candidate accounts")."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.mapped_accounts", lambda: ("acct-wrong",))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ())
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_account",
            lambda name: "acct-exact" if name == "cs-dup" else None)
        seen_accounts = []

        def fake_under(name, account, **_kwargs):
            seen_accounts.append(account)
            if account == "acct-exact":
                return True, "Available"
            return True, "Available"  # the WRONG account also "has" cs-dup

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", fake_under)
        exists, state, resolved = lifecycle.get_codespace_status_with_account("cs-dup")
        assert exists is True and resolved == "acct-exact"
        assert seen_accounts == ["acct-exact"]  # never even reached the generic scan

    def test_confirmed_404_under_binding_never_scans_other_accounts(self, monkeypatch):
        """A CONFIRMED 404 under the authoritative binding must be final --
        never fall through to the generic scan afterward, which could
        misreport (and let claim-reclaim delete!) a DIFFERENT account's
        CodeSpace that happens to share this exact name (claim-provider-
        pattern effort review finding: "Do not scan other accounts after a
        bound lookup returns 404")."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.mapped_accounts", lambda: ("acct-other",))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ())
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_account",
            lambda name: "acct-exact" if name == "cs-dup" else None)
        seen_accounts = []

        def fake_under(name, account, **_kwargs):
            seen_accounts.append(account)
            if account == "acct-exact":
                return False, None  # confirmed 404 under the binding
            return True, "Available"  # a DIFFERENT account's same-named box

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", fake_under)
        exists, state, resolved = lifecycle.get_codespace_status_with_account("cs-dup")
        assert exists is False and resolved is None
        assert seen_accounts == ["acct-exact"]  # never scanned acct-other

    def test_binding_lookup_error_propagates_never_scans_other_accounts(self, monkeypatch):
        """An AMBIGUOUS lookup failure (token mint/auth/5xx) under the
        authoritative binding must propagate, not silently fall through to
        the generic scan -- for this destructive-reclaim-feeding path, a
        binding that was never actually verified must never let a
        DIFFERENT account's same-named CodeSpace be reported/reclaimed
        instead (claim-provider-pattern effort review finding: "Binding
        lookup errors incorrectly fall through to other accounts")."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.mapped_accounts", lambda: ("acct-other",))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ())
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_account",
            lambda name: "acct-exact" if name == "cs-dup" else None)
        seen_accounts = []

        def fake_under(name, account, **_kwargs):
            seen_accounts.append(account)
            if account == "acct-exact":
                raise RuntimeError("HTTP 503: service unavailable")
            return True, "Available"  # a DIFFERENT account's same-named box

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", fake_under)
        with pytest.raises(RuntimeError, match="503"):
            lifecycle.get_codespace_status_with_account("cs-dup")
        assert seen_accounts == ["acct-exact"]  # never scanned acct-other

    def test_default_account_raises_when_no_candidate_confirms_and_one_errors(self, monkeypatch):
        """A live CodeSpace in an account whose lookup failed for a REAL
        reason (not a 404) must never be reported absent just because
        every OTHER candidate happened to 404."""
        monkeypatch.setattr(
            "agent_codespaces.gh_account.mapped_accounts", lambda: ("acct-a", "acct-b"))
        monkeypatch.setattr(
            "agent_codespaces.account_binding.bound_accounts", lambda: ())

        def fake_under(name, account, **_kwargs):
            if account == "acct-a":
                raise RuntimeError("HTTP 503: service unavailable")
            return False, None

        monkeypatch.setattr(lifecycle, "_get_codespace_status_under", fake_under)
        with pytest.raises(RuntimeError, match="503"):
            lifecycle.get_codespace_status("cs-a")


class TestCleanupStale:
    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_removes_stale_ssh_configs(self, mock_list, tmp_path):
        """SSH configs for deleted codespaces are removed."""
        mock_list.return_value = [
            CodespaceInfo(
                name="live-cs-abc",
                display_name="live",
                repository="org/repo",
                branch="main",
                state="Available",
                machine="large",
            ),
        ]

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir()
        live_config = ssh_dir / "live-cs-abc.config"
        live_config.write_text("Host live")
        stale_config = ssh_dir / "deleted-cs-xyz.config"
        stale_config.write_text("Host stale")

        with patch("agent_codespaces.lifecycle.RUNTIME_DIR", tmp_path):
                result = cleanup_stale()

        assert len(result["ssh_configs"]) == 1
        assert "deleted-cs-xyz" in result["ssh_configs"][0]
        assert not stale_config.exists()
        assert live_config.exists()

    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_dry_run_does_not_remove(self, mock_list, tmp_path):
        """Dry run reports but does not delete."""
        mock_list.return_value = []

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir()
        stale_config = ssh_dir / "old-cs.config"
        stale_config.write_text("Host old")

        with patch("agent_codespaces.lifecycle.RUNTIME_DIR", tmp_path):
                result = cleanup_stale(dry_run=True)

        assert len(result["ssh_configs"]) == 1
        assert stale_config.exists()  # Not removed

    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_no_stale_state(self, mock_list, tmp_path):
        """Clean state returns empty results."""
        mock_list.return_value = [
            CodespaceInfo(
                name="my-cs",
                display_name="my",
                repository="org/repo",
                branch="main",
                state="Available",
                machine="large",
            ),
        ]

        ssh_dir = tmp_path / "ssh"
        ssh_dir.mkdir()
        (ssh_dir / "my-cs.config").write_text("Host mine")

        with patch("agent_codespaces.lifecycle.RUNTIME_DIR", tmp_path):
                result = cleanup_stale()

        assert result["ssh_configs"] == []
        assert result["sockets"] == []

    @patch("agent_codespaces.lifecycle.list_codespaces")
    def test_handles_list_failure_gracefully(self, mock_list):
        """If gh codespace list fails, cleanup skips without error."""
        mock_list.side_effect = RuntimeError("auth expired")
        result = cleanup_stale()
        assert result["ssh_configs"] == []
        assert result["sockets"] == []


class TestListDevcontainers:
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_parses_paths(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout=".devcontainer/devcontainer.json\n"
                   ".devcontainer/docker/devcontainer.json\n",
        )
        assert list_devcontainers("org/repo") == [
            ".devcontainer/devcontainer.json",
            ".devcontainer/docker/devcontainer.json",
        ]

    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_api_failure_degrades_to_empty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr="not found")
        assert list_devcontainers("org/repo") == []

    @patch("agent_codespaces.lifecycle.subprocess.run", side_effect=FileNotFoundError)
    def test_missing_gh_degrades_to_empty(self, mock_run):
        assert list_devcontainers("org/repo") == []


class TestResolveDevcontainerPath:
    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_single_devcontainer_returns_none(self, mock_list):
        mock_list.return_value = [".devcontainer/devcontainer.json"]
        assert resolve_devcontainer_path("org/repo", CodespacesConfig()) is None

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_zero_devcontainers_returns_none(self, mock_list):
        mock_list.return_value = []
        assert resolve_devcontainer_path("org/repo", CodespacesConfig()) is None

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_explicit_override_wins_without_enumeration(self, mock_list):
        assert resolve_devcontainer_path(
            "org/repo", CodespacesConfig(), override=".devcontainer/docker/devcontainer.json",
        ) == ".devcontainer/docker/devcontainer.json"
        mock_list.assert_not_called()

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_multiple_uses_repo_config(self, mock_list):
        mock_list.return_value = [
            ".devcontainer/devcontainer.json",
            ".devcontainer/docker/devcontainer.json",
        ]
        config = CodespacesConfig(
            repos={"org/repo": RepoConfig(
                devcontainer_path=".devcontainer/docker/devcontainer.json"
            )},
        )
        assert resolve_devcontainer_path("org/repo", config) == \
            ".devcontainer/docker/devcontainer.json"

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_multiple_falls_back_to_canonical(self, mock_list):
        mock_list.return_value = [
            ".devcontainer/docker/devcontainer.json",
            ".devcontainer/devcontainer.json",
        ]
        assert resolve_devcontainer_path("org/repo", CodespacesConfig()) == \
            ".devcontainer/devcontainer.json"

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_multiple_no_canonical_uses_first(self, mock_list):
        mock_list.return_value = [
            ".devcontainer/alpha/devcontainer.json",
            ".devcontainer/beta/devcontainer.json",
        ]
        assert resolve_devcontainer_path("org/repo", CodespacesConfig()) == \
            ".devcontainer/alpha/devcontainer.json"

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    def test_global_default_used_when_present(self, mock_list):
        mock_list.return_value = [
            ".devcontainer/alpha/devcontainer.json",
            ".devcontainer/custom/devcontainer.json",
        ]
        config = CodespacesConfig(
            default_devcontainer_path=".devcontainer/custom/devcontainer.json",
        )
        assert resolve_devcontainer_path("org/repo", config) == \
            ".devcontainer/custom/devcontainer.json"


class TestCreateCodespaceDevcontainer:
    @patch("agent_codespaces.lifecycle.list_devcontainers")
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_passes_devcontainer_path_when_multiple(self, mock_run, mock_list):
        mock_run.return_value = MagicMock(returncode=0, stdout="cs-name\n")
        mock_list.return_value = [
            ".devcontainer/devcontainer.json",
            ".devcontainer/docker/devcontainer.json",
        ]
        create_codespace("org/repo", CodespacesConfig())
        call_args = mock_run.call_args[0][0]
        assert "--devcontainer-path" in call_args
        idx = call_args.index("--devcontainer-path")
        assert call_args[idx + 1] == ".devcontainer/devcontainer.json"

    @patch("agent_codespaces.lifecycle.list_devcontainers")
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_omits_flag_when_single(self, mock_run, mock_list):
        mock_run.return_value = MagicMock(returncode=0, stdout="cs-name\n")
        mock_list.return_value = [".devcontainer/devcontainer.json"]
        create_codespace("org/repo", CodespacesConfig())
        call_args = mock_run.call_args[0][0]
        assert "--devcontainer-path" not in call_args


def _cs(name, state):
    return CodespaceInfo(
        name=name, display_name=name, repository="org/repo",
        branch="main", state=state, machine="large",
    )


class TestStopCodespace:
    @patch("agent_codespaces.lifecycle.list_codespaces")
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_stops_available(self, mock_run, mock_list):
        mock_list.return_value = [_cs("cs-1", "Available")]
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        assert stop_codespace("cs-1") is True
        call_args = mock_run.call_args[0][0]
        assert call_args[:4] == ["gh", "codespace", "stop", "-c"]
        assert call_args[4] == "cs-1"

    @patch("agent_codespaces.lifecycle.list_codespaces")
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_already_shutdown_is_noop(self, mock_run, mock_list):
        mock_list.return_value = [_cs("cs-1", "Shutdown")]
        assert stop_codespace("cs-1") is False
        mock_run.assert_not_called()

    @patch("agent_codespaces.lifecycle.list_codespaces")
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_tolerates_already_stopped_race(self, mock_run, mock_list):
        mock_list.return_value = [_cs("cs-1", "Available")]
        mock_run.return_value = MagicMock(
            returncode=1, stderr="codespace is not running",
        )
        assert stop_codespace("cs-1") is False

    @patch("agent_codespaces.lifecycle.list_codespaces")
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_raises_on_real_failure(self, mock_run, mock_list):
        mock_list.return_value = [_cs("cs-1", "Available")]
        mock_run.return_value = MagicMock(returncode=1, stderr="boom")
        with pytest.raises(RuntimeError, match="boom"):
            stop_codespace("cs-1")

    @patch("agent_codespaces.lifecycle.list_codespaces", side_effect=RuntimeError("auth"))
    @patch("agent_codespaces.lifecycle.subprocess.run")
    def test_list_failure_falls_through_to_gh(self, mock_run, mock_list):
        mock_run.return_value = MagicMock(returncode=0, stderr="")
        assert stop_codespace("cs-1") is True
