"""Tests for the entry edit/CRUD actions: list, show, set-username, remove, move, copy."""

from __future__ import annotations

import pytest

from agent_vault import extensions as ext
from agent_vault.extensions import ExtensionRegistry
from agent_vault.service import VaultService, _within_group


@pytest.fixture
def empty_registry():
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    yield reg
    ext.reset_registry()


@pytest.fixture
def kpdb(tmp_path):
    p = tmp_path / "vault.kdbx"
    p.write_text("x", encoding="utf-8")
    return str(p)


@pytest.fixture
def unlocked(empty_registry, monkeypatch, kpdb):
    """An unlocked VaultService whose backend calls are stubbed out."""
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: True)
    return svc


# ---------------------------------------------------------------------------
# _within_group guard helper
# ---------------------------------------------------------------------------


def test_within_group_no_group_is_open():
    assert _within_group("Anything/x", None) is True
    assert _within_group("Anything/x", "") is True


def test_within_group_scoping():
    assert _within_group("Managed/x", "Managed") is True
    assert _within_group("Managed", "Managed") is True
    assert _within_group("Managed/sub/x", "Managed/") is True
    assert _within_group("Other/x", "Managed") is False
    assert _within_group("ManagedButNot/x", "Managed") is False


# ---------------------------------------------------------------------------
# list / ls
# ---------------------------------------------------------------------------


