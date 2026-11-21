"""The direct/nested copier and equality guard share generated-file exclusions."""
from __future__ import annotations

import shutil
from pathlib import Path
from types import ModuleType

import pytest

import materialize_main
import nested_uv_editable_ref
import uv_editable_ref


@pytest.mark.parametrize("copier", [materialize_main, nested_uv_editable_ref], ids=["direct", "nested"])
def test_generated_lockfiles_do_not_break_copy_equality(
    tmp_path: Path, copier: ModuleType,
) -> None:
    canonical = tmp_path / "canonical"
    canonical.mkdir()
    (canonical / "pyproject.toml").write_text('[project]\nname = "example"\n', encoding="utf-8")
    package = canonical / "src/example"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    for directory in (canonical, package):
        (directory / "uv.lock").write_text("local resolver output", encoding="utf-8")
    copied = tmp_path / "copied"
    shutil.copytree(canonical, copied, ignore=copier._ignore)
    assert not list(copied.rglob("uv.lock"))
    assert uv_editable_ref.lib_tree_matches(canonical, copied)
    (copied / "unexpected.txt").write_text("must remain visible", encoding="utf-8")
    assert not uv_editable_ref.lib_tree_matches(canonical, copied)
