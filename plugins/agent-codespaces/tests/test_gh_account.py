"""Tests for multi-account gh resolution (gh_account) + cross-account listing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from agent_codespaces import gh_account, lifecycle


@pytest.fixture(autouse=True)
def _clear_caches(monkeypatch):
    gh_account.clear_caches()
    monkeypatch.setattr(
        "agent_codespaces.account_binding.bound_account", lambda name: None,
    )
    monkeypatch.setattr("agent_codespaces.account_binding.bound_accounts", lambda: ())
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
         patch("agent_codespaces.gh_account.mapped_accounts",
               return_value=("ThomasMichon", "example-operator")):
        names = {c.name: c.account for c in lifecycle.list_codespaces()}
    assert names == {"cs-a": "ThomasMichon", "cs-b": "example-operator"}


def test_list_codespaces_ambient_only_without_map():
    with patch.object(lifecycle, "_list_codespaces_under",
                      return_value=[_cs("cs-x", "")]) as under, \
         patch("agent_codespaces.gh_account.mapped_accounts", return_value=()):
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
