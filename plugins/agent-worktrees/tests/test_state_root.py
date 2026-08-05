"""Tests for agent_worktrees.state_root -- the stateless-harness state-root resolver."""

from __future__ import annotations

import pytest

from agent_worktrees import config as cfg
from agent_worktrees import state_root as sr


def _config(repo_name, *, stateless=False, knowledge_repo="", anchor="/anchor"):
    return cfg.Config(
        srcroot="/src",
        machine="test",
        platform="linux",
        repo_name=repo_name,
        knowledge_repo=knowledge_repo,
        repos={
            repo_name: cfg.RepoConfig(
                anchor=anchor,
                worktree_root=f"{anchor}.worktrees",
                default_branch="main",
                remote="origin",
                stateless=stateless,
            )
        },
    )


@pytest.fixture
def fake_checkouts(monkeypatch):
    """Patch the registry->checkout resolver with an in-memory name->path map."""
    table: dict[str, str] = {}

    def _resolve(name):
        return table.get(name)

    monkeypatch.setattr(sr, "_checkout_path", _resolve)
    return table


# ---------------------------------------------------------------------------
# Non-stateless (backward compatible): the launch repo is the state home.
# ---------------------------------------------------------------------------

def test_non_stateless_uses_git_toplevel(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: "/work/tree")
    res = sr.resolve_state_root(_config("dotfiles"))
    assert res.path == "/work/tree"
    assert res.source == "launch_repo"
    assert res.stateless is False
    assert res.bound is True
    assert res.error is None


def test_non_stateless_falls_back_to_anchor(monkeypatch, tmp_path):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: None)
    res = sr.resolve_state_root(_config("dotfiles", anchor=str(tmp_path)))
    assert res.path == str(tmp_path)
    assert res.source == "launch_repo"
    assert res.bound is True


def test_non_stateless_unresolvable(monkeypatch):
    monkeypatch.setattr(sr, "_git_toplevel", lambda cwd: None)
    res = sr.resolve_state_root(_config("dotfiles", anchor="/does/not/exist"))
    assert res.path is None
    assert res.bound is False
    assert "could not resolve" in res.error


# ---------------------------------------------------------------------------
# Stateless harness -> the bound knowledge repo (no fallback).
# ---------------------------------------------------------------------------

def test_stateless_bound_resolves_knowledge_repo(fake_checkouts):
    fake_checkouts["citadel-knowledge"] = "/repos/knowledge"
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="citadel-knowledge")
    )
    assert res.path == "/repos/knowledge"
    assert res.source == "knowledge_repo"
    assert res.repo == "citadel-knowledge"
    assert res.stateless is True
    assert res.bound is True


def test_stateless_unbound_refuses(fake_checkouts):
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="")
    )
    assert res.path is None
    assert res.bound is False
    assert res.stateless is True
    assert "no knowledge_repo is bound" in res.error
    # Must NOT fall back to the harness tree.
    assert "citadel-harness" in res.error


def test_stateless_bound_but_unregistered(fake_checkouts):
    # knowledge_repo points at a name with no registered checkout.
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="ghost")
    )
    assert res.path is None
    assert res.bound is False
    assert "not a registered repo" in res.error


# ---------------------------------------------------------------------------
# Explicit override wins over the binding.
# ---------------------------------------------------------------------------

def test_explicit_override_targets_named_repo(fake_checkouts):
    fake_checkouts["odsp-web"] = "/repos/odsp-web"
    res = sr.resolve_state_root(
        _config("citadel-harness", stateless=True, knowledge_repo="citadel-knowledge"),
        repo_override="odsp-web",
    )
    assert res.path == "/repos/odsp-web"
    assert res.source == "explicit"
    assert res.repo == "odsp-web"


def test_explicit_override_unregistered(fake_checkouts):
    res = sr.resolve_state_root(
        _config("dotfiles"), repo_override="nope"
    )
    assert res.path is None
    assert res.source == "explicit"
    assert "not a registered repo" in res.error


def test_as_dict_shape(fake_checkouts):
    fake_checkouts["k"] = "/k"
    res = sr.resolve_state_root(
        _config("h", stateless=True, knowledge_repo="k")
    )
    d = res.as_dict()
    assert set(d) == {"state_root", "source", "repo", "stateless", "bound", "error"}
    assert d["state_root"] == "/k"
    assert d["bound"] is True
