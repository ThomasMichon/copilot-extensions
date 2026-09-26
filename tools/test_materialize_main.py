"""Tests for tools/materialize_main.py -- the whole-repo pointer materializer."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import materialize_main as mm


def _pointer(root: Path, plugin: str, lib: str) -> Path:
    d = root / "plugins" / plugin / "libs" / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": f"libs/{lib}"}) + "\n",
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n',
                                       encoding="utf-8")
    return d


def _canonical_lib(root: Path, lib: str, *, version: str, content: str) -> Path:
    d = root / "libs" / lib
    (d / "src" / lib.replace("-", "_")).mkdir(parents=True, exist_ok=True)
    (d / "src" / lib.replace("-", "_") / "__init__.py").write_text(content, encoding="utf-8")
    (d / "pyproject.toml").write_text(f'[project]\nname = "x"\nversion = "{version}"\n',
                                       encoding="utf-8")
    return d


def test_materialize_expands_pointer_from_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    _pointer(root, "agent-bridge", "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any(line.startswith("OK") for line in log)
    copy_src = root / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py"
    assert copy_src.read_text() == "real = True\n"
    assert not (root / "plugins/agent-bridge/libs/zdd/VENDOR_POINTER.json").exists()
    pp = (root / "plugins/agent-bridge/libs/zdd/pyproject.toml").read_text()
    assert '"0.1.0-dev5"' in pp


def test_materialize_expands_canonical_tests_alongside_src(tmp_path: Path):
    # A pointer copy vendors tests/ from canonical too (--pointerize) --
    # promotion-time expansion must refresh it the same way it refreshes
    # src/, otherwise a canonical test change after pointerizing would ship
    # a stale tests/ tree into main.
    root = tmp_path / "repo"
    canonical = _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    (canonical / "tests").mkdir(parents=True)
    (canonical / "tests" / "test_zdd.py").write_text("def test_it():\n    pass\n",
                                                       encoding="utf-8")
    pointer_dir = _pointer(root, "agent-bridge", "zdd")
    # The pointer copy's own (now-stale) tests/ predates the canonical edit.
    (pointer_dir / "tests").mkdir(parents=True)
    (pointer_dir / "tests" / "test_zdd.py").write_text("def test_it():\n    assert False\n",
                                                        encoding="utf-8")

    mm.materialize(root, canonical_root=root)

    refreshed = pointer_dir / "tests" / "test_zdd.py"
    assert refreshed.read_text() == "def test_it():\n    pass\n"


def test_materialize_never_introduces_tests_a_copy_never_vendored(tmp_path: Path):
    # A pointer copy that deliberately never vendored tests/ (--pointerize
    # chose not to, or a copy was pointerized before tests/ vendoring
    # existed) must not gain one unilaterally just because canonical has
    # one -- introducing new content beyond what a copy already committed
    # to is out of scope for a refresh.
    root = tmp_path / "repo"
    canonical = _canonical_lib(root, "zdd", version="0.1.0-dev5", content="real = True\n")
    (canonical / "tests").mkdir(parents=True)
    (canonical / "tests" / "test_zdd.py").write_text("def test_it():\n    pass\n",
                                                       encoding="utf-8")
    pointer_dir = _pointer(root, "agent-bridge", "zdd")  # no local tests/ at all

    mm.materialize(root, canonical_root=root)

    assert not (pointer_dir / "tests").exists()


def test_materialize_skips_missing_canonical(tmp_path: Path):
    root = tmp_path / "repo"
    _pointer(root, "agent-bridge", "ghost-lib")  # no libs/ghost-lib/ exists

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "not found" in line for line in log)
    # Pointer must be left in place since nothing was expanded.
    assert (root / "plugins/agent-bridge/libs/ghost-lib/VENDOR_POINTER.json").exists()


def test_materialize_refuses_a_symlinked_pointer_directory_pointing_inside_root(tmp_path: Path):
    # A pointer directory symlinked to ANOTHER directory still inside
    # checkout_root passes _escapes_root's resolved-path check (its target
    # is legitimately within root) -- but expanding it would silently
    # overwrite that OTHER directory's own content and unlink ITS pointer
    # marker. The pointer directory itself being a symlink must be
    # rejected outright.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="shared\n")
    victim = _pointer(root, "victim-plugin", "zdd")
    (victim / "innocent.txt").write_text("do not touch\n", encoding="utf-8")

    attacker_dir = root / "plugins" / "agent-bridge" / "libs"
    attacker_dir.mkdir(parents=True)
    (attacker_dir / "zdd").symlink_to(victim, target_is_directory=True)

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "symlink" in line for line in log)
    # The victim's own content must be untouched (its own genuine pointer
    # legitimately expands independently -- that's expected and fine).
    assert (victim / "innocent.txt").read_text() == "do not touch\n"
    # The attacker's symlink itself must remain untouched -- never expanded
    # into a real copy that would duplicate/corrupt the victim's content.
    assert (attacker_dir / "zdd").is_symlink()


def test_materialize_refuses_a_symlinked_ancestor_directory(tmp_path: Path):
    # Checking only lib_copy_dir itself misses a symlinked ANCESTOR (e.g.
    # plugins/<plugin> or plugins/<plugin>/libs itself): find_pointers()'s
    # own glob already follows such an intermediate symlink to discover
    # the pointer file, and if it resolves to another directory still
    # inside checkout_root, a resolved-path escape check alone would
    # accept it too -- letting the src_sub removal/copy overwrite that
    # OTHER directory's own content and unlink ITS marker.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="shared\n")
    victim = _pointer(root, "victim-plugin", "zdd")
    (victim / "innocent.txt").write_text("do not touch\n", encoding="utf-8")

    # The attacker's OWN plugin dir (not just its libs/<lib> copy) is a
    # symlink pointing at the victim's plugin dir.
    (root / "plugins" / "attacker-plugin").symlink_to(
        root / "plugins" / "victim-plugin", target_is_directory=True
    )

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (victim / "innocent.txt").read_text() == "do not touch\n"
    assert (root / "plugins" / "attacker-plugin").is_symlink()


def test_materialize_refuses_an_ancestor_symlink_resolving_exactly_to_root(tmp_path: Path):
    # Round-14 review finding: _find_symlinked_ancestor's is_symlink()
    # check must run BEFORE the resolved-path termination test, not
    # after -- a symlink whose target happens to resolve to `root` itself
    # (e.g. plugins/evil -> ..) would otherwise short-circuit the walk as
    # "reached root, nothing to check" without ever inspecting that
    # symlink, letting the later replacement + pointer_path.unlink() write
    # through to the checkout root itself.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="shared\n")
    (root / "src").mkdir()  # something already at root/src, must survive
    (root / "src" / "sentinel.txt").write_text("do not touch\n", encoding="utf-8")

    # plugins/evil-plugin -> root itself (resolves exactly to root_r).
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    (root / "plugins" / "evil-plugin").symlink_to(root, target_is_directory=True)
    # "ghost" (not "zdd") -- through the symlink this literally aliases
    # root/libs/ghost, which must not already exist (avoid colliding with
    # the canonical zdd lib _canonical_lib() already created above).
    libs_dir = root / "plugins" / "evil-plugin" / "libs" / "ghost"
    libs_dir.mkdir(parents=True)
    (libs_dir / mm.POINTER_NAME).write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/zdd"}) + "\n",
        encoding="utf-8",
    )

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "is a symlink" in line for line in log)
    # Nothing was written through to the checkout root itself.
    assert (root / "src" / "sentinel.txt").read_text() == "do not touch\n"
    assert not (root / "VENDOR_POINTER.json").exists()


def test_materialize_skips_a_canonical_lib_with_no_src_directory_at_all(tmp_path: Path):
    # _find_symlink() returns None for a MISSING src/ too, not just "no
    # symlink found inside it" -- an incomplete/malformed canonical lib
    # must be refused here, before the copy's existing src/ gets deleted
    # with nothing to replace it (which would publish a pointer-free
    # copy with NO importable source at all).
    root = tmp_path / "repo"
    (root / "libs" / "zdd").mkdir(parents=True)  # no src/ subdirectory
    (root / "libs" / "zdd" / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )
    pointer_dir = _pointer(root, "agent-bridge", "zdd")
    (pointer_dir / "src" / "zdd").mkdir(parents=True)
    (pointer_dir / "src" / "zdd" / "__init__.py").write_text("stub\n", encoding="utf-8")

    log = mm.materialize(root, canonical_root=root)
    assert any("SKIP" in line and "src not found" in line for line in log)
    # The copy's existing (real) src/ must be untouched, and the pointer
    # marker must still be present -- nothing was actually expanded.
    assert (pointer_dir / "src" / "zdd" / "__init__.py").read_text() == "stub\n"
    assert (pointer_dir / mm.POINTER_NAME).exists()



def test_materialize_handles_multiple_pointers_for_same_lib(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev9", content="shared\n")
    for plugin in ("agent-bridge", "agent-codespaces", "agent-mcp"):
        _pointer(root, plugin, "zdd")

    log = mm.materialize(root, canonical_root=root)
    assert sum(1 for line in log if line.startswith("OK")) == 3
    for plugin in ("agent-bridge", "agent-codespaces", "agent-mcp"):
        assert (root / f"plugins/{plugin}/libs/zdd/src/zdd/__init__.py").read_text() == "shared\n"


def test_build_snapshots_then_materializes(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="original\n")
    _pointer(source, "agent-bridge", "zdd")
    (source / "README.md").write_text("hello\n", encoding="utf-8")

    dest = tmp_path / "dest"
    log = mm.build(dest, source_root=source)

    assert any(line.startswith("OK") for line in log)
    # The snapshot copied everything else too, untouched.
    assert (dest / "README.md").read_text() == "hello\n"
    assert (dest / "plugins/agent-bridge/libs/zdd/src/zdd/__init__.py").read_text() == "original\n"
    # The source checkout itself must never be mutated by build().
    assert (source / "plugins/agent-bridge/libs/zdd/VENDOR_POINTER.json").exists()


def test_build_overwrites_a_stale_existing_dest(tmp_path: Path):
    source = tmp_path / "source"
    _canonical_lib(source, "zdd", version="0.1.0-dev1", content="fresh\n")
    (source / "marker.txt").write_text("v2\n", encoding="utf-8")

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "stale-leftover.txt").write_text("old build\n", encoding="utf-8")

    mm.build(dest, source_root=source)
    assert not (dest / "stale-leftover.txt").exists()
    assert (dest / "marker.txt").read_text() == "v2\n"


def test_find_pointers_returns_sorted_paths(tmp_path: Path):
    root = tmp_path / "repo"
    _pointer(root, "zeta-plugin", "libA")
    _pointer(root, "alpha-plugin", "libB")

    found = mm.find_pointers(root)
    assert [p.parent.parent.parent.name for p in found] == ["alpha-plugin", "zeta-plugin"]


def _worktree_manager_pointer(root: Path, lib: str) -> Path:
    """Create a directory pointer under the extra top-level
    ``worktree-manager/libs/<lib>`` tree (mirrors ``sync-vendored-libs.py``'s
    own extra-tree handling, not the ``plugins/*/libs/*`` shape)."""
    d = root / "worktree-manager" / "libs" / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": f"libs/{lib}"}) + "\n",
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n',
                                       encoding="utf-8")
    return d


def test_find_pointers_includes_worktree_manager(tmp_path: Path):
    root = tmp_path / "repo"
    _pointer(root, "agent-bridge", "libA")
    _worktree_manager_pointer(root, "libB")

    found = mm.find_pointers(root)
    assert len(found) == 2
    assert any(p.parent.parent.parent.name == "worktree-manager" for p in found)


def test_materialize_expands_worktree_manager_pointer(tmp_path: Path):
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.2.0-dev1", content="shared\n")
    _worktree_manager_pointer(root, "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any(line.startswith("OK") for line in log)
    copy_src = root / "worktree-manager/libs/zdd/src/zdd/__init__.py"
    assert copy_src.read_text() == "shared\n"


def test_find_pointers_in_libs_dir_finds_a_pointer_directly_under_libs(tmp_path: Path):
    """The shape a copied-out payload has (e.g. a self-installed
    worktree-manager slot's own <slot>/libs/*), unlike find_pointers()'s
    fixed plugins/*/libs/* / worktree-manager/libs/* repo-root-relative
    glob."""
    libs_dir = tmp_path / "slot" / "libs"
    d = libs_dir / "zdd"
    d.mkdir(parents=True)
    (d / mm.POINTER_NAME).write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/zdd"}) + "\n",
        encoding="utf-8",
    )
    found = mm.find_pointers_in_libs_dir(libs_dir)
    assert [p.parent.name for p in found] == ["zdd"]


def test_find_pointers_in_libs_dir_empty_when_no_libs_dir(tmp_path: Path):
    assert mm.find_pointers_in_libs_dir(tmp_path / "nope") == []


def test_materialize_libs_dir_expands_a_pointer_from_canonical(tmp_path: Path):
    """The case worktree_manager.self_install's _materialize_payload_pointers
    needs: expanding a copied-out slot's own libs/<lib> directly (not a
    whole repo-checkout-shaped tree)."""
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.3.0-dev1", content="from-canonical\n")

    libs_dir = tmp_path / "slot" / "libs"
    copy_dir = libs_dir / "zdd"
    (copy_dir / "src" / "zdd").mkdir(parents=True)
    (copy_dir / "src" / "zdd" / "__init__.py").write_text("stub\n", encoding="utf-8")
    (copy_dir / mm.POINTER_NAME).write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": "libs/zdd"}) + "\n",
        encoding="utf-8",
    )

    log = mm.materialize_libs_dir(libs_dir, canonical_root=root)

    assert any(line.startswith("OK") for line in log)
    assert not (copy_dir / mm.POINTER_NAME).exists()
    assert (copy_dir / "src" / "zdd" / "__init__.py").read_text() == "from-canonical\n"


def _pointer_with_source(root: Path, plugin: str, lib: str, *, source: str) -> Path:
    """Like ``_pointer``, but with an arbitrary (possibly malicious) ``source``
    value instead of the well-formed ``libs/<lib>`` default."""
    d = root / "plugins" / plugin / "libs" / lib
    d.mkdir(parents=True, exist_ok=True)
    (d / "VENDOR_POINTER.json").write_text(
        json.dumps({"schema": "copilot-extensions.vendor-pointer", "version": 1,
                    "source": source}) + "\n",
        encoding="utf-8",
    )
    (d / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.0.0"\n',
                                       encoding="utf-8")
    return d


def test_materialize_refuses_absolute_source_path_for_directory_pointer(tmp_path: Path):
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret"
    (secret / "src" / "evil").mkdir(parents=True)
    (secret / "src" / "evil" / "__init__.py").write_text("leak = True\n", encoding="utf-8")
    pointer_dir = _pointer_with_source(root, "agent-bridge", "evil", source=str(secret))

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "escapes the canonical root" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (pointer_dir / "src").exists()


def test_materialize_refuses_traversal_source_path_for_directory_pointer(tmp_path: Path):
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret"
    (secret / "src" / "evil").mkdir(parents=True)
    (secret / "src" / "evil" / "__init__.py").write_text("leak = True\n", encoding="utf-8")
    pointer_dir = _pointer_with_source(
        root, "agent-bridge", "evil", source="../outside-repo-secret"
    )

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "escapes the canonical root" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (pointer_dir / "src").exists()


def test_materialize_refuses_an_escaping_symlink_within_canonical_src(tmp_path: Path):
    # The pointer's own `source` (libs/zdd) is legitimately inside
    # canonical_root, but the canonical directory's own src/ contains a
    # symlink pointing outside it -- shutil.copytree would otherwise follow
    # it and copy external content into the release snapshot.
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret"
    secret.mkdir()
    (secret / "leaked.txt").write_text("do not leak\n", encoding="utf-8")
    canonical = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    (canonical / "src" / "evil-link").symlink_to(secret, target_is_directory=True)
    pointer_dir = _pointer(root, "agent-bridge", "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (pointer_dir / "src").exists()


def test_materialize_refuses_an_in_root_symlink_within_canonical_src(tmp_path: Path):
    # Even a symlink that resolves *inside* canonical_root is refused --
    # a legitimate vendored lib has no reason to contain any symlink.
    root = tmp_path / "repo"
    canonical = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    other_lib = _canonical_lib(root, "other", version="0.1.0-dev1", content="other\n")
    (canonical / "src" / "sneaky-link").symlink_to(other_lib, target_is_directory=True)
    pointer_dir = _pointer(root, "agent-bridge", "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (pointer_dir / "src").exists()


def test_materialize_refuses_a_symlinked_src_root(tmp_path: Path):
    # `tree.is_dir()` follows a symlink, so a canonical lib whose `src`
    # itself is a symlink (not merely containing one) would otherwise slip
    # past a check that only scans descendants via rglob().
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret"
    secret.mkdir()
    (secret / "leaked.txt").write_text("do not leak\n", encoding="utf-8")
    lib_dir = root / "libs" / "zdd"
    lib_dir.mkdir(parents=True)
    (lib_dir / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.1.0-dev1"\n', encoding="utf-8"
    )
    (lib_dir / "src").symlink_to(secret, target_is_directory=True)
    pointer_dir = _pointer(root, "agent-bridge", "zdd")

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (pointer_dir / "src").exists()


def test_materialize_refuses_a_symlinked_tests_root(tmp_path: Path):
    # The tests/ refresh added alongside src/ needs the same symlink
    # containment guarantee -- a canonical lib whose tests/ is a symlink
    # (or contains one) must not let shutil.copytree leak external content.
    # The tests/ refresh only runs at all when the copy already vendors
    # tests/ (see the "gated on the copy already having tests/" design), so
    # this test seeds the copy with one before exercising the symlink path.
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret-tests"
    secret.mkdir()
    (secret / "leaked.txt").write_text("do not leak\n", encoding="utf-8")
    canonical = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    (canonical / "tests").symlink_to(secret, target_is_directory=True)
    pointer_dir = _pointer(root, "agent-bridge", "zdd")
    (pointer_dir / "tests").mkdir(parents=True)
    (pointer_dir / "tests" / "test_zdd.py").write_text("def test_it():\n    pass\n",
                                                        encoding="utf-8")

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (pointer_dir / "src").exists()


def test_materialize_rejection_never_destroys_previous_tests_content(tmp_path: Path):
    # The symlink check for tests/ must run BEFORE anything is deleted --
    # a rejected refresh must leave the copy's previous, safe tests/
    # content intact, not destroy it and leave nothing behind.
    root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret-tests2"
    secret.mkdir()
    canonical = _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    (canonical / "tests").symlink_to(secret, target_is_directory=True)
    pointer_dir = _pointer(root, "agent-bridge", "zdd")
    original_tests = pointer_dir / "tests"
    original_tests.mkdir(parents=True)
    (original_tests / "test_zdd.py").write_text("def test_it():\n    pass\n", encoding="utf-8")

    mm.materialize(root, canonical_root=root)

    assert (original_tests / "test_zdd.py").read_text() == "def test_it():\n    pass\n"


def test_materialize_catches_a_dangling_tests_symlink_at_the_destination(tmp_path: Path):
    # A dangling (or non-directory-target) symlink at the copy's own
    # tests/ has is_dir()==False, since is_dir() follows the link to a
    # target that isn't there -- an is_dir()-only "does this copy have
    # tests/" gate would silently ignore it, leaving it untouched in a
    # materialized release.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    (root / "libs/zdd/tests").mkdir(parents=True)
    (root / "libs/zdd/tests/test_zdd.py").write_text("def test_it():\n    pass\n",
                                                       encoding="utf-8")
    pointer_dir = _pointer(root, "agent-bridge", "zdd")
    (pointer_dir / "tests").symlink_to(tmp_path / "does-not-exist", target_is_directory=True)

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "destination" in line and "is a symlink" in line
               for line in log)


def test_materialize_refuses_a_pointer_dir_that_escapes_dest(tmp_path: Path):
    # find_pointers() globs under dest, but a symlinked plugin (or libs)
    # directory could still resolve outside dest -- rmtree()/copytree()
    # must never write there even though the pointer glob "found" it.
    root = tmp_path / "repo"
    _canonical_lib(root, "zdd", version="0.1.0-dev1", content="real\n")
    outside = tmp_path / "outside-plugin"
    real_pointer_dir = _pointer(outside, "escaped-plugin", "zdd")
    (root / "plugins").mkdir(parents=True, exist_ok=True)
    (root / "plugins" / "escaped-plugin").symlink_to(
        outside / "plugins" / "escaped-plugin", target_is_directory=True
    )

    log = mm.materialize(root, canonical_root=root)

    assert any("SKIP" in line and "is a symlink" in line for line in log)
    assert (real_pointer_dir / "VENDOR_POINTER.json").exists()
    assert not (real_pointer_dir / "src").exists()


def test_build_preserves_a_symlink_instead_of_dereferencing_its_content(tmp_path: Path):
    # The default shutil.copytree(symlinks=False) would dereference ANY
    # symlink under source_root during the initial whole-tree snapshot
    # copy, embedding external content into dest before materialize()'s
    # own per-pointer symlink check even runs. build() must preserve
    # symlinks instead.
    source_root = tmp_path / "repo"
    secret = tmp_path / "outside-repo-secret"
    secret.mkdir()
    (secret / "leaked.txt").write_text("do not leak\n", encoding="utf-8")
    (source_root / "some-dir").mkdir(parents=True)
    (source_root / "some-dir" / "a-link").symlink_to(secret, target_is_directory=True)
    (source_root / "README.md").write_text("hello\n", encoding="utf-8")
    dest = tmp_path / "dest"

    mm.build(dest, source_root=source_root)

    copied_link = dest / "some-dir" / "a-link"
    assert copied_link.is_symlink(), "a-link must be preserved as a symlink, not dereferenced"
    # Confirm the symlink is a real symlink object -- not a directory that
    # copytree(symlinks=False) would have created containing an embedded
    # copy of the secret's own files.
    assert os.readlink(copied_link) == str(secret)


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
    _pointer(source, "agent-bridge", "zdd")
    monkeypatch.setattr(mm, "REPO", source)

    dest = tmp_path / "dest"
    code = mm.main(["--dest", str(dest)])
    assert code == 0
    out = capsys.readouterr().out
    assert "Materialized 1 pointer(s)" in out
