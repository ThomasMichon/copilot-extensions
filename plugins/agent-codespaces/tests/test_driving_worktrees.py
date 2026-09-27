"""Tests for driving-worktree resolution: claim-owner lookup, the Phase 2b
worktree-family bypass, and per-workstream box resumption."""

from __future__ import annotations

import sys
import types

from agent_codespaces import driving_worktrees


def _install_fake_claims_owner(monkeypatch, module: types.ModuleType) -> None:
    fake_pkg = types.ModuleType("agent_worktrees")
    fake_pkg.claims_owner = module
    monkeypatch.setitem(sys.modules, "agent_worktrees", fake_pkg)
    monkeypatch.setitem(sys.modules, "agent_worktrees.claims_owner", module)


# --- resolve_workstream_box (Phase 2b) --------------------------------------


class _Member:
    def __init__(self, name, repository):
        self.name = name
        self.repository = repository


def test_resolve_workstream_box_picks_matching_repo_claim(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["cs-web", "cs-other"]
    _install_fake_claims_owner(monkeypatch, fake)

    members = [
        _Member("cs-web", "o/web-codespaces"),
        _Member("cs-other", "o/other-codespaces"),
    ]
    assert driving_worktrees.resolve_workstream_box(
        members, "web", "wt-a",
    ) == "cs-web"


def test_resolve_workstream_box_no_owner_returns_none():
    assert driving_worktrees.resolve_workstream_box([], "web", None) is None
    assert driving_worktrees.resolve_workstream_box([], "web", "") is None


def test_resolve_workstream_box_no_agent_worktrees_degrades_none(monkeypatch):
    monkeypatch.delitem(sys.modules, "agent_worktrees", raising=False)
    monkeypatch.delitem(sys.modules, "agent_worktrees.claims_owner", raising=False)
    import builtins

    real_import = builtins.__import__

    def _no_agent_worktrees(name, *a, **k):
        if name == "agent_worktrees" or name.startswith("agent_worktrees."):
            raise ImportError(name)
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_agent_worktrees)
    assert driving_worktrees.resolve_workstream_box(
        [_Member("cs-web", "o/web-codespaces")], "web", "wt-a",
    ) is None


def test_resolve_workstream_box_claim_not_in_pool_returns_none(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["cs-gone"]
    _install_fake_claims_owner(monkeypatch, fake)

    assert driving_worktrees.resolve_workstream_box(
        [_Member("cs-web", "o/web-codespaces")], "web", "wt-a",
    ) is None


def test_resolve_workstream_box_no_claims_returns_none(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: []
    _install_fake_claims_owner(monkeypatch, fake)

    assert driving_worktrees.resolve_workstream_box(
        [_Member("cs-web", "o/web-codespaces")], "web", "wt-a",
    ) is None


def test_resolve_workstream_box_degrades_on_lookup_error(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")

    def _boom(owner, kind):
        raise RuntimeError("registry unreadable")

    fake.claims_of_owner = _boom
    _install_fake_claims_owner(monkeypatch, fake)

    assert driving_worktrees.resolve_workstream_box(
        [_Member("cs-web", "o/web-codespaces")], "web", "wt-a",
    ) is None


# --- same_worktree_family (Phase 2b force-takeover bypass) ------------------


def test_same_worktree_family_delegates_to_agent_worktrees(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")
    fake.same_worktree_family = lambda a, b: (a, b) == ("parent", "child")
    _install_fake_claims_owner(monkeypatch, fake)

    assert driving_worktrees.same_worktree_family("parent", "child") is True
    assert driving_worktrees.same_worktree_family("parent", "stranger") is False


def test_same_worktree_family_missing_args_is_false():
    assert driving_worktrees.same_worktree_family("", "wt-a") is False
    assert driving_worktrees.same_worktree_family("wt-a", "") is False


def test_same_worktree_family_degrades_on_lookup_error(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")

    def _boom(a, b):
        raise RuntimeError("registry unreadable")

    fake.same_worktree_family = _boom
    _install_fake_claims_owner(monkeypatch, fake)

    assert driving_worktrees.same_worktree_family("wt-a", "wt-b") is False
