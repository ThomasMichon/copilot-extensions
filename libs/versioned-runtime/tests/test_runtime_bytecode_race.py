"""Static admission tolerates CPython's completed atomic cache rename only."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import versioned_runtime as admission


@pytest.mark.parametrize("relative,tolerated", [
    ("sample/__pycache__/module.cpython-313.pyc.123456", True),
    ("sample/__pycache__/module.cpython-313.pyc", False),
    ("sample/__pycache__/module.py", False),
    ("sample/__pycache__/module.pyc.invalid", False),
    ("sample/module.cpython-313.pyc.123456", False),
])
def test_atomic_cache_rename_between_enumeration_and_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, relative: str, tolerated: bool,
) -> None:
    site = tmp_path / "Lib" / "site-packages"
    temporary = site.joinpath(*relative.split("/"))
    temporary.parent.mkdir(parents=True)
    temporary.write_bytes(b"compiled")
    destination = temporary.with_name("committed.pyc")
    original = Path.lstat
    renamed = False

    def lstat(path: Path) -> os.stat_result:
        nonlocal renamed
        if path.name == temporary.name and path.parent.name == temporary.parent.name and not renamed:
            temporary.rename(destination)
            renamed = True
        return original(path)

    monkeypatch.setattr(Path, "lstat", lstat)

    def inspect() -> None:
        admission.require_immutable_environment(tmp_path)

    if tolerated:
        inspect()
    else:
        with pytest.raises(FileNotFoundError):
            inspect()
    assert renamed
    assert destination.read_bytes() == b"compiled"


def test_existing_unsafe_cache_temporary_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cache = tmp_path / "Lib" / "site-packages" / "sample" / "__pycache__"
    cache.mkdir(parents=True)
    temporary = cache / "module.cpython-313.pyc.123456"
    temporary.write_bytes(b"retained")
    original = Path.lstat

    def redirected(path: Path) -> os.stat_result:
        info = original(path)
        if path.name == temporary.name:
            values = list(info)
            values[0] = stat.S_IFLNK | stat.S_IMODE(info.st_mode)
            return os.stat_result(values)
        return info

    monkeypatch.setattr(Path, "lstat", redirected)
    with pytest.raises(RuntimeError, match="ordinary files"):
        admission.require_immutable_environment(tmp_path)
    assert temporary.read_bytes() == b"retained"
