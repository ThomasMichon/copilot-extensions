"""Tests for worktree_manager._trusted_pointer_materializer.

This module is a deliberate, hand-maintained COPY of
tools/materialize_main.py's pointer-expansion core (see its own module
docstring for why) -- these tests mirror tools/test_materialize_main.py's
coverage of the same logic, scoped to the subset this module actually
ships (materialize_libs_dir()/find_pointers_in_libs_dir(), not the
whole-repo-checkout materialize()/build()).
"""
from __future__ import annotations

import json
from pathlib import Path

from worktree_manager import _trusted_pointer_materializer as mat


def _canonical_lib(root: Path, lib: str, *, version: str, content: str) -> Path:
    pkg = lib.replace("-", "_")
    d = root / "libs" / lib
    (d / "src" / pkg).mkdir(parents=True)
    (d / "src" / pkg / "__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    return d


def _pointer(libs_dir: Path, lib: str) -> Path:
    d = libs_dir / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / mat.POINTER_NAME).write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": f"libs/{lib}"}) + "\n",
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n',
                                       encoding="utf-8")
    return d


def test_materialize_libs_dir_expands_a_pointer_from_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    libs_dir = tmp_path / "slot" / "libs"
    _pointer(libs_dir, "zdd")

    log = mat.materialize_libs_dir(libs_dir, canonical_root=root)

    assert any(line.startswith("OK") for line in log)
    copy_src = libs_dir / "zdd" / "src" / "zdd" / "__init__.py"
    assert copy_src.read_text() == "real = True\n"
    assert not (libs_dir / "zdd" / mat.POINTER_NAME).exists()
    pp = (libs_dir / "zdd" / "pyproject.toml").read_text()
    assert '"0.1.0-dev5"' in pp


def test_materialize_libs_dir_refuses_a_symlinked_canonical_src(tmp_path: Path):
    root = tmp_path / "repo"
    canon = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    outside = tmp_path / "outside-target"
    (outside / "zdd").mkdir(parents=True)
    (outside / "zdd" / "__init__.py").write_text("smuggled = True\n", encoding="utf-8")
    import shutil
    shutil.rmtree(canon / "src")
    (canon / "src").symlink_to(outside, target_is_directory=True)

    libs_dir = tmp_path / "slot" / "libs"
    pointer_dir = _pointer(libs_dir, "zdd")

    log = mat.materialize_libs_dir(libs_dir, canonical_root=root)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (pointer_dir / mat.POINTER_NAME).exists()


def test_materialize_libs_dir_refuses_a_canonical_lib_with_no_src(tmp_path: Path):
    root = tmp_path / "repo"
    (root / "libs" / "zdd").mkdir(parents=True)  # no src/ subdirectory
    (root / "libs" / "zdd" / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )
    libs_dir = tmp_path / "slot" / "libs"
    pointer_dir = _pointer(libs_dir, "zdd")
    (pointer_dir / "src" / "zdd").mkdir(parents=True)
    (pointer_dir / "src" / "zdd" / "__init__.py").write_text("stub\n", encoding="utf-8")

    log = mat.materialize_libs_dir(libs_dir, canonical_root=root)

    assert any("SKIP" in line and "src not found" in line for line in log)
    assert (pointer_dir / "src" / "zdd" / "__init__.py").read_text() == "stub\n"
    assert (pointer_dir / mat.POINTER_NAME).exists()


def test_materialize_libs_dir_refreshes_a_stale_canonical_tests_directory(tmp_path: Path):
    # A pointer copy that already carries tests/ (vendored at --pointerize
    # time) must have it refreshed from canonical too, the same way its
    # src/ is refreshed -- this is the trusted self-update path's own copy
    # of tools/materialize_main.py's promotion-time tests/ refresh, and
    # needs its own direct assertion here (not just parity coverage) so a
    # regression in this module alone still fails a test even if the
    # parity suite were ever skipped (e.g. outside a full monorepo
    # checkout).
    root = tmp_path / "repo"
    canon = _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    (canon / "tests").mkdir(parents=True)
    (canon / "tests" / "test_thing.py").write_text(
        "def test_it():\n    pass\n", encoding="utf-8"
    )
    libs_dir = tmp_path / "slot" / "libs"
    pointer_dir = _pointer(libs_dir, "zdd")
    (pointer_dir / "tests").mkdir(parents=True)
    (pointer_dir / "tests" / "test_thing.py").write_text(
        "def test_it():\n    assert False  # stale\n", encoding="utf-8"
    )

    log = mat.materialize_libs_dir(libs_dir, canonical_root=root)

    assert any(line.startswith("OK") for line in log)
    refreshed = pointer_dir / "tests" / "test_thing.py"
    assert refreshed.read_text() == "def test_it():\n    pass\n"


def test_find_pointers_in_libs_dir_empty_when_no_libs_dir(tmp_path: Path):
    assert mat.find_pointers_in_libs_dir(tmp_path / "nope") == []


def test_find_pointers_in_libs_dir_finds_a_pointer(tmp_path: Path):
    libs_dir = tmp_path / "libs"
    _pointer(libs_dir, "zdd")
    found = mat.find_pointers_in_libs_dir(libs_dir)
    assert [p.parent.name for p in found] == ["zdd"]
