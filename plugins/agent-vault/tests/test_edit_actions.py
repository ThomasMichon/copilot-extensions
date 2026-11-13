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
        lambda db, entry, dest, **kw: called.update(entry=entry, dest=dest) or (True, "Entry moved"),
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
        lambda db, entry, dest, **kw: called.update(entry=entry, dest=dest) or (True, "Entry moved"),
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
        lambda db, entry, dest, **kw: called.update(entry=entry, dest=dest) or (True, "Entry copied"),
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
                        lambda db, entry, dest, **kw: (_ for _ in ()).throw(AssertionError("must not copy")))
    resp = unlocked.handle_request(
        {"action": "copy", "entry": "Managed/x", "dest": "Other", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "already exists" in resp["error"]


def test_copy_overwrite_replaces_existing_destination(unlocked, fake_tree, monkeypatch, kpdb):
    calls = []
    monkeypatch.setattr(unlocked.cli, "remove_entry",
                        lambda db, entry: (_ for _ in ()).throw(AssertionError("backend owns removal")))
    monkeypatch.setattr(unlocked.cli, "copy_entry",
                        lambda db, entry, dest, **kw: calls.append((entry, dest, kw)) or (True, "Entry copied"))
    resp = unlocked.handle_request(
        {"action": "copy", "entry": "Managed/x", "dest": "Other/", "overwrite": True,
         "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert calls == [("Managed/x", "Other/x", {"replace": True, "skip_notes": False})]


@pytest.mark.parametrize("action", ["move", "copy"])
def test_legacy_force_does_not_overwrite(unlocked, fake_tree, monkeypatch, kpdb, action):
    # Older clients sent force=true to bypass the former group-scope guard.
    boom = lambda *a, **kw: (_ for _ in ()).throw(AssertionError("must not relocate"))  # noqa: E731
    monkeypatch.setattr(unlocked.cli, "copy_entry", boom)
    monkeypatch.setattr(unlocked.cli, "move_entry", boom)
    resp = unlocked.handle_request(
        {"action": action, "entry": "Managed/x", "dest": "Other", "force": True,
         "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "already exists" in resp["error"]


class _FakeKeePass:
    """Minimal in-memory stand-in for the keepassxc-cli backend primitives."""

    def __init__(self, backend, monkeypatch, entries, *, fail_add=False):
        self.entries = entries
        self.removed = []
        monkeypatch.setattr(backend, "has_entry", lambda db, e: e in self.entries)
        monkeypatch.setattr(backend, "_read_field_raw",
                            lambda db, e, attr: self.entries[e].get(attr, ""))
        monkeypatch.setattr(backend, "list_attachments", lambda db, e: [])
        monkeypatch.setattr(backend, "remove_entry", self.remove)
        monkeypatch.setattr(backend, "_rename_entry", self.rename)
        monkeypatch.setattr(backend, "_ensure_parent_groups", lambda db, e: None)
        monkeypatch.setattr(
            backend, "add_entry",
            lambda db, e, **kw: (False, "add failed") if fail_add else self.add(e, kw),
        )

    def add(self, path, kw):
        self.entries[path] = {"UserName": kw.get("username") or "",
                              "Password": kw.get("password") or ""}
        return True, "Entry created"

    def remove(self, db, path):
        self.removed.append(path)
        self.entries.pop(path)
        return True, "Entry removed"

    def rename(self, db, path, title):
        parent = path.rpartition("/")[0]
        self.entries[f"{parent}/{title}"] = self.entries.pop(path)
        return True, "Entry renamed"


def test_backend_copy_stages_before_replacing(monkeypatch, kpdb):
    from agent_vault.keepassxc import KeePassXCBackend

    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {
        "A/x": {"UserName": "u", "Password": "new"},
        "B/x": {"UserName": "u", "Password": "old"},
    })
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x", replace=True)
    assert ok, msg
    assert fake.entries["B/x"]["Password"] == "new"
    assert fake.entries["A/x"]["Password"] == "new"
    assert fake.removed == ["B/x"]
    assert sorted(fake.entries) == ["A/x", "B/x"]


def test_backend_copy_failure_keeps_existing_destination(monkeypatch, kpdb):
    from agent_vault.keepassxc import KeePassXCBackend

    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {
        "A/x": {"Password": "new"}, "B/x": {"Password": "old"},
    }, fail_add=True)
    ok, _ = backend.copy_entry(kpdb, "A/x", "B/x", replace=True)
    assert not ok
    assert fake.entries["B/x"]["Password"] == "old"
    assert fake.removed == []


def test_backend_copy_refuses_notes_unless_skipped(monkeypatch, kpdb):
    from agent_vault.keepassxc import KeePassXCBackend

    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": "p", "Notes": "private"}})
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y")
    assert not ok and "notes" in msg
    assert "B/y" not in fake.entries
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y", skip_notes=True)
    assert ok, msg
    assert fake.entries["B/y"]["Password"] == "p"


def test_backend_copy_refuses_url_credentials(monkeypatch, kpdb):
    from agent_vault.keepassxc import KeePassXCBackend

    backend = KeePassXCBackend()
    _FakeKeePass(backend, monkeypatch, {"A/x": {"URL": "https://user:pw@example.com/"}})
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y")
    assert not ok and "URL" in msg


def test_backend_move_rename_across_groups_avoids_sibling_collisions(monkeypatch, kpdb):
    from agent_vault.keepassxc import KeePassXCBackend

    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {
        "A/x": {"Password": "src"}, "A/y": {"Password": "a-y"}, "B/x": {"Password": "b-x"},
    })

    def fake_run(db, args):
        assert args[:2] == ["mv", "-q"]
        src, group = args[3], args[4]
        fake.entries[f"{group}/{src.rpartition('/')[2]}"] = fake.entries.pop(src)
        return type("R", (), {"returncode": 0, "stderr": ""})()

    monkeypatch.setattr(backend, "_run", fake_run)
    ok, msg = backend.move_entry(kpdb, "A/x", "B/y")
    assert ok, msg
    assert fake.entries == {
        "A/y": {"Password": "a-y"}, "B/x": {"Password": "b-x"}, "B/y": {"Password": "src"},
    }


def test_copy_bare_destination_name_lands_in_group(unlocked, fake_tree, monkeypatch, kpdb):
    called = {}
    monkeypatch.setattr(
        unlocked.cli, "copy_entry",
        lambda db, entry, dest, **kw: called.update(dest=dest) or (True, "Entry copied"),
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