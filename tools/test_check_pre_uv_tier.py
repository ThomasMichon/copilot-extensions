"""Contracts for the pre-uv tier syntax check (tools/check_pre_uv_tier.py)."""
from __future__ import annotations

from pathlib import Path

import check_pre_uv_tier as tier


def _tree(tmp_path: Path, fragment_body: str) -> Path:
    package = tmp_path / "pkg"
    package.mkdir()
    (package / "entry.py").write_text(
        '_FRAGMENT_FILES = (\n    "_frag_a.py",\n    "_frag_b.py",\n)\nVALUE = 1\n',
        encoding="utf-8",
    )
    (package / "_frag_a.py").write_text("A = 1\n", encoding="utf-8")
    (package / "_frag_b.py").write_text(fragment_body, encoding="utf-8")
    manifest = tmp_path / "manifest.txt"
    manifest.write_text("# comment\npkg/entry.py  # entrypoint\n", encoding="utf-8")
    return manifest


def test_fragments_loaded_by_a_listed_entrypoint_are_checked(tmp_path: Path) -> None:
    manifest = _tree(tmp_path, "B = 2\n")
    failures, count = tier.check(tmp_path, manifest)
    assert failures == []
    assert count == 3


def test_invalid_syntax_in_a_fragment_fails_the_check(tmp_path: Path) -> None:
    manifest = _tree(tmp_path, "def broken(:\n    pass\n")
    failures, _ = tier.check(tmp_path, manifest)
    assert len(failures) == 1
    assert failures[0].startswith("pkg/_frag_b.py:")


def test_missing_fragment_fails_the_check(tmp_path: Path) -> None:
    manifest = _tree(tmp_path, "B = 2\n")
    (tmp_path / "pkg" / "_frag_a.py").unlink()
    failures, _ = tier.check(tmp_path, manifest)
    assert len(failures) == 1
    assert failures[0].startswith("pkg/_frag_a.py: cannot read")


def test_repository_manifest_includes_codespaces_fragments() -> None:
    failures, count = tier.check()
    assert failures == []
    entries = tier.listed_files()
    assert count > len(entries)
