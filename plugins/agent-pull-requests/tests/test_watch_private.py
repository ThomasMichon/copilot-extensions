"""Private capabilities remain durable, non-link and exclusive across publication."""

import json
import os
import stat
from pathlib import Path

import pytest

from agent_pull_requests.watch_private import confirm_private, require_private, write_private


@pytest.mark.skipif(os.name != "nt", reason="native Windows explicit file ownership")
def test_new_windows_capability_sets_token_user_owner_before_publication(tmp_path, monkeypatch):
    import ctypes
    from ctypes import wintypes as w

    load = ctypes.WinDLL
    calls = []

    def library(name, **kwargs):
        native = load(name, **kwargs)
        if name != "advapi32":
            return native
        setter = native.SetFileSecurityW
        setter.argtypes, setter.restype = [w.LPCWSTR, w.DWORD, w.LPVOID], w.BOOL

        def set_security(path, flags, descriptor):
            calls.append((path, flags))
            return setter(path, flags, descriptor)

        native.SetFileSecurityW = set_security
        return native

    monkeypatch.setattr(ctypes, "WinDLL", library)
    path = tmp_path / "custody.json"
    write_private(path, {"revision": 1}, replace=False)
    require_private(path)
    confirm_private(path)
    assert len(calls) == 1
    assert Path(calls[0][0]).name.startswith(".custody.json.")
    assert calls[0][1] == 0x80000005


@pytest.mark.skipif(os.name != "nt", reason="native Windows foreign owner refusal")
def test_windows_foreign_owner_is_not_repaired_on_read_or_replacement(tmp_path, monkeypatch):
    import ctypes

    path = tmp_path / "custody.json"
    write_private(path, {"prior": True}, replace=False)
    before = path.read_bytes()
    load = ctypes.WinDLL

    def library(name, **kwargs):
        native = load(name, **kwargs)
        if name == "advapi32":
            native.EqualSid = lambda *_: False
            native.SetFileSecurityW = lambda *_: pytest.fail("foreign custody must not be repaired")
        return native

    monkeypatch.setattr(ctypes, "WinDLL", library)
    for action in (
        lambda: require_private(path),
        lambda: write_private(path, {"prior": False}),
    ):
        with pytest.raises(PermissionError, match="another OS account"):
            action()
    assert path.read_bytes() == before
    assert list(tmp_path.iterdir()) == [path]


def test_private_publication_and_replacement_preserve_account_privacy(tmp_path):
    path = tmp_path / "custody.json"
    write_private(path, {"test_capability": "test-owned-not-a-credential"}, replace=False)
    require_private(path)
    confirm_private(path)
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    write_private(path, {"revision": 2})
    require_private(path)
    assert json.loads(path.read_text()) == {"revision": 2}
    assert list(tmp_path.iterdir()) == [path]


def test_exclusive_publication_never_overwrites_existing_custody(tmp_path):
    path = tmp_path / "custody.json"
    write_private(path, {"prior": True}, replace=False)
    with pytest.raises(FileExistsError):
        write_private(path, {"prior": False}, replace=False)
    assert json.loads(path.read_text()) == {"prior": True}


def test_loose_permissions_are_not_repaired_while_reading_or_replacing(tmp_path):
    path = tmp_path / "custody.json"
    path.write_text('{"foreign": true}')
    if os.name != "nt":
        path.chmod(0o644)
    for action in (
        lambda: require_private(path),
        lambda: write_private(path, {"foreign": False}),
    ):
        with pytest.raises(PermissionError, match="owner-private"):
            action()
    assert json.loads(path.read_text()) == {"foreign": True}


def test_linked_private_file_is_never_followed(tmp_path):
    original = tmp_path / "original.json"
    write_private(original, {"prior": True})
    linked = tmp_path / "linked.json"
    try:
        linked.symlink_to(original)
    except OSError:
        pytest.skip("host does not permit symlink creation")
    with pytest.raises(PermissionError, match="non-link"):
        write_private(linked, {"prior": False})
    assert json.loads(original.read_text()) == {"prior": True}
