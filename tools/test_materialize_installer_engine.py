"""Live ancestor checks without repeated resolution of ordinary descendants."""

from __future__ import annotations

import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

import materialize_installer_engine as mie


@pytest.fixture
def tmp_path():
    with tempfile.TemporaryDirectory(prefix="engine-ancestor-") as directory:
        yield Path(directory)


@pytest.mark.parametrize("exists", [False, True])
def test_ordinary_descendant_resolves_only_the_root(tmp_path, monkeypatch, exists):
    root = tmp_path.resolve()
    candidate = root / "plugins" / "example" / "scripts" / "asset.py"
    candidate.parent.mkdir(parents=True)
    if exists:
        candidate.write_bytes(b"payload")
    original = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert mie._find_symlinked_ancestor(candidate, root) is None
    assert calls == [root]


@pytest.mark.parametrize("linked", ["leaf", "parent"])
def test_ordinary_descendant_keeps_live_symlink_refusal(tmp_path, monkeypatch, linked):
    root = tmp_path.resolve()
    candidate = root / "directory" / "asset.py"
    candidate.parent.mkdir()
    candidate.write_bytes(b"payload")
    forbidden = candidate if linked == "leaf" else candidate.parent
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == forbidden:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert mie._find_symlinked_ancestor(candidate, root) == forbidden


def test_reparse_metadata_retains_resolved_ancestor_walk(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    candidate = root / "directory" / "asset.py"
    candidate.parent.mkdir()
    candidate.write_bytes(b"payload")
    original_lstat = Path.lstat
    original_resolve = Path.resolve
    calls = []
    monkeypatch.setattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400, raising=False)

    def lstat(path, *args, **kwargs):
        if path == candidate.parent:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(path, *args, **kwargs)

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    monkeypatch.setattr(Path, "resolve", resolve)
    assert mie._find_symlinked_ancestor(candidate, root) is None
    assert candidate in calls


def test_relative_path_retains_resolved_ancestor_walk(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    monkeypatch.chdir(root)
    candidate = Path("directory") / "asset.py"
    candidate.parent.mkdir()
    candidate.write_bytes(b"payload")
    original = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert mie._find_symlinked_ancestor(candidate, root) is None
    assert candidate in calls


@pytest.mark.parametrize("kind", ["outside", "dotdot"])
def test_nonordinary_path_retains_resolved_ancestor_walk(tmp_path, monkeypatch, kind):
    root = tmp_path.resolve() / "root"
    root.mkdir()
    if kind == "outside":
        candidate = root.parent / "outside" / "asset.py"
        candidate.parent.mkdir()
    else:
        (root / "directory").mkdir()
        candidate = root / "directory" / ".." / "asset.py"
    candidate.write_bytes(b"payload")
    original = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    assert mie._find_symlinked_ancestor(candidate, root) is None
    assert candidate in calls


def test_ancestor_metadata_is_fresh_on_every_call(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    candidate = root / "directory" / "asset.py"
    candidate.parent.mkdir()
    candidate.write_bytes(b"payload")
    original = Path.lstat
    linked = False

    def lstat(path, *args, **kwargs):
        if linked and path == candidate.parent:
            return SimpleNamespace(st_mode=stat.S_IFLNK, st_file_attributes=0)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    assert mie._find_symlinked_ancestor(candidate, root) is None
    linked = True
    assert mie._find_symlinked_ancestor(candidate, root) == candidate.parent


def test_metadata_permission_failure_is_not_accepted(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    candidate = root / "asset.py"
    original = Path.lstat

    def lstat(path, *args, **kwargs):
        if path == candidate:
            raise PermissionError("metadata refused")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(PermissionError, match="metadata refused"):
        mie._find_symlinked_ancestor(candidate, root)
