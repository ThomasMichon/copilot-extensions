"""Tests for the entry edit/CRUD actions: list, show, set-username, remove, move, copy."""

from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any, NoReturn

import pytest

from agent_vault import extensions as ext
from agent_vault.config import within_group as _within_group
from agent_vault.extensions import ExtensionRegistry
from agent_vault.keepassxc import KeePassXCBackend, _parse_attachment_names
from agent_vault.service import VaultService


def _never(message: str) -> Any:
    def fail(*_args: object, **_kwargs: object) -> NoReturn:
        raise AssertionError(message)
    return fail


@pytest.fixture
def empty_registry() -> Iterator[ExtensionRegistry]:
    reg = ExtensionRegistry()
    reg._loaded = True
    ext._REGISTRY = reg
    yield reg
    ext.reset_registry()


@pytest.fixture
def kpdb(tmp_path: Path) -> str:
    p = tmp_path / "vault.kdbx"
    p.write_text("x", encoding="utf-8")
    return str(p)


@pytest.fixture
def unlocked(
    empty_registry: ExtensionRegistry, monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> VaultService:
    """An unlocked VaultService whose backend calls are stubbed out."""
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: True)
    return svc


# ---------------------------------------------------------------------------
# _within_group guard helper
# ---------------------------------------------------------------------------


def test_within_group_no_group_is_open() -> None:
    assert _within_group("Anything/x", None) is True
    assert _within_group("Anything/x", "") is True


def test_within_group_scoping() -> None:
    assert _within_group("Managed/x", "Managed") is True
    assert _within_group("Managed", "Managed") is True
    assert _within_group("Managed/sub/x", "Managed/") is True
    assert _within_group("Other/x", "Managed") is False
    assert _within_group("ManagedButNot/x", "Managed") is False


# ---------------------------------------------------------------------------
# list / ls
# ---------------------------------------------------------------------------


