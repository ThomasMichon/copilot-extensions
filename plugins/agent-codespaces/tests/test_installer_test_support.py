"""Complete, fresh snapshot manifests without repeated relative-path parsing."""

from __future__ import annotations

import hashlib
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from .installer_test_support import snapshot_tree_manifest


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="installer-manifest-") as directory:
        yield Path(directory)


def test_manifest_records_complete_contents_without_reparsing_each_key(tmp_path, monkeypatch):
    root = tmp_path / "payload"
    (root / "nested" / "empty-directory").mkdir(parents=True)
    binary = bytes(range(256))
    (root / "nested" / ".binary").write_bytes(binary)
    (root / "empty").write_bytes(b"")
    original = Path.relative_to

    def relative_to(path, *args, **kwargs):
        if path == root or root in path.parents:
            raise AssertionError("Manifest keys must be accumulated during the walk")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "relative_to", relative_to)
    assert snapshot_tree_manifest(root) == {
        ".": ("directory", ""),
        "empty": ("file", hashlib.sha256(b"").hexdigest()),
        "nested": ("directory", ""),
        "nested/.binary": ("file", hashlib.sha256(binary).hexdigest()),
        "nested/empty-directory": ("directory", ""),
    }


def test_manifest_rereads_bytes_and_membership_on_every_call(tmp_path):
    root = tmp_path / "payload"
    root.mkdir()
    content = root / "content"
    content.write_bytes(b"original")
    before = snapshot_tree_manifest(root)
    content.write_bytes(b"modified")
    (root / "added").write_bytes(b"new")
    after = snapshot_tree_manifest(root)
    assert after["content"] != before["content"]
    assert after["added"] == ("file", hashlib.sha256(b"new").hexdigest())
    content.unlink()
    assert "content" not in snapshot_tree_manifest(root)


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_manifest_does_not_walk_a_live_link_root(tmp_path, monkeypatch, kind):
    root = tmp_path / "payload"
    root.mkdir()
    (root / "not-followed").write_bytes(b"private")
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == root:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFDIR,
                st_file_attributes=0 if kind == "symlink" else 0x400,
            )
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr("os.readlink", lambda path: "declared-target")
    assert snapshot_tree_manifest(root) == {".": ("link", "declared-target")}


def test_manifest_checks_live_child_link_metadata_before_reading(tmp_path, monkeypatch):
    root = tmp_path / "payload"
    root.mkdir()
    child = root / "child"
    child.write_bytes(b"original")
    original_lstat = Path.lstat
    original_read = Path.read_bytes
    linked = False

    def lstat(path, *args, **kwargs):
        if linked and path == child:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original_lstat(path, *args, **kwargs)

    def read_bytes(path, *args, **kwargs):
        if linked and path == child:
            raise AssertionError("A changed link must not be followed")
        return original_read(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    monkeypatch.setattr("os.readlink", lambda path: "changed-target")
    assert snapshot_tree_manifest(root)["child"][0] == "file"
    linked = True
    assert snapshot_tree_manifest(root)["child"] == ("link", "changed-target")
