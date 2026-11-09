"""Tests for multi-account gh resolution (gh_account) + cross-account listing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import json
import os
import subprocess

import pytest

from agent_codespaces import gh_account, lifecycle


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch):
    gh_account.clear_caches()
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: None,
    )
    monkeypatch.setattr("agent_codespaces.account_binding.bound_accounts_or_raise", lambda: ())
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bind", lambda *args, **kwargs: None,
    )
    yield
    gh_account.clear_caches()


# --- account_for_repo (shells agent-worktrees repos account-for) ------------


def test_account_for_repo_returns_login():
    with patch.object(gh_account, "_agent_worktrees_bin", return_value="aw"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"account_map": {}}'),
            MagicMock(returncode=0, stdout="example-operator\n"),
        ]
        assert gh_account.account_for_repo("example-org/x") == "example-operator"
        args = run.call_args[0][0]
        assert args[:3] == ["aw", "repos", "account-for"]
        assert run.call_count == 2


def test_account_for_repo_none_when_unresolved():
    with patch.object(gh_account, "_agent_worktrees_bin", return_value="aw"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.side_effect = [
            MagicMock(returncode=0, stdout='{"account_map": {}}'),
            MagicMock(returncode=1, stdout=""),
        ]
        assert gh_account.account_for_repo("owner/x") is None


def test_account_for_repo_none_without_agent_worktrees():
    with patch.object(gh_account, "_agent_worktrees_bin", return_value=None):
        assert gh_account.account_for_repo("owner/x") is None


def test_explicit_individual_lookup_cannot_fall_back_on_undecodable_output(monkeypatch):
    monkeypatch.setattr(gh_account, "account_inventory", lambda: gh_account.AccountInventory())
    monkeypatch.setattr(gh_account.worktrees, "explicit_context", lambda: True)
    monkeypatch.setattr(gh_account.worktrees, "validate_context", lambda: None)
    monkeypatch.setattr(
        gh_account.worktrees, "run",
        MagicMock(side_effect=UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid output")),
    )
    with pytest.raises(gh_account.worktrees.ContextRefused, match="lookup is unavailable"):
        gh_account.env_for_repo("example/repo")


# --- token + env ------------------------------------------------------------


def test_env_for_account_sets_gh_token():
    with patch.object(gh_account.shutil, "which", return_value="gh"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout="ghs_tok\n")
        env = gh_account.env_for_account(
            "ThomasMichon", base={"GITHUB_TOKEN": "old", "GH_HOST": "github.example.com"},
        )
        assert env["GH_TOKEN"] == "ghs_tok"
        assert env["GH_HOST"] == "github.com"
        assert "GITHUB_TOKEN" not in env  # stale token dropped


def test_env_for_named_account_never_silently_uses_ambient(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "foreign-fixture")
    with patch.object(gh_account.shutil, "which", return_value="gh"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=1, stdout="")
        with pytest.raises(RuntimeError, match="no ambient fallback"):
            gh_account.env_for_account("selected-work", base={"X": "1"})


def test_named_store_lookup_cannot_be_shadowed_by_an_inherited_token(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "foreign-fixture")
    monkeypatch.setenv("GITHUB_TOKEN", "another-foreign-fixture")
    monkeypatch.setenv("GH_DEBUG", "api")
    monkeypatch.setattr(gh_account.shutil, "which", lambda name: "gh")
    monkeypatch.setattr("agent_codespaces.picker_auth.credential_token", lambda login: None)
    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        assert argv[-2:] == ["--user", "selected-work"]
        environment = kwargs["env"]
        assert "GH_TOKEN" not in environment and "GITHUB_TOKEN" not in environment
        assert "GH_DEBUG" not in environment
        assert environment["GH_HOST"] == "github.com"
        return subprocess.CompletedProcess(argv, 0, "selected-store-fixture", "")

    monkeypatch.setattr(gh_account.subprocess, "run", run)
    environment = gh_account.env_for_account("selected-work")
    assert environment["GH_TOKEN"] == "selected-store-fixture"
    assert os.environ["GH_TOKEN"] == "foreign-fixture"
    assert len(calls) == 1


@pytest.mark.parametrize("observed", ["selected-work", "other-user"])
def test_inherited_token_is_reused_only_for_the_verified_selected_account(monkeypatch, observed):
    from agent_codespaces import allocation

    monkeypatch.setenv("GH_TOKEN", "inherited-fixture")
    monkeypatch.setattr(gh_account, "token_for_account", lambda login: None)
    monkeypatch.setattr(
        allocation, "_environment_context",
        lambda token, **kwargs: (observed, {"GH_TOKEN": token}),
    )
    if observed == "selected-work":
        assert gh_account.env_for_account("selected-work")["GH_TOKEN"] == "inherited-fixture"
    else:
        with pytest.raises(RuntimeError, match="no ambient fallback"):
            gh_account.env_for_account("selected-work")


def test_owner_inventory_bypasses_the_process_token_cache(monkeypatch):
    from agent_codespaces import owner_inventory

    monkeypatch.setattr(gh_account.shutil, "which", lambda name: "gh")
    monkeypatch.setattr("agent_codespaces.picker_auth.credential_token", lambda login: None)
    store = {"token": "first-fixture"}
    monkeypatch.setattr(
        gh_account.subprocess, "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, store["token"], ""),
    )
    assert gh_account.token_for_account("selected-work") == "first-fixture"
    store["token"] = "rotated-fixture"
    assert owner_inventory.credential("selected-work") == "rotated-fixture"


def test_env_for_account_none_login():
    env = gh_account.env_for_account(None, base={"X": "1"})
    assert env == {"X": "1"}


@pytest.mark.parametrize("consumer", ["inventory", "verified_token"])
def test_saved_account_credential_lookup_ignores_ambient_enterprise_host(monkeypatch, consumer):
    from agent_codespaces import allocation, owner_inventory

    monkeypatch.setenv("GH_HOST", "github.example.com")
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("agent_codespaces.picker_auth.credential_token", lambda login: None)
    monkeypatch.setattr(gh_account.shutil, "which", lambda name: "gh")
    observed = []

    def run(argv, **kwargs):
        observed.append(argv)
        assert argv[:3] == ["gh", "auth", "token"]
        assert argv[argv.index("--hostname") + 1] == "github.com"
        assert argv[argv.index("--user") + 1] == "selected-owner"
        return MagicMock(returncode=0, stdout="fixture-token")

    monkeypatch.setattr(gh_account.subprocess, "run", run)
    if consumer == "inventory":
        environment = owner_inventory.qualified_environment("selected-owner")
        assert environment["GH_HOST"] == "github.com"
        token = environment["GH_TOKEN"]
    else:
        token = allocation.verified_account_token("selected-owner")
    assert token == "fixture-token"
    assert len(observed) == 1
    assert os.environ["GH_HOST"] == "github.example.com"


def test_mapped_accounts_parses_json():
    payload = '{"account_map": {"github": "ThomasMichon", "example-org": "example-operator"}}'
    with patch.object(gh_account, "_agent_worktrees_bin", return_value="aw"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout=payload)
        assert gh_account.mapped_accounts() == ("ThomasMichon", "example-operator")


def test_mapped_accounts_empty_without_map():
    with patch.object(gh_account, "_agent_worktrees_bin", return_value="aw"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=0, stdout='{"account_map": {}}')
        assert gh_account.mapped_accounts() == ()


def test_bulk_preferred_account_and_repo_exceptions_are_operator_identities(monkeypatch):
    payload = {
        "account_map": {},
        "default_account": "operator-work",
        "resolution_version": 1,
        "unmapped_owner_fallback": False,
        "repository_accounts": {"Publisher/tool": "operator-public"},
        "owner_accounts": {"Publisher": "operator-work"},
    }
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    fallback = MagicMock(side_effect=AssertionError("redundant account lookup"))
    monkeypatch.setattr(gh_account, "_legacy_account_for_repo", fallback)
    inventory = gh_account.account_inventory()
    # Unrelated repo exceptions must not require public-account CodeSpace auth.
    assert inventory.accounts == ("operator-work",)
    assert inventory.account_for("Publisher/tool") == "operator-public"
    assert inventory.account_for("Publisher/other") == "operator-work"
    assert inventory.account_for("UnmappedOrg/product") == "operator-work"
    assert inventory.account_for("AnotherUser/repo") == "operator-work"
    fallback.assert_not_called()


@pytest.mark.parametrize("preferred", [12, [], "", "two accounts", True])
def test_malformed_preferred_account_refuses_inventory(monkeypatch, preferred):
    payload = {
        "account_map": {}, "default_account": preferred, "resolution_version": 1,
        "repository_accounts": {}, "owner_accounts": {},
    }
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="preferred account"):
        gh_account.account_inventory()


@pytest.mark.parametrize("key", ["github.com:*", "GITHUB.COM:*"])
@pytest.mark.parametrize("metadata", [
    {},
    {"resolution_version": 1, "repository_accounts": {}, "owner_accounts": {},
     "unmapped_owner_fallback": True},
    {"resolution_version": 1, "repository_accounts": {}, "owner_accounts": {},
     "unmapped_owner_fallback": False, "default_account": "different-account"},
])
def test_wildcard_policy_refuses_older_or_inconsistent_receivers(monkeypatch, metadata, key):
    payload = {"account_map": {key: "operator-work"}, **metadata}
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="preferred account contract"):
        gh_account.account_inventory()


@pytest.mark.parametrize("consumer", ["allocation", "creation", "environment"])
def test_individual_consumers_refuse_older_preferred_policy_before_credentials(
    monkeypatch, consumer,
):
    from agent_codespaces import allocation, owner_inventory

    calls = []

    def lookup(*args, **kwargs):
        calls.append(args)
        if "--json" in args:
            return subprocess.CompletedProcess(
                [], 0, '{"account_map": {"github.com:*": "operator-work"}}', "",
            )
        return subprocess.CompletedProcess([], 0, "publisher", "")

    monkeypatch.setattr(gh_account, "_lookup", lookup)
    credential = MagicMock(side_effect=AssertionError("credential acquisition"))
    monkeypatch.setattr(gh_account, "token_for_account", credential)
    monkeypatch.setattr(owner_inventory, "qualified_environment", credential)
    backend = MagicMock(side_effect=AssertionError("backend invocation"))
    monkeypatch.setattr(lifecycle.subprocess, "run", backend)
    with pytest.raises(gh_account.AccountInventoryError, match="preferred account contract"):
        if consumer == "allocation":
            allocation.allocation_context("publisher/tool")
        elif consumer == "creation":
            lifecycle.create_codespace("publisher/tool", lifecycle.CodespacesConfig())
        else:
            gh_account.env_for_repo("publisher/tool")
    credential.assert_not_called()
    backend.assert_not_called()
    assert calls == [("repos", "account", "list", "--json", "--with-resolutions")]


def test_individual_resolution_uses_fresh_preferred_policy_and_repo_exception(monkeypatch):
    payload = {
        "account_map": {"github.com:*": "operator-work"},
        "default_account": "operator-work",
        "resolution_version": 1,
        "unmapped_owner_fallback": False,
        "repository_accounts": {"Publisher/tool": "operator-public"},
        "owner_accounts": {},
    }
    calls = []

    def lookup(*args, **kwargs):
        calls.append(args)
        assert "--json" in args
        return subprocess.CompletedProcess([], 0, json.dumps(payload), "")

    monkeypatch.setattr(gh_account, "_lookup", lookup)
    assert gh_account.account_for_repo("Publisher/tool") == "operator-public"
    assert gh_account.account_for_repo("Publisher/other") == "operator-work"
    payload["default_account"] = payload["account_map"]["github.com:*"] = "new-work"
    assert gh_account.account_for_repo("Publisher/other") == "new-work"
    assert len(calls) == 3


def test_older_peer_upgrade_cannot_reuse_a_cached_publisher_login(monkeypatch):
    corrected = False
    individual_reads = []

    def lookup(*args, **kwargs):
        if "--json" in args:
            payload = {"account_map": {}}
            if corrected:
                payload.update(
                    resolution_version=1, repository_accounts={},
                    owner_accounts={}, unmapped_owner_fallback=False,
                )
            return subprocess.CompletedProcess([], 0, json.dumps(payload), "")
        individual_reads.append(args)
        return subprocess.CompletedProcess([], 0, "" if corrected else "publisher", "")

    monkeypatch.setattr(gh_account.worktrees, "explicit_context", lambda: False)
    monkeypatch.setattr(gh_account, "_lookup", lookup)
    assert gh_account.account_for_repo("publisher/tool") == "publisher"
    corrected = True
    assert gh_account.account_for_repo("publisher/tool") is None
    assert len(individual_reads) == 2


def test_bulk_account_inventory_preserves_exact_precedence_without_another_peer_read(monkeypatch):
    payload = {
        "account_map": {"Example": "mapped"},
        "resolution_version": 1,
        "repository_accounts": {"EXAMPLE/one": "one-user", "other": None},
        "owner_accounts": {"EXAMPLE": "sibling-user"},
    }
    lookup = MagicMock(return_value=subprocess.CompletedProcess([], 0, json.dumps(payload), ""))
    monkeypatch.setattr(gh_account, "_lookup", lookup)
    inventory = gh_account.account_inventory()
    assert inventory.accounts == ("mapped",)
    assert inventory.account_for("EXAMPLE/one") == "one-user"
    assert inventory.account_for("example/unregistered") == "sibling-user"
    assert inventory.account_for("other") is None
    assert lookup.call_count == 1


def test_bulk_inventory_retains_alias_case_and_explicit_owner_fallback(monkeypatch):
    inventory = gh_account.AccountInventory(
        repositories={"Alias": "pinned"}, owners={"example": "owner-user"},
        unmapped_owner_fallback=True,
    )
    fallback = MagicMock(side_effect=AssertionError("redundant peer invocation"))
    monkeypatch.setattr(gh_account, "_legacy_account_for_repo", fallback)
    assert inventory.account_for("Alias") == "pinned"
    assert inventory.account_for("alias") == "alias"
    assert inventory.account_for("Example/unregistered") == "owner-user"
    assert inventory.account_for("Unknown/repo") == "Unknown"
    fallback.assert_not_called()


def test_bulk_inventory_falls_back_for_an_old_peer_or_unknown_owner(monkeypatch):
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, '{"account_map": {}}', ""),
    )
    fallback = MagicMock(return_value="resolved")
    monkeypatch.setattr(gh_account, "_legacy_account_for_repo", fallback)
    assert gh_account.account_inventory().account_for("example/repo") == "resolved"
    fallback.assert_called_once_with("example/repo")


def test_malformed_bulk_resolution_is_not_an_empty_success(monkeypatch):
    payload = '{"account_map": {}, "resolution_version": 1, "repository_accounts": []}'
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, payload, ""),
    )
    with pytest.raises(RuntimeError, match="malformed account resolution"):
        gh_account.account_inventory()


@pytest.mark.parametrize("mapping", [[], {"Example": []}, {"Example": ""}])
def test_malformed_snapshot_mapping_is_not_confirmed_empty(monkeypatch, mapping):
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps({"account_map": mapping}), "",
        ),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="malformed account mapping"):
        gh_account.account_inventory()


def test_missing_snapshot_mapping_is_not_confirmed_empty(monkeypatch):
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, "{}", ""),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="malformed account mapping"):
        gh_account.account_inventory()


@pytest.mark.parametrize("returncode", [1, 2, -9])
def test_strict_snapshot_failure_is_not_confirmed_empty(monkeypatch, returncode):
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            [], returncode, '{"error":"unreadable"}', "",
        ),
    )
    with pytest.raises(
        gh_account.AccountInventoryError, match=f"could not read.*exit status {returncode}",
    ):
        gh_account.account_inventory()


@pytest.mark.parametrize(("error", "reason"), [
    (OSError("private startup detail"), "process launch failed"),
    (subprocess.TimeoutExpired("private-command", 10, output="private output"),
     "invocation timed out after 10 seconds"),
    (UnicodeDecodeError("utf-8", b"\xff", 0, 1, "private output"),
     "output decoding failed"),
    (subprocess.SubprocessError("private process detail"), "subprocess failed"),
])
def test_snapshot_process_failure_is_not_confirmed_empty(monkeypatch, error, reason):
    monkeypatch.setattr(gh_account.worktrees, "explicit_context", lambda: False)
    monkeypatch.setattr(gh_account, "_agent_worktrees_bin", lambda: "aw")
    monkeypatch.setattr(
        gh_account.subprocess, "run", MagicMock(side_effect=error),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="unavailable") as caught:
        gh_account.account_inventory()
    assert reason in str(caught.value)
    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is error


def test_snapshot_failed_process_reaches_cli_without_echoing_output(monkeypatch, tmp_path, capsys):
    from agent_codespaces import __main__ as cli, resolver

    peer = tmp_path / ("snapshot-peer.cmd" if os.name == "nt" else "snapshot-peer")
    peer.write_text(
        "@echo off\n"
        "echo private snapshot output\n"
        "echo private snapshot stderr 1>&2\n"
        "exit /b 7\n"
        if os.name == "nt" else
        "#!/bin/sh\n"
        "printf 'private snapshot output\\n'\n"
        "printf 'private snapshot stderr\\n' >&2\n"
        "exit 7\n",
        encoding="utf-8",
    )
    if os.name != "nt":
        peer.chmod(0o700)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(gh_account, "_agent_worktrees_bin", lambda: str(peer))

    async def unavailable(self):
        gh_account.account_inventory()
        pytest.fail("failed snapshot was accepted as an empty inventory")

    monkeypatch.setattr(resolver.CodespaceResolver, "list_specs", unavailable)
    assert cli.main(["namespace-list"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "exit status 7" in captured.err
    assert "private snapshot" not in captured.err
    assert str(peer) not in captured.err


def test_each_discovery_refreshes_bulk_routing_once(monkeypatch):
    from types import SimpleNamespace

    from agent_codespaces import account_candidates, config, picker_auth

    payload = {
        "account_map": {}, "resolution_version": 1, "repository_accounts": {},
        "owner_accounts": {}, "unmapped_owner_fallback": True,
    }
    lookup = MagicMock(
        side_effect=lambda *args, **kwargs: subprocess.CompletedProcess(
            [], 0, json.dumps(payload), "",
        ),
    )
    monkeypatch.setattr(gh_account, "_lookup", lookup)
    monkeypatch.setattr(gh_account, "active_account", lambda: "active")
    monkeypatch.setattr(picker_auth, "authorized_configs", lambda: {})
    monkeypatch.setattr(
        config, "load_merged_config",
        lambda **_: SimpleNamespace(repos={"Alias": {}}),
    )
    assert account_candidates.candidates() == ("Alias", "active")
    payload["repository_accounts"]["Alias"] = "newly-configured"
    assert account_candidates.candidates() == ("newly-configured", "active")
    assert lookup.call_count == 2


def test_conflicting_case_insensitive_resolution_is_not_arbitrarily_selected(monkeypatch):
    payload = {
        "account_map": {}, "resolution_version": 1, "repository_accounts": {},
        "owner_accounts": {"Example": "one", "example": "two"},
    }
    monkeypatch.setattr(
        gh_account, "_lookup",
        lambda *args, **kwargs: subprocess.CompletedProcess([], 0, json.dumps(payload), ""),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="conflicting account resolution"):
        gh_account.account_inventory()


# --- cross-account list merge ----------------------------------------------


def _cs(name, account):
    return lifecycle.CodespaceInfo(
        name=name, display_name=name, repository="o/r", branch="main",
        state="Available", machine="lg", account=account,
    )


def test_list_codespaces_merges_across_accounts():
    def fake_under(login):
        if login == "ThomasMichon":
            return [_cs("cs-a", "ThomasMichon")]
        if login == "example-operator":
            return [_cs("cs-b", "example-operator")]
        return []  # ambient (None)

    with patch.object(lifecycle, "_list_codespaces_under", side_effect=fake_under), \
         patch("agent_codespaces.gh_account.account_inventory",
               return_value=gh_account.AccountInventory(("ThomasMichon", "example-operator"))):
        names = {c.name: c.account for c in lifecycle.list_codespaces()}
    assert names == {"cs-a": "ThomasMichon", "cs-b": "example-operator"}


def test_list_codespaces_ambient_only_without_map():
    with patch.object(lifecycle, "_list_codespaces_under",
                      return_value=[_cs("cs-x", "")]) as under, \
         patch("agent_codespaces.gh_account.account_inventory",
               return_value=gh_account.AccountInventory()):
        result = lifecycle.list_codespaces()
    assert [c.name for c in result] == ["cs-x"]
    under.assert_called_once_with(None)


def test_account_for_codespace_resolves_owner():
    with patch.object(lifecycle, "list_codespaces",
                      return_value=[_cs("cs-a", "ThomasMichon")]):
        assert lifecycle.account_for_codespace("cs-a") == "ThomasMichon"
        assert lifecycle.account_for_codespace("missing") is None


def test_account_for_codespace_swallows_errors():
    with patch.object(lifecycle, "list_codespaces", side_effect=RuntimeError("boom")):
        assert lifecycle.account_for_codespace("cs-a") is None


# --- repository_for_codespace (#5441: targeted, not listing-capped) ---------


def test_repository_for_codespace_prefers_the_bound_repo(monkeypatch):
    monkeypatch.setattr("agent_codespaces.account_binding.bound_repo", lambda n: "o/bound")
    with patch("subprocess.run") as run, patch.object(lifecycle, "list_codespaces") as listing:
        assert lifecycle.repository_for_codespace("cs-a") == "o/bound"
    run.assert_not_called()
    listing.assert_not_called()


def test_repository_for_codespace_uses_targeted_api_as_owner(monkeypatch):
    import subprocess

    monkeypatch.setattr("agent_codespaces.account_binding.bound_repo", lambda n: None)
    monkeypatch.setattr("agent_codespaces.account_binding.bound_account", lambda n: "owner")
    monkeypatch.setattr(gh_account, "env_for_account", lambda login: {"GH_TOKEN": f"tok-{login}"})
    ok = subprocess.CompletedProcess([], 0, "o/api\n", "")
    with patch("subprocess.run", return_value=ok) as run, \
         patch.object(lifecycle, "list_codespaces") as listing:
        assert lifecycle.repository_for_codespace("cs-a") == "o/api"
    assert run.call_args.args[0][:3] == ["gh", "api", "/user/codespaces/cs-a"]
    assert run.call_args.kwargs["env"] == {"GH_TOKEN": "tok-owner"}
    listing.assert_not_called()


def test_repository_for_codespace_falls_back_to_listing_then_none(monkeypatch):
    import subprocess

    monkeypatch.setattr("agent_codespaces.account_binding.bound_repo", lambda n: None)
    monkeypatch.setattr(gh_account, "env_for_account", lambda login: {})
    miss = subprocess.CompletedProcess([], 1, "", "HTTP 404")
    listed = _cs("cs-a", "")
    listed.repository = "o/listed"
    with patch("subprocess.run", return_value=miss), \
         patch.object(lifecycle, "list_codespaces", return_value=[listed]):
        assert lifecycle.repository_for_codespace("cs-a") == "o/listed"
        assert lifecycle.repository_for_codespace("missing") is None
    with patch("subprocess.run", side_effect=OSError("no gh")):
        assert lifecycle.repository_for_codespace("cs-a") is None
