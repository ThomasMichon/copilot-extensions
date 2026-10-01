"""Tests for tools/materialize_main.py -- the whole-repo pointer materializer."""
from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier
import launch_wrapper_assets_ref as lwar
import materialize_main as mm
import uv_editable_ref as uer


def _canonical_lib(root: Path, lib: str, *, version: str, content: str) -> Path:
    pkg = lib.replace("-", "_")
    d = root / "libs" / lib
    (d / "src" / pkg).mkdir(parents=True, exist_ok=True)
    (d / "src" / pkg / "__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n', encoding="utf-8"
    )
    return d


def _file_pointer(root: Path, plugin: str, rel: str, *, source: str) -> Path:
    """Create a vendored *file* pointer stub at ``plugins/<plugin>/<rel>``."""
    d = root / "plugins" / plugin
    path = d / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<!-- VENDOR_POINTER: source={source} kind=file -->\n\n"
        "This file is a vendored pointer; see the canonical source above.\n",
        encoding="utf-8",
    )
    return path


def _canonical_file(root: Path, rel: str, *, content: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_materialize_expands_file_pointer_from_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/thing.md", content="# Thing\n\nreal content\n")
    pointer = _file_pointer(root, "agent-bridge", "docs/thing.md",
                             source="docs/patterns/thing.md")

    log = mm.materialize(root, canonical_root=root)

    assert any("(file pointer)" in line and line.startswith("OK") for line in log)
    assert pointer.read_text() == "# Thing\n\nreal content\n"


def test_materialize_skips_file_pointer_with_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    pointer = _file_pointer(root, "agent-bridge", "docs/ghost.md",
                             source="docs/patterns/ghost.md")

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "(file pointer)" in line and "not found" in line
               for line in log)
    # Left untouched -- still the stub, since nothing was expanded.
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")


def test_materialize_refuses_a_symlinked_file_pointer(tmp_path: Path):
    # find_file_pointers()'s own is_file() check follows a symlink, and
    # pointer_path.write_bytes() would follow it again -- a symlinked
    # pointer path (or an ancestor between it and dest) would let
    # materialization overwrite the symlink's TARGET outside the
    # snapshot, the same class of gap already fixed for the
    # directory-pointer path.
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/thing.md", content="# Thing\n\nreal content\n")
    victim = root.parent / "outside-victim-pointer.md"
    victim.write_text(
        "<!-- VENDOR_POINTER: source=docs/patterns/thing.md kind=file -->\n\n"
        "This file is a vendored pointer; see the canonical source above.\n",
        encoding="utf-8",
    )
    pointer_path = root / "plugins" / "agent-bridge" / "docs" / "thing.md"
    pointer_path.parent.mkdir(parents=True)
    pointer_path.symlink_to(victim)

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "(file pointer)" in line and "is a symlink" in line
               for line in log)
    # Nothing was overwritten through the symlink.
    assert victim.read_text().startswith("<!-- VENDOR_POINTER:")


def test_materialize_refuses_absolute_source_path(tmp_path: Path):
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret.txt"
    secret.write_text("do not leak\n", encoding="utf-8")
    pointer = _file_pointer(root, "agent-bridge", "docs/evil.md", source=str(secret))

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "escapes the canonical root" in line for line in log)
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")


def test_materialize_refuses_traversal_source_path(tmp_path: Path):
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret.txt"
    secret.write_text("do not leak\n", encoding="utf-8")
    pointer = _file_pointer(root, "agent-bridge", "docs/evil.md",
                             source="../outside-repo-secret.txt")

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "escapes the canonical root" in line for line in log)
    assert pointer.read_text().startswith("<!-- VENDOR_POINTER:")


