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


def test_lib_tree_matches_ignores_only_relative_build_dir_names(tmp_path: Path):
    """Review finding (PR #4372): the ignored-name check must apply to the
    path RELATIVE to each tree's own root, never the full absolute path.
    Placing the whole comparison under an ancestor directory that happens
    to be named e.g. ``build`` must not make every file "invisible" (which
    would make two genuinely DIFFERENT trees compare as falsely equal)."""
    build_root = tmp_path / "build" / "checkout"
    a = _lib(build_root / "a", content="x = 1\n", version="0.1.0")
    b = _lib(build_root / "b", content="x = 2\n", version="0.1.0")  # genuinely differs
    assert uer.lib_tree_matches(a, b) is False
    # A real build/ SUBDIRECTORY inside the tree is still correctly ignored.
    c = _lib(build_root / "c", content="x = 1\n", version="0.1.0")
    (a / "build").mkdir()
    (a / "build" / "stray.txt").write_text("stray build artifact\n", encoding="utf-8")
    assert uer.lib_tree_matches(a, c) is True


def test_lib_tree_matches_ignores_egg_info_artifacts(tmp_path: Path):
    a = _lib(tmp_path / "a", content="x = 1\n", version="0.1.0")
    b = _lib(tmp_path / "b", content="x = 1\n", version="0.1.0")
    egg = b / "src" / "agent_shared_lib.egg-info"
    egg.mkdir(parents=True)
    (egg / "PKG-INFO").write_text("generated metadata\n", encoding="utf-8")
    assert uer.lib_tree_matches(a, b) is True


def test_lib_tree_matches_ignores_dot_venv_artifacts(tmp_path: Path):
    a = _lib(tmp_path / "a", content="x = 1\n", version="0.1.0")
    b = _lib(tmp_path / "b", content="x = 1\n", version="0.1.0")
    venv = b / ".venv" / "lib"
    venv.mkdir(parents=True)
    (venv / "marker.txt").write_text("generated venv\n", encoding="utf-8")
    assert uer.lib_tree_matches(a, b) is True


def test_uv_editable_problems_rejects_a_symlinked_canonical_lib_root(tmp_path: Path, monkeypatch):
    # A symlinked libs/<lib> that happens to resolve to the SAME target the
    # referenced path also resolves to must still be rejected -- comparing
    # only resolved paths would accept it instead of catching the symlink.
    repo = tmp_path / "repo"
    real_lib = tmp_path / "outside-lib"
    (real_lib / "src/shared_lib").mkdir(parents=True)
    (real_lib / "src/shared_lib/__init__.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "libs").mkdir(parents=True)
    (repo / "libs/shared-lib").symlink_to(real_lib, target_is_directory=True)
    consumer = repo / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(uer, "REPO", repo)
    monkeypatch.setattr(uer, "LIBS_DIR", repo / "libs")
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("is a symlink" in p for p in problems)


def test_find_uv_editable_refs_editable_requires_exact_true(tmp_path: Path):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-a = { path = "../../libs/a", editable = "false" }\n'
        'agent-b = { path = "../../libs/b", editable = 1 }\n'
        'agent-c = { path = "../../libs/c", editable = true }\n',
        encoding="utf-8",
    )
    refs = {name: editable for name, _raw, _lib, editable in uer.find_uv_editable_refs(consumer)}
    assert refs == {"agent-a": False, "agent-b": False, "agent-c": True}


def test_uv_editable_problems_rejects_a_symlinked_consumer_pyproject(tmp_path: Path):
    # find_uv_editable_refs() fails closed on a symlinked pyproject.toml by
    # returning [] -- uv_editable_problems() must not let that look
    # identical to "nothing to validate"; it must surface the symlink
    # itself as an explicit problem.
    real = tmp_path / "outside-pyproject.toml"
    real.write_text(
        "[tool.uv.sources]\nagent-x = { path = \"../../libs/x\", editable = true }\n",
        encoding="utf-8",
    )
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").symlink_to(real)

    assert uer.find_uv_editable_refs(consumer) == []
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("pyproject.toml is a symlink" in p for p in problems)


def test_is_safe_lib_name():
    assert uer.is_safe_lib_name("shared-lib") is True
    assert uer.is_safe_lib_name("") is False
    assert uer.is_safe_lib_name(".") is False
    assert uer.is_safe_lib_name("..") is False
    assert uer.is_safe_lib_name("../outside") is False
    assert uer.is_safe_lib_name("a/b") is False


def test_convert_to_uv_editable_refuses_an_unsafe_lib_name():
    with pytest.raises(SystemExit, match="not a valid lib name"):
        uer.convert_to_uv_editable(
            "alpha", "../outside",
            repo=Path("/nonexistent"), libs_dir=Path("/nonexistent/libs"),
            consumer_dir_of=lambda c: Path("/nonexistent/plugins") / c,
            find_symlinked_ancestor=lambda p, r: None,
            remove_path=lambda p: None,
            is_pointer_copy=lambda p: False,
        )


def test_find_uv_editable_refs_raises_manifest_unreadable_for_malformed_toml(tmp_path: Path):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text("this is not [ valid toml", encoding="utf-8")
    with pytest.raises(uer.ManifestUnreadable):
        uer.find_uv_editable_refs(consumer)


