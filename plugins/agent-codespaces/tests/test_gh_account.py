"""Tests for multi-account gh resolution (gh_account) + cross-account listing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import json
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
        run.return_value = MagicMock(returncode=0, stdout="example-operator\n")
        assert gh_account.account_for_repo("example-org/x") == "example-operator"
        args = run.call_args[0][0]
        assert args[:3] == ["aw", "repos", "account-for"]


def test_account_for_repo_none_when_unresolved():
    with patch.object(gh_account, "_agent_worktrees_bin", return_value="aw"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=1, stdout="")
        assert gh_account.account_for_repo("owner/x") is None


def test_account_for_repo_none_without_agent_worktrees():
    with patch.object(gh_account, "_agent_worktrees_bin", return_value=None):
        assert gh_account.account_for_repo("owner/x") is None


def test_explicit_individual_lookup_cannot_fall_back_on_undecodable_output(monkeypatch):
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
        env = gh_account.env_for_account("ThomasMichon", base={"GITHUB_TOKEN": "old"})
        assert env["GH_TOKEN"] == "ghs_tok"
        assert "GITHUB_TOKEN" not in env  # stale token dropped


def test_env_for_account_ambient_when_no_token():
    with patch.object(gh_account.shutil, "which", return_value="gh"), \
         patch.object(gh_account.subprocess, "run") as run:
        run.return_value = MagicMock(returncode=1, stdout="")
        env = gh_account.env_for_account("nobody", base={"X": "1"})
        assert env == {"X": "1"}


def test_env_for_account_none_login():
    env = gh_account.env_for_account(None, base={"X": "1"})
    assert env == {"X": "1"}


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
    monkeypatch.setattr(gh_account, "account_for_repo", fallback)
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
    monkeypatch.setattr(gh_account, "account_for_repo", fallback)
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
    with pytest.raises(gh_account.AccountInventoryError, match="could not read"):
        gh_account.account_inventory()


@pytest.mark.parametrize("error", [
    OSError("startup failed"), subprocess.TimeoutExpired("aw", 10),
    UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid output"),
])
def test_snapshot_process_failure_is_not_confirmed_empty(monkeypatch, error):
    monkeypatch.setattr(gh_account.worktrees, "explicit_context", lambda: False)
    monkeypatch.setattr(gh_account, "_agent_worktrees_bin", lambda: "aw")
    monkeypatch.setattr(
        gh_account.subprocess, "run", MagicMock(side_effect=error),
    )
    with pytest.raises(gh_account.AccountInventoryError, match="unavailable"):
        gh_account.account_inventory()


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
