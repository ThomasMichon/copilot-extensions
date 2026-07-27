"""Tests for the gh account identity catalog (accounts.yaml)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_worktrees import accounts


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Redirect ~ so the catalog reads/writes under a tmp dir."""
    monkeypatch.setattr(accounts.Path, "home", lambda: tmp_path)
    return tmp_path


def test_empty_catalog_when_missing(home: Path):
    assert accounts.list_accounts() == []
    assert accounts.find_account("ThomasMichon") is None
    assert accounts.login_flow_for("ThomasMichon") is None
    assert accounts.scopes_for("ThomasMichon") == []


def test_set_and_read_round_trip(home: Path):
    accounts.set_account(
        "ThomasMichon",
        scopes=["codespace", "repo", "workflow"],
        login_flow="gh auth login -h github.com",
    )
    e = accounts.find_account("ThomasMichon")
    assert e is not None
    assert e.host == "github.com"
    assert e.scopes == ["codespace", "repo", "workflow"]
    assert e.login_flow == "gh auth login -h github.com"


def test_find_account_case_insensitive(home: Path):
    accounts.set_account("ThomasMichon", scopes=["codespace"])
    assert accounts.find_account("thomasmichon") is not None
    assert accounts.scopes_for("THOMASMICHON") == ["codespace"]


def test_set_account_merges(home: Path):
    accounts.set_account("acct", scopes=["repo"])
    accounts.set_account("acct", login_flow="gh auth login")
    e = accounts.find_account("acct")
    assert e.scopes == ["repo"]  # preserved
    assert e.login_flow == "gh auth login"  # added


def test_remove_account(home: Path):
    accounts.set_account("acct")
    assert accounts.remove_account("acct") is True
    assert accounts.find_account("acct") is None
    assert accounts.remove_account("acct") is False


def test_bare_login_entry_parses(home: Path):
    """A ``login:`` key with an empty/None body is a valid minimal entry."""
    path = home / ".agent-worktrees"
    path.mkdir(parents=True, exist_ok=True)
    (path / "accounts.yaml").write_text(
        "accounts:\n  soloacct:\n", encoding="utf-8"
    )
    e = accounts.find_account("soloacct")
    assert e is not None
    assert e.login == "soloacct"
    assert e.scopes == []
