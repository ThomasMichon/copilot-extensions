"""Tests for tools/uv_editable_ref.py -- the shared `uv`-editable
canonical-reference helpers used by both sync-vendored-libs.py (dev-time
conversion + drift guard) and materialize_main.py/preview_release.py
(promotion-time rewriter)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uv_editable_ref as uer


def test_uv_editable_relpath_is_always_forward_slashed(tmp_path: Path, monkeypatch):
    # os.path.relpath() returns native (backslash) separators on Windows,
    # which would embed invalid escapes into the TOML basic string this
    # value gets written into. Force a Windows-shaped relpath via
    # monkeypatching os.path.relpath itself (portable regardless of the
    # host OS actually running this test) and confirm the result is still
    # forward-slashed.
    monkeypatch.setattr(uer.os.path, "relpath", lambda *a, **k: "..\\..\\libs\\shared-lib")
    result = uer.uv_editable_relpath(tmp_path / "plugins/alpha", "shared-lib")
    assert result == "../../libs/shared-lib"
    assert "\\" not in result


def test_uv_editable_relpath_matches_plain_relpath_on_posix(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(uer, "LIBS_DIR", tmp_path / "repo/libs")
    consumer = tmp_path / "repo/plugins/alpha"
    consumer.mkdir(parents=True)
    (tmp_path / "repo/libs/shared-lib").mkdir(parents=True)
    result = uer.uv_editable_relpath(consumer, "shared-lib")
    assert result == "../../libs/shared-lib"


def test_rewrite_scoped_to_uv_sources_table_only(tmp_path: Path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        "[project]\nname = \"x\"\n\n"
        "[tool.other]\n"
        'value = { path = "libs/shared-lib" }\n\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "libs/shared-lib" }\n',
        encoding="utf-8",
    )
    uer.rewrite_uv_source_to_editable(pp, "shared-lib", "../../libs/shared-lib")
    text = pp.read_text()
    assert 'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }' in text
    assert 'value = { path = "libs/shared-lib" }' in text


def test_can_rewrite_uv_source_to_editable_true_when_present(tmp_path: Path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        "[tool.uv.sources]\nagent-shared-lib = { path = \"libs/shared-lib\" }\n",
        encoding="utf-8",
    )
    assert uer.can_rewrite_uv_source_to_editable(pp, "shared-lib") is True


def test_can_rewrite_uv_source_to_editable_false_when_absent(tmp_path: Path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text("[project]\nname = \"x\"\n", encoding="utf-8")
    assert uer.can_rewrite_uv_source_to_editable(pp, "shared-lib") is False


def test_can_rewrite_uv_source_to_editable_ignores_a_match_outside_the_table(tmp_path: Path):
    pp = tmp_path / "pyproject.toml"
    pp.write_text(
        '[tool.other]\nvalue = { path = "libs/shared-lib" }\n\n'
        "[tool.uv.sources]\n"
        'agent-other-lib = { path = "libs/other-lib" }\n',
        encoding="utf-8",
    )
    assert uer.can_rewrite_uv_source_to_editable(pp, "shared-lib") is False


def _lib(root: Path, *, content: str, version: str, readme: str = "# doc\n") -> Path:
    d = root
    (d / "src/shared_lib").mkdir(parents=True)
    (d / "src/shared_lib/__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    (d / "README.md").write_text(readme, encoding="utf-8")
    return d


def test_lib_tree_matches_true_for_identical_trees(tmp_path: Path):
    a = _lib(tmp_path / "a", content="x = 1\n", version="0.1.0")
    b = _lib(tmp_path / "b", content="x = 1\n", version="0.1.0")
    assert uer.lib_tree_matches(a, b) is True


@pytest.mark.parametrize("what", ["readme", "version", "src"])
def test_lib_tree_matches_false_when_any_piece_differs(tmp_path: Path, what: str):
    a = _lib(tmp_path / "a", content="x = 1\n", version="0.1.0")
    kwargs = {"content": "x = 1\n", "version": "0.1.0"}
    if what == "readme":
        kwargs["readme"] = "# different\n"
    elif what == "version":
        kwargs["version"] = "0.2.0"
    elif what == "src":
        kwargs["content"] = "x = 2\n"
    b = _lib(tmp_path / "b", **kwargs)
    assert uer.lib_tree_matches(a, b) is False


def test_lib_tree_matches_false_when_only_copy_has_tests(tmp_path: Path):
    a = _lib(tmp_path / "a", content="x = 1\n", version="0.1.0")
    b = _lib(tmp_path / "b", content="x = 1\n", version="0.1.0")
    (b / "tests").mkdir()
    (b / "tests/test_it.py").write_text("def test_it(): pass\n", encoding="utf-8")
    assert uer.lib_tree_matches(a, b) is False
