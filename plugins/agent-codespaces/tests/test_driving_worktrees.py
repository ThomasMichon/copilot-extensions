"""Tests for driving-worktree resolution: claim-owner lookup, the Phase 2b
worktree-family bypass, and per-workstream box resumption."""

from __future__ import annotations

import sys
import types

import pytest

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


def test_fully_qualified_workstream_rejects_another_repository_owner(monkeypatch):
    from agent_codespaces import account_binding

    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["cs-fork"]
    _install_fake_claims_owner(monkeypatch, fake)
    monkeypatch.setattr("agent_codespaces.lease.resolve_owner_worktree", lambda: "wt-a")
    monkeypatch.setattr(account_binding, "list_bindings", lambda **kw: [
        account_binding.AccountBinding("cs-fork", "fork-account", 0, "other/web-codespaces"),
    ])
    assert driving_worktrees.current_workstream_account("expected/web") is None
    assert driving_worktrees.resolve_workstream_box(
        [_Member("cs-fork", "other/web-codespaces")], "expected/web", "wt-a",
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


@pytest.mark.parametrize("claimed,repository,expected", [
    (True, "o/web-codespaces", "original-owner"),
    (False, "o/web-codespaces", None),
    (True, "o/other-codespaces", None),
])
def test_current_workstream_account_requires_own_matching_binding(
    monkeypatch, claimed, repository, expected,
):
    from agent_codespaces import account_binding

    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["cs-web"] if claimed else []
    _install_fake_claims_owner(monkeypatch, fake)
    monkeypatch.setattr("agent_codespaces.lease.resolve_owner_worktree", lambda: "wt-a")
    monkeypatch.setattr(account_binding, "list_bindings", lambda **kw: [
        account_binding.AccountBinding("cs-web", "original-owner", 0, repository),
    ])
    assert driving_worktrees.current_workstream_account("web") == expected


def test_current_workstream_account_preserves_binding_unavailability(monkeypatch):
    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["cs-web"]
    _install_fake_claims_owner(monkeypatch, fake)
    monkeypatch.setattr("agent_codespaces.lease.resolve_owner_worktree", lambda: "wt-a")

    def unavailable(**kwargs):
        raise RuntimeError("binding lock unavailable")

    monkeypatch.setattr("agent_codespaces.account_binding.list_bindings", unavailable)
    with pytest.raises(RuntimeError, match="binding lock unavailable"):
        driving_worktrees.current_workstream_account("web")


def test_current_workstream_account_reports_malformed_binding(monkeypatch):
    from agent_codespaces import account_binding

    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["cs-web"]
    _install_fake_claims_owner(monkeypatch, fake)
    monkeypatch.setattr("agent_codespaces.lease.resolve_owner_worktree", lambda: "wt-a")
    monkeypatch.setattr(account_binding, "list_bindings", lambda **kw: [
        account_binding.AccountBinding("cs-web", {"bad": "account"}, 0, "o/web"),
    ])
    with pytest.raises(RuntimeError, match="Malformed"):
        driving_worktrees.current_workstream_account("web")


@pytest.mark.parametrize("command", ["create", "allocate"])
def test_corrupt_binding_file_refuses_admission_before_active_account_switch(monkeypatch, tmp_path, capsys, command):
    from agent_codespaces import __main__ as cli, account_binding, gh_account
    from agent_codespaces.config import CodespacesConfig
    from agent_codespaces.pool import build_pool

    path = tmp_path / "account-bindings.json"
    path.write_text("{corrupt", encoding="utf-8")
    monkeypatch.setattr(account_binding, "BINDINGS_FILE", path)
    monkeypatch.setattr(account_binding, "_LOCK_FILE", tmp_path / "account-bindings.lock")
    monkeypatch.setattr(account_binding, "ensure_runtime_dir", lambda: None)
    fake = types.ModuleType("fake_claims_owner")
    fake.claims_of_owner = lambda owner, kind: ["retained-venue"]
    _install_fake_claims_owner(monkeypatch, fake)
    monkeypatch.setattr("agent_codespaces.lease.resolve_owner_worktree", lambda: "wt-a")
    monkeypatch.setattr(gh_account, "account_for_repo", lambda repo: None)
    monkeypatch.setattr(gh_account, "active_account", lambda: "new-owner")
    for name in ("GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("agent_codespaces.owner_inventory.qualified_environment", lambda account: {"GH_TOKEN": "fixture-token"})
    monkeypatch.setattr(cli, "load_merged_config", CodespacesConfig)
    monkeypatch.setattr(cli, "validate_context", lambda: None)
    monkeypatch.setattr(cli, "_gh_binary_available", lambda: True)
    monkeypatch.setattr(cli, "_require_codespace_scope", lambda *a, **kw: None)
    empty = build_pool(codespaces=[], leases=[], markers={}, l2_leases={}, clean_records={}, live={})
    monkeypatch.setattr(cli.pool_mod, "build_pool", lambda **kw: empty)
    monkeypatch.setattr(cli, "create_codespace", lambda *a, **kw: types.SimpleNamespace(name="duplicate"))
    extra = ["--json"] if command == "allocate" else ["--no-wait"]
    assert cli.main([command, "example/project", *extra]) == 4
    assert path.read_text(encoding="utf-8") == "{corrupt"
    output = capsys.readouterr()
    assert "binding" in (output.out + output.err).lower()


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
