"""Inventory metadata costs and indirect-path refusal at the same native boundary."""
from __future__ import annotations

from collections import Counter
import ctypes
import hashlib
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest

from worktree_manager import self_install

self_install._runtime_installer()
from installer_dependency_config import native_retained_path  # noqa: E402
import runtime_uninstall as removal  # noqa: E402


def test_inventory_checks_ancestors_and_artifact_snapshots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = tmp_path / "slot" / ".venv" / "Lib" / "site-packages" / "sample.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"VALUE = 42\n")
    original_lstat, original_lexists = Path.lstat, os.path.lexists
    reads: Counter[str] = Counter()

    def inspect(path: Path) -> os.stat_result:
        reads[os.path.normcase(native_retained_path(path))] += 1
        return original_lstat(path)

    def exists(path: str) -> bool:
        reads[os.path.normcase(native_retained_path(path))] += 1
        return original_lexists(path)

    monkeypatch.setattr(Path, "lstat", inspect)
    monkeypatch.setattr(os.path, "lexists", exists)
    if os.name == "nt":
        original_attributes = removal._windows_file_attributes()

        def attributes(path: str) -> int:
            reads[os.path.normcase(native_retained_path(path))] += 1
            return original_attributes(path)

        monkeypatch.setattr(removal, "_windows_file_attributes", lambda: attributes)
    observed = removal._state(artifact)
    assert observed["digest"] == hashlib.sha256(b"VALUE = 42\n").hexdigest()
    expected: Counter[str] = Counter({
        os.path.normcase(native_retained_path(path)): 1 if os.name == "nt" else 2
        for path in artifact.parents
    })
    expected[os.path.normcase(native_retained_path(artifact))] = 3
    assert reads == expected


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_inventory_refuses_indirect_ancestors_before_reading_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    artifact = tmp_path / "slot" / "sample.py"
    artifact.parent.mkdir()
    artifact.write_bytes(b"unchanged")
    original = Path.lstat
    indirect = os.path.normcase(native_retained_path(artifact.parent))
    inspected: list[str] = []

    def inspect(path: Path) -> os.stat_result | SimpleNamespace:
        inspected.append(os.path.normcase(native_retained_path(path)))
        if inspected[-1] == indirect:
            return SimpleNamespace(
                st_mode=stat.S_IFLNK if kind == "symlink" else stat.S_IFDIR,
                st_file_attributes=0x400 if kind == "reparse" else 0,
            )
        return original(path)

    monkeypatch.setattr(Path, "lstat", inspect)
    if os.name == "nt":
        original_attributes = removal._windows_file_attributes()

        def attributes(path: str) -> int:
            inspected.append(os.path.normcase(native_retained_path(path)))
            return 0x400 if inspected[-1] == indirect else original_attributes(path)

        monkeypatch.setattr(removal, "_windows_file_attributes", lambda: attributes)
    with pytest.raises(ValueError, match="Indirect removal path refused"):
        removal._state(artifact)
    assert os.path.normcase(native_retained_path(artifact)) not in inspected
    assert artifact.read_bytes() == b"unchanged"


@pytest.mark.skipif(os.name != "nt", reason="Windows attribute query errors")
@pytest.mark.parametrize("error", [2, 3, 5])
def test_inventory_attributes_skip_only_missing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, error: int,
) -> None:
    target = tmp_path / "absent"
    original = removal._windows_file_attributes()

    def attributes(path: str) -> int:
        return 0xFFFFFFFF if Path(path).name == target.name else original(path)

    monkeypatch.setattr(removal, "_windows_file_attributes", lambda: attributes)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: error)
    if error in (2, 3):
        assert removal._plain(target) == target
    else:
        with pytest.raises(OSError):
            removal._plain(target)
    assert not target.exists()