def test_find_uv_editable_refs_returns_empty_for_a_genuinely_absent_manifest(tmp_path: Path):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    assert uer.find_uv_editable_refs(consumer) == []


def test_uv_editable_problems_surfaces_a_malformed_manifest(tmp_path: Path):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text("this is not [ valid toml", encoding="utf-8")
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("could not read/parse" in p for p in problems)


def test_uv_editable_problems_rejects_an_unsafe_lib_name(tmp_path: Path, monkeypatch):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        # raw_path's own final component is ".." -- Path(raw_path).name is
        # ".." too, an unsafe lib name that would let libs_dir / lib escape
        # the intended libs/ tree entirely.
        'agent-x = { path = "../../libs/..", editable = true }\n',
        encoding="utf-8",
    )
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("not a safe lib name" in p for p in problems)


def test_find_uv_editable_refs_raises_manifest_unreadable_for_a_non_string_path(tmp_path: Path):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        "agent-x = { path = 1, editable = true }\n",
        encoding="utf-8",
    )
    with pytest.raises(uer.ManifestUnreadable):
        uer.find_uv_editable_refs(consumer)


def test_lib_tree_matches_false_for_an_unexpected_extra_file(tmp_path: Path):
    a = _lib(tmp_path / "a", content="x = 1\n", version="0.1.0")
    b = _lib(tmp_path / "b", content="x = 1\n", version="0.1.0")
    (b / "LICENSE").write_text("MIT\n", encoding="utf-8")
    assert uer.lib_tree_matches(a, b) is False


def test_find_uv_editable_refs_raises_manifest_unreadable_for_a_non_table_sources(
    tmp_path: Path,
):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv]\n"
        "sources = []\n",
        encoding="utf-8",
    )
    with pytest.raises(uer.ManifestUnreadable, match="is not a table"):
        uer.find_uv_editable_refs(consumer)


def test_find_uv_editable_refs_raises_manifest_unreadable_for_invalid_utf8(tmp_path: Path):
    consumer = tmp_path / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_bytes(b"\xff\xfe not valid utf-8")
    with pytest.raises(uer.ManifestUnreadable):
        uer.find_uv_editable_refs(consumer)


def test_uv_editable_problems_rejects_a_nested_symlink_in_canonical(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    _lib(repo / "libs/shared-lib", content="value = 1\n", version="0.1.0")
    victim = tmp_path / "victim.py"
    victim.write_text("x = 1\n", encoding="utf-8")
    (repo / "libs/shared-lib/src/sneaky.py").symlink_to(victim)
    consumer = repo / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(uer, "REPO", repo)
    monkeypatch.setattr(uer, "LIBS_DIR", repo / "libs")
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("is a symlink" in p for p in problems)


def test_uv_editable_problems_rejects_a_canonical_lib_missing_src(tmp_path: Path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "libs/shared-lib").mkdir(parents=True)  # exists, but no src/ at all
    (repo / "libs/shared-lib/pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = repo / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(uer, "REPO", repo)
    monkeypatch.setattr(uer, "LIBS_DIR", repo / "libs")
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("src does not exist" in p for p in problems)


def test_uv_editable_problems_rejects_a_canonical_lib_missing_pyproject(
    tmp_path: Path, monkeypatch,
):
    repo = tmp_path / "repo"
    (repo / "libs/shared-lib/src/shared_lib").mkdir(parents=True)
    (repo / "libs/shared-lib/src/shared_lib/__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    consumer = repo / "plugins/alpha"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(uer, "REPO", repo)
    monkeypatch.setattr(uer, "LIBS_DIR", repo / "libs")
    problems = uer.uv_editable_problems("alpha", consumer)
    assert any("pyproject.toml is missing" in p for p in problems)


def test_convert_to_uv_editable_uses_the_injected_libs_dir_for_the_relpath(tmp_path: Path):
    # The replacement path must be computed from the SAME libs_dir the
    # conversion validates and removes paths under -- using the module-
    # global LIBS_DIR instead would delete the correct copy while writing
    # a reference relative to a different checkout entirely.
    alt_repo = tmp_path / "alt-repo"
    alt_libs = alt_repo / "libs"
    (alt_libs / "shared-lib/src/shared_lib").mkdir(parents=True)
    (alt_libs / "shared-lib/src/shared_lib/__init__.py").write_text("x = 1\n", encoding="utf-8")
    (alt_libs / "shared-lib/pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer_dir = alt_repo / "plugins/alpha"
    consumer_dir.mkdir(parents=True)
    (consumer_dir / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-shared-lib = { path = "libs/shared-lib" }\n',
        encoding="utf-8",
    )

    copy_dir, relpath = uer.convert_to_uv_editable(
        "alpha", "shared-lib",
        repo=alt_repo, libs_dir=alt_libs,
        consumer_dir_of=lambda c: consumer_dir,
        find_symlinked_ancestor=lambda p, r: None,
        remove_path=lambda p: None,
        is_pointer_copy=lambda p: False,
    )
    assert relpath == "../../libs/shared-lib"
    text = (consumer_dir / "pyproject.toml").read_text()
    assert 'agent-shared-lib = { path = "../../libs/shared-lib", editable = true }' in text