def test_list_returns_entries(unlocked, monkeypatch, kpdb):
    captured = {}

    def fake_list(db, path, *, recursive, flatten):
        captured.update(path=path, recursive=recursive, flatten=flatten)
        return ["Group/a", "Group/b"]

    monkeypatch.setattr(unlocked.cli, "list_entries", fake_list)
    resp = unlocked.handle_request(
        {"action": "ls", "path": "Group", "recursive": True, "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert resp["entries"] == ["Group/a", "Group/b"]
    assert captured == {"path": "Group", "recursive": True, "flatten": False}


def test_list_locked_returns_needs_unlock(empty_registry, monkeypatch, kpdb):
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: False)
    monkeypatch.setattr("agent_vault.service.prompt_password",
                        lambda _m: (_ for _ in ()).throw(AssertionError("no prompt")))
    resp = svc.handle_request({"action": "list", "kpdb": kpdb})
    assert resp["ok"] is False
    assert resp["needs_unlock"] is True


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_returns_output(unlocked, monkeypatch, kpdb):
    monkeypatch.setattr(unlocked.cli, "show_entry",
                        lambda db, entry, show_protected: "Title: x\nUserName: bob\n")
    resp = unlocked.handle_request({"action": "show", "entry": "Group/x", "kpdb": kpdb})
    assert resp["ok"] is True
    assert "UserName: bob" in resp["output"]


def test_show_not_found(unlocked, monkeypatch, kpdb):
    monkeypatch.setattr(unlocked.cli, "show_entry", lambda db, entry, show_protected: None)
    resp = unlocked.handle_request({"action": "show", "entry": "Group/missing", "kpdb": kpdb})
    assert resp["ok"] is False
    assert "not found" in resp["error"].lower()


# ---------------------------------------------------------------------------
# set-username
# ---------------------------------------------------------------------------


def test_set_username_updates_and_invalidates_cache(unlocked, monkeypatch, kpdb):
    monkeypatch.setattr(unlocked.cli, "edit_username", lambda db, entry, user: (True, "Username updated"))
    unlocked.cache[(kpdb, "Group/x", "username")] = "old"
    resp = unlocked.handle_request(
        {"action": "set-username", "entry": "Group/x", "username": "new", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert (kpdb, "Group/x", "username") not in unlocked.cache


def test_set_username_requires_username(unlocked, kpdb):
    resp = unlocked.handle_request({"action": "set-username", "entry": "Group/x", "kpdb": kpdb})
    assert resp["ok"] is False
    assert "username" in resp["error"].lower()


# ---------------------------------------------------------------------------
# remove / rm  (+ group-scoped destructive guard)
# ---------------------------------------------------------------------------


def test_remove_in_group(unlocked, monkeypatch, kpdb):
    called = {}

    def fake_remove(db, entry):
        called["entry"] = entry
        return (True, "Entry removed")

    monkeypatch.setattr(unlocked.cli, "remove_entry", fake_remove)
    resp = unlocked.handle_request(
        {"action": "rm", "entry": "Managed/x", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called["entry"] == "Managed/x"


def test_remove_out_of_group_blocked_without_force(unlocked, monkeypatch, kpdb):
    monkeypatch.setattr(unlocked.cli, "remove_entry",
                        lambda db, entry: (_ for _ in ()).throw(AssertionError("must not remove")))
    resp = unlocked.handle_request(
        {"action": "rm", "entry": "Other/x", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "outside" in resp["error"].lower()


def test_remove_out_of_group_allowed_with_force(unlocked, monkeypatch, kpdb):
    monkeypatch.setattr(unlocked.cli, "remove_entry", lambda db, entry: (True, "Entry removed"))
    resp = unlocked.handle_request(
        {"action": "rm", "entry": "Other/x", "group": "Managed", "force": True, "kpdb": kpdb}
    )
    assert resp["ok"] is True


# ---------------------------------------------------------------------------
# move / mv and copy / cp  (explicit paths reach any group)
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tree(unlocked, monkeypatch):
    """Stub group/entry existence with simple in-memory sets."""
    groups = {"Managed", "Managed/sub", "Other", "Archive"}
    entries = {"Managed/x", "Other/x"}
    monkeypatch.setattr(unlocked.cli, "group_exists", lambda db, g: g.strip("/") in groups)
    monkeypatch.setattr(unlocked.cli, "has_entry", lambda db, e: e.strip("/") in entries)
    return entries


def test_move_in_group(unlocked, fake_tree, monkeypatch, kpdb):
    called = {}
    monkeypatch.setattr(
        unlocked.cli, "move_entry",
        lambda db, entry, dest: called.update(entry=entry, dest=dest) or (True, "Entry moved"),
    )
    resp = unlocked.handle_request(
        {"action": "mv", "entry": "Managed/x", "dest": "Managed/sub", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called == {"entry": "Managed/x", "dest": "Managed/sub/x"}


def test_move_requires_dest(unlocked, kpdb):
    resp = unlocked.handle_request(
        {"action": "mv", "entry": "Managed/x", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "destination" in resp["error"].lower()


def test_move_out_of_group_explicit_path_needs_no_force(unlocked, fake_tree, monkeypatch, kpdb):
    called = {}
    monkeypatch.setattr(
        unlocked.cli, "move_entry",
        lambda db, entry, dest: called.update(entry=entry, dest=dest) or (True, "Entry moved"),
    )
    unlocked.cache[(kpdb, "Other/x", "password")] = "stale"
    resp = unlocked.handle_request(
        {"action": "mv", "entry": "Other/x", "dest": "Archive/renamed", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called == {"entry": "Other/x", "dest": "Archive/renamed"}
    assert (kpdb, "Other/x", "password") not in unlocked.cache


def test_copy_happy_path_keeps_source_and_invalidates_dest(unlocked, fake_tree, monkeypatch, kpdb):
    called = {}
    monkeypatch.setattr(
        unlocked.cli, "copy_entry",
        lambda db, entry, dest: called.update(entry=entry, dest=dest) or (True, "Entry copied"),
    )
    monkeypatch.setattr(unlocked.cli, "remove_entry",
                        lambda db, entry: (_ for _ in ()).throw(AssertionError("must not remove")))
    unlocked.cache[(kpdb, "Managed/x", "password")] = "keep"
    unlocked.cache[(kpdb, "Other/x2", "password")] = "stale"
    resp = unlocked.handle_request(
        {"action": "cp", "entry": "x", "dest": "Other/x2", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert resp["source"] == "Managed/x"
    assert resp["dest"] == "Other/x2"
    assert called == {"entry": "Managed/x", "dest": "Other/x2"}
    assert unlocked.cache[(kpdb, "Managed/x", "password")] == "keep"
    assert (kpdb, "Other/x2", "password") not in unlocked.cache


def test_copy_refuses_existing_destination_without_force(unlocked, fake_tree, monkeypatch, kpdb):
    monkeypatch.setattr(unlocked.cli, "copy_entry",
                        lambda db, entry, dest: (_ for _ in ()).throw(AssertionError("must not copy")))
    resp = unlocked.handle_request(
        {"action": "copy", "entry": "Managed/x", "dest": "Other", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "already exists" in resp["error"]


def test_copy_force_replaces_existing_destination(unlocked, fake_tree, monkeypatch, kpdb):
    calls = []
    monkeypatch.setattr(unlocked.cli, "remove_entry",
                        lambda db, entry: calls.append(("rm", entry)) or (True, "Entry removed"))
    monkeypatch.setattr(unlocked.cli, "copy_entry",
                        lambda db, entry, dest: calls.append(("cp", entry, dest)) or (True, "Entry copied"))
    resp = unlocked.handle_request(
        {"action": "copy", "entry": "Managed/x", "dest": "Other/", "force": True,
         "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert calls == [("rm", "Other/x"), ("cp", "Managed/x", "Other/x")]


def test_copy_bare_destination_name_lands_in_group(unlocked, fake_tree, monkeypatch, kpdb):
    called = {}
    monkeypatch.setattr(
        unlocked.cli, "copy_entry",
        lambda db, entry, dest: called.update(dest=dest) or (True, "Entry copied"),
    )
    resp = unlocked.handle_request(
        {"action": "cp", "entry": "Other/x", "dest": "y", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called["dest"] == "Managed/y"


def test_bare_name_normalization_unchanged():
    from agent_vault.config import normalize_entry

    assert normalize_entry("x", "Managed") == "Managed/x"
    assert normalize_entry("Managed/x", "Managed") == "Managed/x"
    assert normalize_entry("Other/x", "Managed") == "Other/x"
    assert normalize_entry("/x", "Managed") == "/x"