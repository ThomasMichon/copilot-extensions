"""Trusted, LOCALLY-DEFINED vendor-pointer materializer for self_install.py.

This is a deliberate, hand-maintained COPY of the pointer-expansion core
from ``tools/materialize_main.py`` (specifically ``find_pointers_in_libs_dir``,
``materialize_libs_dir``, ``_materialize_one_pointer``, and their small
helpers) -- NOT a dynamic src-passthrough pointer, and NOT dynamically
loaded from a fetched repository file.

Why a real, static copy instead of the DRY mechanism this whole effort
otherwise builds: ``self_update`` supports user-configured forks/canary
refs (see ``source_config.py``), so the fetched ``tools/`` tree is
untrusted input, not a monorepo dev-checkout. Dynamically loading and
``exec_module()``-ing ``tools/materialize_main.py`` FROM that fetched
source would hand a compromised or merely untrusted update source
arbitrary code execution with the updater's own privileges -- the exact
opposite of what a self-updater's security boundary should allow. This
module ships as part of ``worktree_manager``'s own already-installed,
already-trusted package, so it is compiled into the running installer
itself; it only ever READS fetched content as DATA (canonical file
bytes), never EXECUTES anything from the fetch.

Keep this in sync BY HAND with ``tools/materialize_main.py`` when that
module's pointer-expansion logic changes -- this file's whole point is to
NOT be kept in sync via dynamic loading, so no import-time or runtime
mechanism enforces agreement. What DOES catch a drift:
``worktree-manager/tests/test_trusted_materializer_parity.py`` runs the
same battery of scenarios through both implementations' shared
``materialize_libs_dir()``/``find_pointers_in_libs_dir()`` API (in a full
monorepo checkout) and fails if either one behaves differently for the
same input -- a test-time parity guard, not a build/import-time one, so
still run it after any hand-applied sync.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

POINTER_NAME = "VENDOR_POINTER.json"
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)


def find_pointers_in_libs_dir(libs_dir: Path) -> list[Path]:
    """Every directory/lib pointer directly under a single ``libs/`` dir."""
    if not libs_dir.is_dir():
        return []
    return sorted(libs_dir.glob("*/" + POINTER_NAME))


def _escapes_root(candidate: Path, root: Path) -> bool:
    candidate_r = candidate.resolve()
    root_r = root.resolve()
    return candidate_r != root_r and root_r not in candidate_r.parents


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    """The first symlink among ``path`` itself and every ancestor directory
    up to and including ``root``. ``is_symlink()`` is checked BEFORE the
    resolved-path termination test, not after: a symlink whose target
    happens to RESOLVE to ``root`` itself would otherwise short-circuit
    the loop without ever inspecting that symlink itself."""
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def _resolve_within(canonical_root: Path, source_rel: str) -> Path | None:
    if Path(source_rel).is_absolute():
        return None
    candidate = canonical_root / source_rel
    if _escapes_root(candidate, canonical_root):
        return None
    return candidate.resolve()


def _find_symlink(tree: Path) -> str | None:
    """A path (relative to ``tree``, or ``"."`` when ``tree`` itself is the
    symlink) under ``tree`` that is a symlink, or ``None`` if none is found."""
    if tree.is_symlink():
        return "."
    if not tree.is_dir():
        return None
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            return str(entry.relative_to(tree))
    return None


def _remove_path(p: Path) -> None:
    """Remove ``p`` whatever it is -- a real directory, a real file, or a
    symlink (including a dangling one)."""
    if p.is_symlink():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def _materialize_one_pointer(pointer_path: Path, *, checkout_root: Path, canonical_root: Path) -> str:
    """Expand a single directory/lib pointer at ``pointer_path`` from
    ``canonical_root``, returning one log line."""
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    source_rel = pointer["source"]  # e.g. "libs/zdd"
    lib_copy_dir = pointer_path.parent

    bad_ancestor = _find_symlinked_ancestor(lib_copy_dir, checkout_root)
    if bad_ancestor is not None:
        return (
            f"SKIP {lib_copy_dir}: {bad_ancestor} is a symlink -- refusing "
            "(a pointer copy's own path, and every ancestor between it and "
            "the checkout root, must be a real directory)"
        )

    if _escapes_root(lib_copy_dir, checkout_root):
        return (
            f"SKIP {lib_copy_dir}: pointer directory escapes the "
            "checkout root (a symlinked plugin/libs path) -- refusing"
        )

    if not Path(source_rel).is_absolute():
        unresolved_candidate = canonical_root / source_rel
        bad_ancestor = _find_symlinked_ancestor(unresolved_candidate, canonical_root)
        if bad_ancestor is not None:
            return (
                f"SKIP {lib_copy_dir}: {bad_ancestor} is a symlink -- "
                "refusing (a canonical lib source, and every ancestor "
                "between it and the canonical root, must be a real "
                "directory)"
            )

    canonical = _resolve_within(canonical_root, source_rel)

    if canonical is None:
        return f"SKIP {lib_copy_dir}: source {source_rel!r} escapes the canonical root -- refusing"
    if not canonical.is_dir():
        return f"SKIP {lib_copy_dir}: canonical {source_rel} not found"

    src_sub = canonical / "src"
    dst_sub = lib_copy_dir / "src"
    if not src_sub.is_dir():
        return f"SKIP {lib_copy_dir}: canonical {source_rel}/src not found"
    symlink_found = _find_symlink(src_sub)
    if symlink_found is not None:
        where = f"{source_rel}/src" if symlink_found == "." else f"{source_rel}/src/{symlink_found}"
        return (
            f"SKIP {lib_copy_dir}: {where} is a symlink -- refusing "
            "(a canonical lib source must contain only real files)"
        )
    if dst_sub.is_symlink():
        return (
            f"SKIP {lib_copy_dir}: {source_rel}/src (destination) is a "
            "symlink -- refusing to replace it blindly (a vendored "
            "copy must contain only real files)"
        )

    dst_tests_sub = lib_copy_dir / "tests"
    tests_sub = canonical / "tests"
    refresh_tests = dst_tests_sub.is_dir() or dst_tests_sub.is_symlink()
    if refresh_tests:
        if dst_tests_sub.is_symlink():
            return (
                f"SKIP {lib_copy_dir}: {source_rel}/tests (destination) "
                "is a symlink -- refusing to replace it blindly (a "
                "vendored copy must contain only real files)"
            )
        tests_symlink_found = _find_symlink(tests_sub)
        if tests_symlink_found is not None:
            where = (
                f"{source_rel}/tests" if tests_symlink_found == "."
                else f"{source_rel}/tests/{tests_symlink_found}"
            )
            return (
                f"SKIP {lib_copy_dir}: {where} is a symlink -- refusing "
                "(a canonical lib source must contain only real files)"
            )

    canon_pp = canonical / "pyproject.toml"
    copy_pp = lib_copy_dir / "pyproject.toml"
    if canon_pp.is_symlink():
        return f"SKIP {lib_copy_dir}: {source_rel}/pyproject.toml is a symlink -- refusing"
    if copy_pp.is_symlink():
        return (
            f"SKIP {lib_copy_dir}: pyproject.toml (destination) is a symlink "
            "-- refusing to write through it blindly"
        )

    stray_symlink = _find_symlink(lib_copy_dir)
    if stray_symlink is not None:
        where = str(lib_copy_dir) if stray_symlink == "." else f"{lib_copy_dir}/{stray_symlink}"
        return (
            f"SKIP {lib_copy_dir}: {where} is a symlink -- refusing (a "
            "vendored copy must contain only real files)"
        )

    _remove_path(dst_sub)
    if src_sub.is_dir():
        shutil.copytree(src_sub, dst_sub)

    if refresh_tests:
        _remove_path(dst_tests_sub)
        if tests_sub.is_dir():
            shutil.copytree(tests_sub, dst_tests_sub)

    if canon_pp.exists() and copy_pp.exists():
        m = _VERSION_RE.search(canon_pp.read_text(encoding="utf-8"))
        if m:
            text = copy_pp.read_text(encoding="utf-8")
            copy_pp.write_text(_VERSION_RE.sub(rf"\g<1>{m.group(2)}\g<3>", text, count=1),
                               encoding="utf-8")

    pointer_path.unlink()
    return f"OK   {lib_copy_dir} <- {source_rel}"


def materialize_libs_dir(libs_dir: Path, *, canonical_root: Path) -> list[str]:
    """Expand every directory/lib pointer directly under a single copied-out
    ``libs/`` dir, from ``canonical_root``."""
    return [
        _materialize_one_pointer(pointer_path, checkout_root=libs_dir, canonical_root=canonical_root)
        for pointer_path in find_pointers_in_libs_dir(libs_dir)
    ]