def test_resolve_within_allows_legitimate_nested_source(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/deep/thing.md", content="ok\n")
    resolved = mm._resolve_within(root, "docs/patterns/deep/thing.md")
    assert resolved == (root / "docs/patterns/deep/thing.md").resolve()


def test_materialize_expands_multiple_file_pointers_for_same_doc(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_file(root, "docs/patterns/shared.md", content="shared doc\n")
    pointers = [
        _file_pointer(root, plugin, "docs/shared.md", source="docs/patterns/shared.md")
        for plugin in ("agent-bridge", "agent-worktrees", "agent-dispatch")
    ]

    log = mm.materialize(root, canonical_root=root)
    assert sum(1 for line in log if line.startswith("OK") and "(file pointer)" in line) == 3
    for pointer in pointers:
        assert pointer.read_text() == "shared doc\n"


def test_find_file_pointers_ignores_non_pointer_files(tmp_path: Path):
    root = tmp_path / "repo"
    _file_pointer(root, "agent-bridge", "docs/real-pointer.md",
                   source="docs/patterns/thing.md")
    (root / "plugins/agent-bridge/docs").mkdir(parents=True, exist_ok=True)
    (root / "plugins/agent-bridge/docs/plain.md").write_text("# Not a pointer\n",
                                                              encoding="utf-8")

    found = mm.find_file_pointers(root)
    assert [p.name for p in found] == ["real-pointer.md"]


def test_build_materializes_file_pointers_end_to_end(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_file(source, "docs/patterns/thing.md", content="canonical\n")
    _file_pointer(source, "agent-bridge", "docs/thing.md", source="docs/patterns/thing.md")

    dest = tmp_path / "dest"
    log = mm.build(dest, source_root=source)

    assert any("(file pointer)" in line and line.startswith("OK") for line in log)
    assert (dest / "plugins/agent-bridge/docs/thing.md").read_text() == "canonical\n"
    # The source checkout itself must never be mutated by build().
    assert (source / "plugins/agent-bridge/docs/thing.md").read_text().startswith(
        "<!-- VENDOR_POINTER:"
    )


def test_main_smoke(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="x\n")
    _uv_editable_consumer(source, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")
    monkeypatch.setattr(mm, "REPO", source)

    dest = tmp_path / "dest"
    code = mm.main(["--dest", str(dest)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Materialized 1 artifact(s)" in out


def _uv_editable_consumer(
    root: Path, consumer_rel: str, dist_name: str, raw_path: str,
) -> Path:
    """Write a consumer ``pyproject.toml`` carrying a `uv`-editable
    canonical-reference entry (``path`` escapes the consumer's own root,
    ``editable = true``)."""
    d = root / consumer_rel
    d.mkdir(parents=True, exist_ok=True)
    (d / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        f'dependencies = ["{dist_name}"]\n'
        "\n"
        "[tool.uv.sources]\n"
        f'{dist_name} = {{ path = "{raw_path}", editable = true }}\n',
        encoding="utf-8",
    )
    return d


def _rewrite_to_local_path(pyproject: Path, *, name: str, raw_path: str, lib: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^([ \t]*(?:"' + re.escape(name) + r'"|\'' + re.escape(name) + r"'|" +
        re.escape(name) + r')\s*=\s*)\{\s*(?:'
        r'path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' + re.escape(raw_path) + r"')"
        r'\s*,\s*editable\s*=\s*true'
        r'|editable\s*=\s*true\s*,\s*path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' +
        re.escape(raw_path) + r"'))\s*\}[ \t]*(?:#.*)?$",
        re.MULTILINE,
    )
    text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "libs/{lib}" }}',
        text,
        count=1,
    )
    assert count == 1, f"{pyproject}: missing top-level entry for {name}"
    pyproject.write_text(text, encoding="utf-8")


def _rewrite_nested_path(pyproject: Path, *, name: str, raw_path: str) -> None:
    text = pyproject.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^([ \t]*(?:"' + re.escape(name) + r'"|\'' + re.escape(name) + r"'|" +
        re.escape(name) + r')\s*=\s*)\{\s*(?:'
        r'path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' + re.escape(raw_path) + r"')"
        r'\s*,\s*editable\s*=\s*true'
        r'|editable\s*=\s*true\s*,\s*path\s*=\s*(?:"' + re.escape(raw_path) + r'"|\'' +
        re.escape(raw_path) + r"'))\s*\}[ \t]*(?:#.*)?$",
        re.MULTILINE,
    )
    text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "{raw_path}" }}',
        text,
        count=1,
    )
    assert count == 1, f"{pyproject}: missing nested entry for {name}"
    pyproject.write_text(text, encoding="utf-8")


def _build_expected_vendored_tree(dest_lib: Path, *, canonical_root: Path) -> None:
    """Independently reconstruct the vendored tree from current canonical libs.

    This intentionally does NOT call the production nested materializer: it
    copies the canonical lib, rewrites nested source entries itself, and recurses
    over nested canonical sibling deps so the expected manifests are an
    independent oracle.
    """

    refs = uer.find_uv_editable_refs(dest_lib)
    for name, raw_path, lib, editable in refs:
        assert editable, f"{dest_lib}: expected editable nested ref for {name}"
        nested_dest = dest_lib.parent.parent / "libs" / lib
        if not nested_dest.exists():
            shutil.copytree(canonical_root / "libs" / lib, nested_dest, ignore=mm._ignore)
            _build_expected_vendored_tree(nested_dest, canonical_root=canonical_root)
        _rewrite_nested_path(dest_lib / "pyproject.toml", name=name, raw_path=raw_path)


def test_find_uv_editable_refs_only_returns_escaping_entries_regardless_of_editable(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd"
    )
    # An in-tree (non-escaping) entry must never be reported.
    (consumer / "pyproject.toml").write_text(
        (consumer / "pyproject.toml").read_text()
        + 'agent-other = { path = "libs/other" }\n',
        encoding="utf-8",
    )
    refs = uer.find_uv_editable_refs(consumer)
    assert refs == [("agent-zdd", "../../libs/zdd", "zdd", True)]


def test_find_uv_editable_refs_includes_a_non_editable_escaping_entry(tmp_path: Path):
    # A caller must SEE this entry (never silently skip it) so promotion
    # can refuse it explicitly rather than shipping the external path
    # unchanged -- see materialize_uv_editable_ref_into's own handling.
    root = tmp_path / "repo"
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd" }\n',
        encoding="utf-8",
    )
    refs = uer.find_uv_editable_refs(consumer)
    assert refs == [("agent-zdd", "../../libs/zdd", "zdd", False)]


def test_materialize_uv_editable_ref_into_copies_full_tree_and_rewrites_entry(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    (root / "libs/zdd/README.md").write_text("# zdd\n", encoding="utf-8")
    (root / "libs/zdd/tests").mkdir(parents=True)
    (root / "libs/zdd/tests/test_zdd.py").write_text("def test_it(): pass\n", encoding="utf-8")
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any(line.startswith("OK") for line in log), log
    dest_lib = consumer / "libs/zdd"
    assert (dest_lib / "src/zdd/__init__.py").read_text() == "real = True\n"
    assert (dest_lib / "README.md").read_text() == "# zdd\n"
    assert (dest_lib / "tests/test_zdd.py").exists()
    assert '"0.1.0-dev5"' in (dest_lib / "pyproject.toml").read_text()

    pp_text = (consumer / "pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in pp_text
    assert "editable" not in pp_text


def test_materialize_uv_editable_ref_into_skips_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-ghost", "../../libs/ghost")
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("does not exist" in line for line in log)
    assert not (consumer / "libs/ghost").exists()


def test_materialize_uv_editable_ref_into_refuses_a_non_editable_entry(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd" }\n',
        encoding="utf-8",
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("missing editable = true" in line for line in log)
    assert not (consumer / "libs/zdd").exists()
    # The pyproject.toml is never touched either -- refused before any write.
    assert "editable" not in (consumer / "pyproject.toml").read_text()


def test_materialize_uv_editable_ref_into_refuses_escaping_canonical_root(tmp_path: Path):
    root = tmp_path / "repo"
    outside = tmp_path / "outside" / "zdd"
    (outside / "src" / "zdd").mkdir(parents=True)
    (outside / "src" / "zdd" / "__init__.py").write_text("evil\n", encoding="utf-8")
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-zdd", "../../../outside/zdd"
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is not canonical_root/libs/zdd" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_path_outside_the_libs_tree(tmp_path: Path):
    # The reviewer's exact concern: an escaping entry that DOES resolve
    # inside canonical_root, but not under canonical_root/libs/<lib> --
    # e.g. another plugin's own tree entirely. A containment check scoped
    # only to "somewhere inside canonical_root" would accept this and copy
    # the OTHER plugin's tree into this consumer's libs/<lib>/, rewriting
    # it as a shared-lib dependency it never was.
    root = tmp_path / "repo"
    (root / "plugins/other-plugin/src/other_plugin").mkdir(parents=True)
    (root / "plugins/other-plugin/src/other_plugin/__init__.py").write_text(
        "x = 1\n", encoding="utf-8"
    )
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-zdd", "../other-plugin"
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is not canonical_root/libs/other-plugin" in line for line in log)
    assert not (consumer / "libs/other-plugin").exists()


def test_materialize_uv_editable_refs_whole_repo(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="x\n")
    _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")
    _uv_editable_consumer(root, "worktree-manager", "agent-zdd", "../libs/zdd")

    log = mm.materialize_uv_editable_refs(root, canonical_root=root)

    assert (root / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py").exists()
    assert (root / "worktree-manager/libs/zdd/src/zdd/__init__.py").exists()
    assert sum(1 for line in log if line.startswith("OK")) == 2


def test_materialize_whole_repo_also_expands_uv_editable_refs(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="x\n")
    _uv_editable_consumer(source, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    dest_dir = tmp_path / "dest"
    log = mm.build(dest_dir, source_root=source)

    assert any(line.startswith("OK") for line in log)
    assert (dest_dir / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py").exists()
    pp_text = (dest_dir / "plugins/agent-bridge/pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in pp_text


def test_materialize_uv_editable_ref_into_preflights_rewrite_before_copying(tmp_path: Path):
    # If the pyproject.toml entry doesn't exactly match the expected form
    # (e.g. hand-edited between authoring and promotion), the rewrite must
    # be refused BEFORE canonical's tree is ever copied in -- never leaving
    # a half-materialized libs/<lib>/ with an un-rewritten pyproject.toml.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        # Deliberately a DIFFERENT raw_path than what find_uv_editable_refs
        # will report reading it back (can't normally happen through the
        # ordinary API, but simulates a rewrite target that vanished
        # between discovery and expansion).
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    # Sabotage the entry's exact text AFTER computing raw_path via a direct
    # call, to exercise the preflight independent of find_uv_editable_refs.
    canonical = root / "libs/zdd"
    dest_lib_dir = consumer / "libs/zdd"
    log_line = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=consumer / "pyproject.toml",
        name="agent-zdd", raw_path="../../libs/nonexistent-raw-path-text", lib="zdd",
    )
    assert "could not find" in log_line
    assert not dest_lib_dir.exists()


def test_materialize_uv_editable_ref_into_refuses_a_malformed_canonical_lib(tmp_path: Path):
    # A canonical directory that exists but has no src/ (or no
    # pyproject.toml) must be refused BEFORE anything is copied -- matching
    # the existing directory/file pointer materializer's own fail-closed
    # behavior for an incomplete canonical lib.
    root = tmp_path / "repo"
    (root / "libs/zdd").mkdir(parents=True)  # exists, but no src/ at all
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("src not found" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_canonical_lib_missing_pyproject(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    (root / "libs/zdd/src/zdd").mkdir(parents=True)
    (root / "libs/zdd/src/zdd/__init__.py").write_text("x = 1\n", encoding="utf-8")
    # No pyproject.toml under canonical.
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("pyproject.toml missing" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_source_pyproject(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    real = tmp_path / "outside-pyproject.toml"
    real.write_text(
        '[tool.uv.sources]\nagent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").symlink_to(real)

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_catches_a_symlinked_canonical_before_resolving(
    tmp_path: Path,
):
    # The symlink-ancestor check must run on the UNRESOLVED, clean
    # canonical_root/libs/<lib> path BEFORE any .resolve() call -- resolving
    # first would silently follow (and erase) the symlink, so a later
    # ancestor check against the already-resolved path could never detect
    # it.
    root = tmp_path / "repo"
    real_lib = tmp_path / "outside-lib"
    (real_lib / "src/zdd").mkdir(parents=True)
    (real_lib / "src/zdd/__init__.py").write_text("evil\n", encoding="utf-8")
    (real_lib / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.0.0"\n', encoding="utf-8"
    )
    (root / "libs").mkdir(parents=True)
    (root / "libs/zdd").symlink_to(real_lib, target_is_directory=True)
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_an_unsafe_lib_name(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = _uv_editable_consumer(
        root, "plugins/agent-bridge", "agent-x", "../../libs/.."
    )
    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("not a safe lib name" in line for line in log)


def test_real_repo_uv_editable_materialization_matches_current_canonical_trees(
    tmp_path: Path,
):
    """Regression coverage for the real converted consumers.

    A historical pre-conversion tree is NOT the faithful baseline once a
    conversion PR also changed canonical README/tests content in the same commit.
    The non-circular comparison is "promotion output now" vs. "what a byte-
    vendored copy from the SAME current canonical lib would look like now" --
    i.e. the materialized tree must match today's canonical ``libs/<lib>`` tree
    byte-for-byte for every real `uv`-editable consumer reference.
    """

    repo = mm.REPO.resolve()
    discovered: list[tuple[str, Path, list[tuple[str, str, str, bool]]]] = []
    for consumer, consumer_dir in uer.iter_consumer_dirs():
        refs = uer.find_uv_editable_refs(consumer_dir)
        if refs:
            discovered.append((consumer, consumer_dir, refs))

    assert discovered, "expected at least one real uv-editable consumer"
    checked: list[str] = []
    actual_root = tmp_path / "actual"

    def _consumer_snapshot_dir(root: Path, consumer: str) -> Path:
        return root / consumer if consumer == "worktree-manager" else root / "plugins" / consumer

    for consumer, consumer_dir, refs in discovered:
        actual_consumer_dir = _consumer_snapshot_dir(actual_root, consumer)
        expected_consumer_dir = _consumer_snapshot_dir(tmp_path / "expected", consumer)
        actual_consumer_dir.mkdir(parents=True, exist_ok=True)
        expected_consumer_dir.mkdir(parents=True, exist_ok=True)
        pyproject_text = (consumer_dir / "pyproject.toml").read_text(encoding="utf-8")
        (actual_consumer_dir / "pyproject.toml").write_text(
            pyproject_text,
            encoding="utf-8",
        )
        (expected_consumer_dir / "pyproject.toml").write_text(
            pyproject_text,
            encoding="utf-8",
        )
        for name, raw_path, lib, editable in refs:
            assert editable, f"{consumer}: expected editable ref for {name}"
            _rewrite_to_local_path(
                expected_consumer_dir / "pyproject.toml", name=name, raw_path=raw_path, lib=lib
            )

    log = mm.materialize(actual_root, canonical_root=repo)
    skips = [line for line in log if line.startswith("SKIP")]
    assert not skips, "; ".join(skips)
    for consumer, _consumer_dir, refs in discovered:
        actual_consumer_dir = _consumer_snapshot_dir(actual_root, consumer)
        expected_consumer_dir = _consumer_snapshot_dir(tmp_path / "expected", consumer)
        assert (actual_consumer_dir / "pyproject.toml").read_text(encoding="utf-8") == (
            expected_consumer_dir / "pyproject.toml"
        ).read_text(encoding="utf-8")
        for _name, _raw_path, lib, _editable in refs:
            expected_lib = expected_consumer_dir / "libs" / lib
            if not expected_lib.exists():
                shutil.copytree(repo / "libs" / lib, expected_lib, ignore=mm._ignore)
                _build_expected_vendored_tree(expected_lib, canonical_root=repo)

            materialized = actual_consumer_dir / "libs" / lib
            assert materialized.is_dir(), f"{consumer}: libs/{lib} was not materialized"
            assert uer.lib_tree_matches(expected_lib, materialized), (
                f"{consumer}: materialized libs/{lib} differs from the current vendored "
                f"reconstruction built from canonical libs/{lib}"
            )
            checked.append(f"{consumer}:{lib}")
    assert checked, "expected at least one materialized consumer/lib pair"


def test_materialize_uv_editable_ref_into_refuses_a_malformed_manifest(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text("this is not [ valid toml", encoding="utf-8")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("could not read/parse" in line for line in log)


def test_materialize_one_uv_editable_ref_accepts_editable_before_path_key_order(tmp_path: Path):
    # find_uv_editable_refs() parses real TOML and accepts either key
    # order -- the rewrite must too, or a valid entry authored with
    # editable-then-path would pass --check yet make promotion emit an
    # unresolvable SKIP.
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { editable = true, path = "../../libs/zdd" }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    assert (dest_lib_dir / "src/zdd/__init__.py").read_text() == "real\n"
    assert 'agent-zdd = { path = "libs/zdd" }' in pyproject.read_text()


def test_materialize_one_uv_editable_ref_scopes_rewrite_to_uv_sources_table(tmp_path: Path):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.other]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n'
        "\n"
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    text = pyproject.read_text()
    # The unrelated table's identical-looking entry survives untouched.
    assert text.count('agent-zdd = { path = "../../libs/zdd", editable = true }') == 1
    assert 'agent-zdd = { path = "libs/zdd" }' in text


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_destination_manifest(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    victim = tmp_path / "outside-victim-pyproject.toml"
    victim.write_text("victim content\n", encoding="utf-8")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    real_pp = tmp_path / "real-pyproject.toml"
    real_pp.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    (consumer / "pyproject.toml").symlink_to(victim)

    # find_uv_editable_refs() reads via the symlink (so the ref is
    # discovered), but the destination-manifest symlink itself must still
    # be refused before ever writing through it.
    victim.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert victim.read_text() == (
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n'
    )
    assert not (consumer / "libs/zdd").exists()


def test_materialize_one_uv_editable_ref_copies_files_outside_the_four_known_subpaths(
    tmp_path: Path,
):
    # lib_tree_matches() (the dev-time conversion's drift gate) compares
    # canonical's COMPLETE tree -- the copy performed here must match that
    # promise, or an extra file (a root-level LICENSE, package data, or
    # anything else) would silently disappear from the promoted snapshot.
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    (canonical / "LICENSE").write_text("MIT\n", encoding="utf-8")
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    assert (dest_lib_dir / "LICENSE").read_text() == "MIT\n"


def test_materialize_one_uv_editable_ref_refuses_a_symlink_anywhere_in_canonical(
    tmp_path: Path,
):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    victim = tmp_path / "victim.txt"
    victim.write_text("victim\n", encoding="utf-8")
    (canonical / "sneaky-link").symlink_to(victim)
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert "is a symlink" in result
    assert not dest_lib_dir.exists()


def test_materialize_uv_editable_ref_into_rewrites_all_aliases_of_the_same_lib(tmp_path: Path):
    # Two different [tool.uv.sources] distribution names pointing at the
    # SAME canonical lib -- the second must be rewritten too, not treated
    # as an "already exists" overwrite conflict (which would leave its
    # escaping reference un-rewritten in the promoted snapshot).
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    consumer = root / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n'
        'agent-zdd-alias = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert sum(1 for line in log if line.startswith("OK")) == 2
    text = (consumer / "pyproject.toml").read_text()
    assert 'agent-zdd = { path = "libs/zdd" }' in text
    assert 'agent-zdd-alias = { path = "libs/zdd" }' in text
    # Only copied once -- the second entry reused the existing dest_lib_dir.
    assert (consumer / "libs/zdd/src/zdd/__init__.py").read_text() == "real\n"


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_dest_consumer_ancestor(
    tmp_path: Path,
):
    # dest_root bounds the destination-side ancestor check all the way to
    # the real snapshot root -- checking only up to dest_consumer_dir
    # itself would miss a symlinked dest/plugins (or dest_consumer_dir
    # itself), which materialize_uv_editable_refs()'s own symlink-
    # following is_dir() discovery could otherwise be redirected through.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    dest = tmp_path / "dest"
    outside = tmp_path / "outside-plugins"
    consumer = outside / "agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest.mkdir(parents=True)
    dest_plugins = dest / "plugins"
    dest_plugins.symlink_to(outside, target_is_directory=True)
    dest_consumer_dir = dest_plugins / "agent-bridge"

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=dest_consumer_dir,
        canonical_root=root, dest_root=dest,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_refs_passes_dest_root_for_the_ancestor_check(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    dest = tmp_path / "dest"
    outside = tmp_path / "outside-plugins"
    consumer = outside / "agent-bridge"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        "[tool.uv.sources]\n"
        'agent-zdd = { path = "../../libs/zdd", editable = true }\n',
        encoding="utf-8",
    )
    dest.mkdir(parents=True)
    (dest / "plugins").symlink_to(outside, target_is_directory=True)

    log = mm.materialize_uv_editable_refs(dest, canonical_root=root)
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_uv_editable_ref_into_refuses_a_symlinked_canonical_root(tmp_path: Path):
    # canonical_root.resolve() must never run before its own is_symlink()
    # is checked -- resolving first discards that fact, so every later
    # _find_symlinked_ancestor() walk (which terminates AT the resolved
    # root) would never inspect canonical_root itself, letting its
    # external target be treated as trusted.
    real_root = tmp_path / "real-repo"
    _canonical_lib(real_root, "zdd", version="0.1.0-dev1", content="real\n")
    root = tmp_path / "repo-symlink"
    root.symlink_to(real_root, target_is_directory=True)
    consumer = _uv_editable_consumer(root, "plugins/agent-bridge", "agent-zdd", "../../libs/zdd")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )
    assert any("is a symlink" in line for line in log)
    assert not (consumer / "libs/zdd").exists()


def test_materialize_one_uv_editable_ref_accepts_a_quoted_key_and_trailing_comment(
    tmp_path: Path,
):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        '"agent-zdd" = { path = "../../libs/zdd", editable = true }  # a comment\n',
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    # The original quoted key form is preserved verbatim (group(1) captures
    # the matched key text as-is); only the value is rewritten.
    assert '"agent-zdd" = { path = "libs/zdd" }' in pyproject.read_text()


def test_materialize_one_uv_editable_ref_accepts_a_single_quoted_path(tmp_path: Path):
    canonical = tmp_path / "libs/zdd"
    (canonical / "src/zdd").mkdir(parents=True)
    (canonical / "src/zdd/__init__.py").write_text("real\n", encoding="utf-8")
    (canonical / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    consumer = tmp_path / "plugins/agent-bridge"
    consumer.mkdir(parents=True)
    pyproject = consumer / "pyproject.toml"
    pyproject.write_text(
        "[tool.uv.sources]\n"
        "agent-zdd = { path = '../../libs/zdd', editable = true }\n",
        encoding="utf-8",
    )
    dest_lib_dir = consumer / "libs/zdd"

    result = mm._materialize_one_uv_editable_ref(
        canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
        name="agent-zdd", raw_path="../../libs/zdd", lib="zdd",
    )
    assert result.startswith("OK"), result
    assert 'agent-zdd = { path = "libs/zdd" }' in pyproject.read_text()


# ── nested `uv`-editable dependency between two canonical libs (PR #4372) ─
#
# A canonical lib (e.g. ssh-manager) can itself depend on another canonical
# lib (e.g. agent-procutil) via its OWN escaping `uv`-editable entry. When a
# consumer materializes ssh-manager, the outer rewrite only fixes the
# CONSUMER's own top-level entry -- the just-copied ssh-manager's own
# nested entry must be fixed up too, or a real release ends up requiring
# the same path both editable and non-editable at once.


def _canonical_lib_with_dependency(
    root: Path, lib: str, *, dep_lib: str, dep_raw_path: str, version: str = "0.1.0-dev1",
) -> Path:
    """A canonical lib whose own ``pyproject.toml`` declares an escaping
    `uv`-editable dependency on another canonical lib."""
    d = _canonical_lib(root, lib, version=version, content="real = True\n")
    (d / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nversion = "{version}"\n'
        f'dependencies = ["{dep_lib}"]\n'
        "\n[tool.uv.sources]\n"
        f'{dep_lib} = {{ path = "{dep_raw_path}", editable = true }}\n',
        encoding="utf-8",
    )
    return d


def test_materialize_uv_editable_refs_fixes_up_a_nested_canonical_dependency(tmp_path: Path):
    root = tmp_path / "repo"
    # agent-procutil: a plain canonical lib, no dependencies of its own.
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    # ssh-manager: depends on agent-procutil via its own escaping entry.
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    # Consumer directly depends on BOTH -- ssh-manager listed first so its
    # materialization (and the nested fixup it triggers) runs before the
    # consumer's own direct agent-procutil entry is processed.
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager", "agent-procutil"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n'
        'agent-procutil = { path = "../../libs/agent-procutil", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert all(not line.startswith("SKIP") for line in log), log

    consumer_text = (consumer / "pyproject.toml").read_text()
    assert 'ssh-manager = { path = "libs/ssh-manager" }' in consumer_text
    assert 'agent-procutil = { path = "libs/agent-procutil" }' in consumer_text
    assert "editable" not in consumer_text

    nested_ssh_manager_pp = (consumer / "libs/ssh-manager/pyproject.toml").read_text()
    assert 'agent-procutil = { path = "../agent-procutil" }' in nested_ssh_manager_pp
    assert "editable" not in nested_ssh_manager_pp

    # The nested dependency materialized to the SAME sibling location the
    # consumer's own direct entry would also use.
    assert (consumer / "libs/agent-procutil/src/agent_procutil/__init__.py").read_text() == (
        "real = True\n"
    )


def test_materialize_uv_editable_refs_nested_dependency_already_materialized(tmp_path: Path):
    """Same scenario, but the consumer's own direct entry for the nested
    dependency is listed FIRST -- the top-level entry materializes
    agent-procutil before ssh-manager's own nested fixup runs, so the
    nested step must rewrite the entry WITHOUT re-copying (or failing on)
    an already-materialized sibling."""
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["agent-procutil", "ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'agent-procutil = { path = "../../libs/agent-procutil", editable = true }\n'
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert all(not line.startswith("SKIP") for line in log), log
    nested_ssh_manager_pp = (consumer / "libs/ssh-manager/pyproject.toml").read_text()
    assert 'agent-procutil = { path = "../agent-procutil" }' in nested_ssh_manager_pp
    assert "editable" not in nested_ssh_manager_pp


def test_materialize_nested_uv_editable_refs_refuses_a_missing_editable_true(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    # ssh-manager's own nested entry is missing editable = true.
    d = _canonical_lib(root, "ssh-manager", version="0.1.0-dev1", content="real = True\n")
    (d / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev1"\n'
        'dependencies = ["agent-procutil"]\n'
        "\n[tool.uv.sources]\n"
        'agent-procutil = { path = "../agent-procutil" }\n',
        encoding="utf-8",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any(
        "missing editable = true" in line for line in log
    ), log


def test_materialize_nested_uv_editable_refs_refuses_a_path_traversal_raw_path(
    tmp_path: Path,
):
    """Review finding (PR #4372): a nested entry whose ``raw_path`` does
    NOT resolve to the expected sibling ``<consumer>/libs/<nested_lib>``
    (e.g. ``../../plugins/other``, escaping into an unrelated project)
    must be refused outright -- accepting it would copy canonical's
    `<nested_lib>` content into that OTHER location while the manifest
    still points at the wrong path, silently shipping the wrong project."""
    root = tmp_path / "repo"
    _canonical_lib(root, "other-project", version="0.1.0-dev1", content="unrelated\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="other-project",
        dep_raw_path="../../plugins/other",  # escapes the expected sibling shape
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("which is not" in line for line in log), log
    assert not (root / "plugins" / "other").exists()


def test_materialize_nested_uv_editable_refs_refuses_a_symlinked_canonical(tmp_path: Path):
    """Review finding (PR #4372): the nested materializer must check the
    UNRESOLVED canonical path for a symlinked ancestor BEFORE ever calling
    ``.resolve()`` on it -- resolving first would silently follow (and
    erase) the symlink, letting `copytree` import files from outside the
    trusted canonical tree during release materialization."""
    root = tmp_path / "repo"
    outside = root.parent / "outside-target"
    (outside / "agent-procutil").mkdir(parents=True)
    (outside / "agent-procutil" / "src").mkdir()
    (outside / "agent-procutil" / "src" / "smuggled.py").write_text(
        "smuggled = True\n", encoding="utf-8"
    )
    (root / "libs").mkdir(parents=True)
    (root / "libs" / "agent-procutil").symlink_to(
        outside / "agent-procutil", target_is_directory=True
    )
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is a symlink" in line for line in log), log
    assert not (consumer / "libs" / "agent-procutil").exists()


def test_materialize_nested_uv_editable_refs_refuses_a_non_directory_sibling(tmp_path: Path):
    """Review finding (PR #4372): a pre-existing REGULAR FILE at the
    expected sibling path must never be silently accepted as
    "already materialized" -- rewriting the manifest over it would ship a
    dependency the promoted package can't actually import."""
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )
    # A stray regular file already sits where agent-procutil should land.
    (consumer / "libs").mkdir(parents=True)
    (consumer / "libs" / "agent-procutil").write_text("not a directory\n", encoding="utf-8")

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is not a directory" in line for line in log), log
    ssh_manager_pp = (consumer / "libs/ssh-manager/pyproject.toml").read_text()
    assert "editable = true" in ssh_manager_pp


def test_materialize_nested_uv_editable_refs_refuses_stale_mismatched_content(tmp_path: Path):
    """Review finding (PR #4372): a pre-existing directory at the expected
    sibling path whose content does NOT match canonical (stale from an
    earlier release) must never be silently accepted as "already
    materialized" -- only a byte-identical directory may be treated as
    equivalent to a fresh copy."""
    root = tmp_path / "repo"
    _canonical_lib(root, "agent-procutil", version="0.2.0-dev1", content="real = True\n")
    _canonical_lib_with_dependency(
        root, "ssh-manager", dep_lib="agent-procutil", dep_raw_path="../agent-procutil",
    )
    consumer = root / "plugins/agent-ssh"
    consumer.mkdir(parents=True)
    (consumer / "pyproject.toml").write_text(
        '[project]\nname = "consumer"\nversion = "1.0.0"\n'
        'dependencies = ["ssh-manager"]\n'
        "\n[tool.uv.sources]\n"
        'ssh-manager = { path = "../../libs/ssh-manager", editable = true }\n',
        encoding="utf-8",
    )
    # A stale prior copy of agent-procutil already sits at the sibling
    # location, with content that no longer matches canonical.
    stale_dir = consumer / "libs" / "agent-procutil" / "src" / "agent_procutil"
    stale_dir.mkdir(parents=True)
    (stale_dir / "__init__.py").write_text("stale = True\n", encoding="utf-8")
    (consumer / "libs/agent-procutil/pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev-stale"\n', encoding="utf-8"
    )

    log = mm.materialize_uv_editable_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("does not match canonical" in line for line in log), log
    # The stale content is untouched, not silently overwritten.
    assert (stale_dir / "__init__.py").read_text() == "stale = True\n"


def test_materialize_installer_engine_ref_into_copies_and_rewrites(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (consumer / "scripts" / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n",
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert sum(1 for line in log if line.startswith("OK")) == 2
    assert (consumer / "scripts" / "installer-engine.sh").read_text() == "# canonical sh\n"
    assert (consumer / "scripts" / "installer-engine.ps1").read_text() == "# canonical ps1\n"
    assert (consumer / "scripts" / "install.sh").read_text(encoding="utf-8").splitlines() == [
        "#!/usr/bin/env bash",
        '. "$SCRIPT_DIR/installer-engine.sh"',
    ]
    assert (consumer / "scripts" / "install.ps1").read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')"
    ]


def test_materialize_installer_engine_ref_into_reports_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("canonical source missing" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_refuses_an_escaping_reference(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../outside/installer-engine.sh"\n',
        encoding="utf-8",
    )
    (root / "outside").mkdir()
    (root / "outside" / "installer-engine.sh").write_text("# nope\n", encoding="utf-8")

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is not libs/installer-engine/installer-engine.sh" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_refuses_a_symlinked_canonical_ancestor(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    real_engine = outside / "installer-engine"
    real_engine.mkdir()
    (real_engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (real_engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")
    (root / "libs").mkdir(parents=True)
    (root / "libs" / "installer-engine").symlink_to(real_engine, target_is_directory=True)
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("is a symlink -- refusing" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_refuses_duplicate_source_lines(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("expected exactly one" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_rewrite_to_local_refuses_duplicate_source_lines(tmp_path: Path):
    script = tmp_path / "install.sh"
    script.write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n',
        encoding="utf-8",
    )

    assert ier.rewrite_to_local(script, "sh") is False
    assert script.read_text(encoding="utf-8").count("../../../libs/installer-engine") == 2


def test_rewrite_to_local_preserves_following_comment_lines(tmp_path: Path):
    sh_script = tmp_path / "install.sh"
    sh_script.write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        "# keep me\n",
        encoding="utf-8",
    )
    ps1_script = tmp_path / "install.ps1"
    ps1_script.write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n"
        "# keep me\n",
        encoding="utf-8",
    )

    assert ier.rewrite_to_local(sh_script, "sh") is True
    assert ier.rewrite_to_local(ps1_script, "ps1") is True
    assert sh_script.read_text(encoding="utf-8").splitlines() == [
        '. "$SCRIPT_DIR/installer-engine.sh"',
        "# keep me",
    ]
    assert ps1_script.read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')",
        "# keep me",
    ]


def test_rewrite_to_local_preserves_trailing_same_line_comments(tmp_path: Path):
    sh_script = tmp_path / "install.sh"
    sh_script.write_text(
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh" # keep me\n',
        encoding="utf-8",
    )
    ps1_script = tmp_path / "install.ps1"
    ps1_script.write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1') # keep me\n",
        encoding="utf-8",
    )

    assert ier.rewrite_to_local(sh_script, "sh") is True
    assert ier.rewrite_to_local(ps1_script, "ps1") is True
    assert sh_script.read_text(encoding="utf-8").splitlines() == [
        '. "$SCRIPT_DIR/installer-engine.sh" # keep me'
    ]
    assert ps1_script.read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1') # keep me"
    ]


def test_materialize_installer_engine_ref_into_skips_malformed_local_reference_for_registered_adopter(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n. "$SCRIPT_DIR/missing/installer-engine.sh"\n',
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert any("neither the exact local form" in line for line in log)
    assert not (consumer / "scripts" / "installer-engine.sh").exists()


def test_materialize_installer_engine_ref_into_preserves_following_comment_lines(tmp_path: Path):
    root = tmp_path / "repo"
    engine = root / "libs" / "installer-engine"
    engine.mkdir(parents=True)
    (engine / "installer-engine.sh").write_text("# canonical sh\n", encoding="utf-8")
    (engine / "installer-engine.ps1").write_text("# canonical ps1\n", encoding="utf-8")
    consumer = root / "plugins" / "agent-pull-requests"
    (consumer / "scripts").mkdir(parents=True)
    (consumer / "scripts" / "install.sh").write_text(
        '#!/usr/bin/env bash\n'
        '. "$SCRIPT_DIR/../../../libs/installer-engine/installer-engine.sh"\n'
        "# keep me\n",
        encoding="utf-8",
    )
    (consumer / "scripts" / "install.ps1").write_text(
        ". (Join-Path $PSScriptRoot '..\\..\\..\\libs\\installer-engine\\installer-engine.ps1')\n"
        "# keep me\n",
        encoding="utf-8",
    )

    log = mm.materialize_installer_engine_ref_into(
        source_consumer_dir=consumer, dest_consumer_dir=consumer, canonical_root=root,
    )

    assert sum(1 for line in log if line.startswith("OK")) == 2
    assert (consumer / "scripts" / "install.sh").read_text(encoding="utf-8").splitlines() == [
        "#!/usr/bin/env bash",
        '. "$SCRIPT_DIR/installer-engine.sh"',
        "# keep me",
    ]
    assert (consumer / "scripts" / "install.ps1").read_text(encoding="utf-8").splitlines() == [
        ". (Join-Path $PSScriptRoot 'installer-engine.ps1')",
        "# keep me",
    ]


def test_materialize_launch_wrapper_assets_into_copies_manifested_files(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    source_dir = root / "worktree-manager" / "bin"
    source_dir.mkdir(parents=True)
    for name, content in {
        "launch-session.sh": "#!/usr/bin/env bash\n",
        "pane-wrapper.sh": "#!/usr/bin/env bash\n",
        "session-options.sh": "session opts\n",
    }.items():
        (source_dir / name).write_text(content, encoding="utf-8")
    plugin = root / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / lwar.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "worktree-manager/bin",
                "files": ["launch-session.sh", "pane-wrapper.sh", "session-options.sh"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    dest_plugin = root / "preview" / "plugins" / "agent-worktrees"
    dest_plugin.mkdir(parents=True)

    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=plugin,
        dest_consumer_dir=dest_plugin,
        canonical_root=root,
        dest_root=root / "preview",
    )

    assert sum(1 for line in log if line.startswith("OK")) == 3
    assert (dest_plugin / "bin" / "launch-session.sh").read_text() == "#!/usr/bin/env bash\n"
    assert (dest_plugin / "bin" / "pane-wrapper.sh").read_text() == "#!/usr/bin/env bash\n"
    assert (dest_plugin / "bin" / "session-options.sh").read_text() == "session opts\n"


def test_materialize_launch_wrapper_assets_into_reports_missing_canonical_file(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    (root / "worktree-manager" / "bin").mkdir(parents=True)
    plugin = root / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / lwar.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "worktree-manager/bin",
                "files": ["launch-session.sh"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dest_plugin = root / "preview" / "plugins" / "agent-worktrees"
    dest_plugin.mkdir(parents=True)

    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=plugin,
        dest_consumer_dir=dest_plugin,
        canonical_root=root,
        dest_root=root / "preview",
    )

    assert any("canonical source missing" in line for line in log)
    assert not (dest_plugin / "bin" / "launch-session.sh").exists()


def test_materialize_launch_wrapper_assets_into_refuses_escaping_source_dir(
    tmp_path: Path,
):
    root = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "launch-session.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
    plugin = root / "plugins" / "agent-worktrees"
    plugin.mkdir(parents=True)
    (plugin / lwar.MANIFEST_NAME).write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.launch-wrapper-assets",
                "version": 1,
                "canonicalDir": "../outside",
                "files": ["launch-session.sh"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    dest_plugin = root / "preview" / "plugins" / "agent-worktrees"
    dest_plugin.mkdir(parents=True)

    log = mm.materialize_launch_wrapper_assets_into(
        source_consumer_dir=plugin,
        dest_consumer_dir=dest_plugin,
        canonical_root=root,
        dest_root=root / "preview",
    )

    assert any("canonicalDir must be a non-empty string" in line for line in log)
    assert not (dest_plugin / "bin" / "launch-session.sh").exists()