def test_list_returns_entries(unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str) -> None:
    captured: dict[str, object] = {}

    def fake_list(db: str, path: str, *, recursive: bool, flatten: bool) -> list[str]:
        captured.update(path=path, recursive=recursive, flatten=flatten)
        return ["Group/a", "Group/b"]

    monkeypatch.setattr(unlocked.cli, "list_entries", fake_list)
    resp = unlocked.handle_request(
        {"action": "ls", "path": "Group", "recursive": True, "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert resp["entries"] == ["Group/a", "Group/b"]
    assert captured == {"path": "Group", "recursive": True, "flatten": False}


def test_list_locked_returns_needs_unlock(
    empty_registry: ExtensionRegistry, monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    svc = VaultService()
    monkeypatch.setattr(svc.cli, "has_password", lambda db=None: False)
    monkeypatch.setattr("agent_vault.service.prompt_password", _never("no prompt"))
    resp = svc.handle_request({"action": "list", "kpdb": kpdb})
    assert resp["ok"] is False
    assert resp["needs_unlock"] is True


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------


def test_show_returns_output(unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str) -> None:
    monkeypatch.setattr(unlocked.cli, "show_entry",
                        lambda db, entry, show_protected: "Title: x\nUserName: bob\n")
    resp = unlocked.handle_request({"action": "show", "entry": "Group/x", "kpdb": kpdb})
    assert resp["ok"] is True
    assert "UserName: bob" in resp["output"]


def test_show_not_found(unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str) -> None:
    monkeypatch.setattr(unlocked.cli, "show_entry", lambda db, entry, show_protected: None)
    resp = unlocked.handle_request({"action": "show", "entry": "Group/missing", "kpdb": kpdb})
    assert resp["ok"] is False
    assert "not found" in resp["error"].lower()


# ---------------------------------------------------------------------------
# set-username
# ---------------------------------------------------------------------------


def test_set_username_updates_and_invalidates_cache(
    unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    monkeypatch.setattr(unlocked.cli, "edit_username", lambda db, entry, user: (True, "Username updated"))
    unlocked.cache[(kpdb, "Group/x", "username")] = "old"
    resp = unlocked.handle_request(
        {"action": "set-username", "entry": "Group/x", "username": "new", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert (kpdb, "Group/x", "username") not in unlocked.cache


def test_set_username_requires_username(unlocked: VaultService, kpdb: str) -> None:
    resp = unlocked.handle_request({"action": "set-username", "entry": "Group/x", "kpdb": kpdb})
    assert resp["ok"] is False
    assert "username" in resp["error"].lower()


# ---------------------------------------------------------------------------
# remove / rm  (+ group-scoped destructive guard)
# ---------------------------------------------------------------------------


def test_remove_in_group(unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str) -> None:
    called: dict[str, str] = {}

    def fake_remove(db: str, entry: str) -> tuple[bool, str]:
        called["entry"] = entry
        return (True, "Entry removed")

    monkeypatch.setattr(unlocked.cli, "remove_entry", fake_remove)
    resp = unlocked.handle_request(
        {"action": "rm", "entry": "Managed/x", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called["entry"] == "Managed/x"


def test_remove_out_of_group_blocked_without_force(
    unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    monkeypatch.setattr(unlocked.cli, "remove_entry", _never("must not remove"))
    resp = unlocked.handle_request(
        {"action": "rm", "entry": "Other/x", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "outside" in resp["error"].lower()


def test_remove_out_of_group_allowed_with_force(
    unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    monkeypatch.setattr(unlocked.cli, "remove_entry", lambda db, entry: (True, "Entry removed"))
    resp = unlocked.handle_request(
        {"action": "rm", "entry": "Other/x", "group": "Managed", "force": True, "kpdb": kpdb}
    )
    assert resp["ok"] is True


# ---------------------------------------------------------------------------
# move / mv and copy / cp
# ---------------------------------------------------------------------------


@pytest.fixture
def fake_tree(unlocked: VaultService, monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Stub group/entry existence with simple in-memory sets."""
    groups = {"Managed", "Managed/sub", "Other", "Archive"}
    entries = {"Managed/x", "Other/x"}
    monkeypatch.setattr(unlocked.cli, "group_exists", lambda db, g: g.strip("/") in groups)
    monkeypatch.setattr(unlocked.cli, "has_entry", lambda db, e: e.strip("/") in entries)
    return entries


def _record(called: dict[str, str], message: str) -> Any:
    def record(db: str, entry: str, dest: str, **_kw: object) -> tuple[bool, str]:
        called.update(entry=entry, dest=dest)
        return True, message
    return record


def test_move_in_group(
    unlocked: VaultService, fake_tree: set[str], monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    called: dict[str, str] = {}
    monkeypatch.setattr(unlocked.cli, "move_entry", _record(called, "Entry moved"))
    resp = unlocked.handle_request(
        {"action": "mv", "entry": "Managed/x", "dest": "Managed/sub", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called == {"entry": "Managed/x", "dest": "Managed/sub"}


def test_move_requires_dest(unlocked: VaultService, kpdb: str) -> None:
    resp = unlocked.handle_request(
        {"action": "mv", "entry": "Managed/x", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "destination" in resp["error"].lower()


def test_move_out_of_group_blocked_without_force(
    unlocked: VaultService, monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    monkeypatch.setattr(unlocked.cli, "move_entry", _never("must not move"))
    resp = unlocked.handle_request(
        {"action": "mv", "entry": "Other/x", "dest": "Managed", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "outside" in resp["error"]


def test_copy_happy_path_keeps_source_and_invalidates_dest(
    unlocked: VaultService, fake_tree: set[str], monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    called: dict[str, str] = {}
    monkeypatch.setattr(unlocked.cli, "copy_entry", _record(called, "Entry copied"))
    monkeypatch.setattr(unlocked.cli, "remove_entry", _never("must not remove"))
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


def test_copy_refuses_existing_destination(
    unlocked: VaultService, fake_tree: set[str], monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    monkeypatch.setattr(unlocked.cli, "copy_entry", _never("must not copy"))
    resp = unlocked.handle_request(
        {"action": "copy", "entry": "Managed/x", "dest": "Other", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is False
    assert "already exists" in resp["error"]


class _FakeKeePass:
    """Minimal in-memory stand-in for the backend command-line primitives."""

    def __init__(
        self, backend: KeePassXCBackend, monkeypatch: pytest.MonkeyPatch,
        entries: dict[str, dict[str, str]], *, fail_add: bool = False,
        fail_rename_to: str | None = None,
        attachments: dict[str, dict[str, bytes]] | None = None,
        fail_attachment: str | None = None, terminator: bytes = b"\n",
        commit_failed_add: bool = False, fail_remove: bool = False,
    ) -> None:
        self.entries = entries
        self.attachments = attachments if attachments is not None else {}
        self.fail_attachment = fail_attachment
        self.terminator = terminator
        self.commit_failed_add = commit_failed_add
        self.fail_remove = fail_remove
        self.removed: list[str] = []
        self.fail_rename_to = fail_rename_to
        self.fail_add = fail_add
        monkeypatch.setattr(backend, "has_entry", lambda db, e: e in self.entries)
        monkeypatch.setattr(backend, "_run_raw", self.run_raw)
        monkeypatch.setattr(backend, "_run", self.run)
        monkeypatch.setattr(backend, "list_attachments",
                            lambda db, e: list(self.attachments.get(e, {})))
        monkeypatch.setattr(backend, "export_attachment", self.export_attachment)
        monkeypatch.setattr(backend, "import_attachment", self.import_attachment)
        monkeypatch.setattr(backend, "remove_entry", self.remove)
        monkeypatch.setattr(backend, "_rename_entry", self.rename)
        monkeypatch.setattr(backend, "_ensure_parent_groups", lambda db, e: None)
        monkeypatch.setattr(backend, "add_entry", self.add_entry)

    def run_raw(self, db: str, args: list[str]) -> subprocess.CompletedProcess[bytes]:
        path, attr = args[-3], args[-1]
        if path not in self.entries:
            return subprocess.CompletedProcess(args, 1, b"", b"not found")
        value = path.rpartition("/")[2] if attr == "Title" else self.entries[path].get(attr, "")
        out = value.encode() + self.terminator
        return subprocess.CompletedProcess(args, 0, out, b"")

    def run(self, db: str, args: list[str]) -> subprocess.CompletedProcess[str]:
        assert args[0] == "ls"
        prefix = f"{args[-1]}/"
        titles = [e[len(prefix):] for e in self.entries if e.startswith(prefix)]
        return subprocess.CompletedProcess(args, 0, "\n".join(titles) + "\n", "")

    def add_entry(self, db: str, path: str, **kw: Any) -> tuple[bool, str]:
        if self.commit_failed_add:
            self.add(path, kw)
        if self.fail_add or self.commit_failed_add:
            return False, "add failed: TimeoutExpired"
        return self.add(path, kw)

    def add(self, path: str, kw: dict[str, Any]) -> tuple[bool, str]:
        self.entries[path] = {"UserName": kw.get("username") or "",
                              "Password": kw.get("password") or ""}
        return True, "Entry created"

    def export_attachment(
        self, db: str, path: str, name: str, **kw: Any,
    ) -> tuple[bytes | None, str]:
        if self.fail_attachment == f"export:{name}":
            return None, "export failed"
        return self.attachments[path][name], "ok"

    def import_attachment(
        self, db: str, path: str, name: str, data: bytes, **kw: Any,
    ) -> tuple[bool, str]:
        if self.fail_attachment == f"import:{name}":
            return False, "import failed"
        self.attachments.setdefault(path, {})[name] = data
        return True, "ok"

    def remove(self, db: str, path: str) -> tuple[bool, str]:
        if self.fail_remove or path not in self.entries:
            return False, "remove failed"
        self.removed.append(path)
        self.entries.pop(path)
        self.attachments.pop(path, None)
        return True, "Entry removed"

    def rename(self, db: str, path: str, title: str) -> tuple[bool, str]:
        if title == self.fail_rename_to and path.rpartition("/")[2].startswith(".agent-vault-tmp-"):
            self.fail_rename_to = None
            return False, "rename failed"
        parent = path.rpartition("/")[0]
        self.entries[f"{parent}/{title}"] = self.entries.pop(path)
        if path in self.attachments:
            self.attachments[f"{parent}/{title}"] = self.attachments.pop(path)
        return True, "Entry renamed"


def test_backend_copy_stages_then_renames_into_place(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"UserName": "u", "Password": "new"}},
                        attachments={"A/x": {"id_ed25519": b"key\x00bytes", "b.txt": b"b"}})
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x")
    assert ok, msg
    assert fake.entries == {"A/x": {"UserName": "u", "Password": "new"},
                            "B/x": {"UserName": "u", "Password": "new"}}
    assert fake.attachments["B/x"] == {"id_ed25519": b"key\x00bytes", "b.txt": b"b"}
    assert fake.removed == []


def test_backend_copy_refuses_existing_destination(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {
        "A/x": {"Password": "new"}, "B/x": {"Password": "old"},
    }, attachments={"B/x": {"keep": b"k"}})
    monkeypatch.setattr(backend, "add_entry", _never("must not stage"))
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x")
    assert not ok and "already exists" in msg
    assert fake.entries == {"A/x": {"Password": "new"}, "B/x": {"Password": "old"}}
    assert fake.attachments == {"B/x": {"keep": b"k"}}


def test_backend_copy_removes_staged_entry_when_final_rename_fails(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"B/z": {"Password": "src"}}, fail_rename_to="x")
    ok, msg = backend.copy_entry(kpdb, "B/z", "B/x")
    assert not ok and "Rename failed" in msg and "may remain" not in msg
    assert fake.entries == {"B/z": {"Password": "src"}}


def test_backend_copy_add_failure_leaves_nothing(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": "new"}}, fail_add=True)
    ok, _ = backend.copy_entry(kpdb, "A/x", "B/x")
    assert not ok
    assert fake.entries == {"A/x": {"Password": "new"}}
    assert fake.removed == []


@pytest.mark.parametrize("failure", ["export:b.txt", "import:b.txt"])
def test_backend_copy_attachment_failure_cleans_staging(
    monkeypatch: pytest.MonkeyPatch, kpdb: str, failure: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": "new"}},
                        attachments={"A/x": {"a.txt": b"a", "b.txt": b"b"}},
                        fail_attachment=failure)
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x")
    assert not ok and "b.txt" in msg
    assert sorted(fake.entries) == ["A/x"]
    assert sorted(fake.attachments) == ["A/x"]
    assert len(fake.removed) == 1 and fake.removed[0].startswith("B/.agent-vault-tmp-")


@pytest.mark.parametrize(("output", "expected"), [
    ("x\n\nAttachments:\n  id_ed25519 (411 B)\n  a (b).txt (1.2 KiB)\n",
     ["id_ed25519", "a (b).txt"]),
    ("x\n\nAnh\u00e4nge:\n  id_ed25519 (411 B)\n", ["id_ed25519"]),
    ("x\n\nNo attachments present.\n", []),
    ("x\n\nKeine Anh\u00e4nge vorhanden.\n", []),
    ("\n\nAttachments:\n  k (1 B)\n", ["k"]),
    ("x\n", None),
    ("x\nAttachments:\n  k (1 B)\n", None),
    ("x\n\nAttachments:\n  k\n", None),
])
def test_attachment_listing_is_language_independent_and_fails_closed(
    output: str, expected: list[str] | None,
) -> None:
    assert _parse_attachment_names(output) == expected

def test_backend_copy_refuses_notes_unless_skipped(monkeypatch: pytest.MonkeyPatch, kpdb: str) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": "p", "Notes": "private"}})
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y")
    assert not ok and "notes" in msg
    assert "B/y" not in fake.entries
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y", skip_notes=True)
    assert ok, msg
    assert fake.entries["B/y"]["Password"] == "p"


def test_backend_copy_refuses_url_unless_skipped(monkeypatch: pytest.MonkeyPatch, kpdb: str) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"URL": "https://example.com/?token=t"}})
    added: list[dict[str, Any]] = []

    def add_entry(db: str, path: str, **kw: Any) -> tuple[bool, str]:
        added.append(kw)
        return fake.add(path, kw)

    monkeypatch.setattr(backend, "add_entry", add_entry)
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y")
    assert not ok and "URL" in msg
    assert "B/y" not in fake.entries
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y", skip_url=True)
    assert ok, msg
    assert "url" not in added[-1]


@pytest.mark.parametrize("terminator", [b"\n", b"\r\n"])
@pytest.mark.parametrize("password", ["line1\nline2", "line1\r\nline2", "pw\r"])
def test_backend_copy_refuses_password_that_cannot_round_trip(
    monkeypatch: pytest.MonkeyPatch, kpdb: str, password: str, terminator: bytes,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": password}},
                        terminator=terminator)
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x")
    assert not ok and "carriage-return" in msg
    assert fake.entries == {"A/x": {"Password": password}}


def test_backend_copy_strips_only_the_cli_terminator(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"UserName": "u", "Password": "pw"}},
                        terminator=b"\r\n")
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/y")
    assert ok, msg
    assert fake.entries["B/y"] == {"UserName": "u", "Password": "pw"}


def test_backend_copy_cleans_up_staged_entry_committed_before_timeout(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": "new"}},
                        commit_failed_add=True)
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x")
    assert not ok and "TimeoutExpired" in msg and "may remain" not in msg
    assert fake.entries == {"A/x": {"Password": "new"}}


def test_backend_copy_names_staged_entry_it_cannot_remove(
    monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    backend = KeePassXCBackend()
    fake = _FakeKeePass(backend, monkeypatch, {"A/x": {"Password": "new"}},
                        commit_failed_add=True, fail_remove=True)
    ok, msg = backend.copy_entry(kpdb, "A/x", "B/x")
    staged = next(e for e in fake.entries if e.startswith("B/.agent-vault-tmp-"))
    assert not ok and f"may remain at '{staged}'" in msg


def test_copy_bare_destination_name_lands_in_group(
    unlocked: VaultService, fake_tree: set[str], monkeypatch: pytest.MonkeyPatch, kpdb: str,
) -> None:
    called: dict[str, str] = {}
    monkeypatch.setattr(unlocked.cli, "copy_entry", _record(called, "Entry copied"))
    resp = unlocked.handle_request(
        {"action": "cp", "entry": "Other/x", "dest": "y", "group": "Managed", "kpdb": kpdb}
    )
    assert resp["ok"] is True
    assert called["dest"] == "Managed/y"


def test_bare_name_normalization_unchanged() -> None:
    from agent_vault.config import normalize_entry

    assert normalize_entry("x", "Managed") == "Managed/x"
    assert normalize_entry("Managed/x", "Managed") == "Managed/x"
    assert normalize_entry("Other/x", "Managed") == "Other/x"
    assert normalize_entry("/x", "Managed") == "x"


def test_copy_cli_sends_skip_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse

    from agent_vault import cli, copy_command

    sent: list[dict[str, Any]] = []

    def send(request: dict[str, Any], timeout: float) -> dict[str, Any]:
        sent.append(request)
        return {"ok": True, "message": "Entry copied", "source": "A/x", "dest": "B/x"}

    monkeypatch.setattr(cli, "_ensure_unlocked_service", lambda: True)
    monkeypatch.setattr(cli, "send_command", send)
    args = argparse.Namespace(entry="A/x", dest="B/", skip_notes=False, skip_url=True)
    assert copy_command.cmd_copy(args) == 0
    assert sent == [{"action": "copy", "entry": "A/x", "dest": "B/", "skip_url": True}]
