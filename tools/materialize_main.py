#!/usr/bin/env python3
"""Build the "main release form" from a `dev`-shaped checkout: snapshot the
whole tree, then expand every DRY vendor pointer back into a full copy from
its canonical source. This is the whole-repo counterpart of
`preview_release.py`'s single-plugin materialization, and a validated
prototype of the promotion pipeline's core step (Phase 3 of the
dev-branch-release-pipeline effort) -- confirmed byte-for-byte lossless
across all 56 vendored copies of this repo's 10 shared libs in a standalone
trial clone (see the effort's Journal).

Two pointer kinds are expanded, generalized under the
`vendored-doc-pointers` effort (see its README, Phase 1):

* **Directory (lib) pointers** -- `plugins/<plugin>/libs/<lib>/VENDOR_POINTER.json`
  (also `worktree-manager/libs/<lib>/VENDOR_POINTER.json`, the one extra
  top-level tree `sync-vendored-libs.py`/`check-vendored-libs-sync.py` also
  scan), a JSON sidecar next to a lib copy that has no `src/` of its own.
  Expanded from the canonical `libs/<lib>` directory (refusing any `source`
  that escapes the canonical root, the same containment guarantee the file
  pointer kind below already has); `src/` and the declared `pyproject.toml`
  version are always touched, matching `check-vendored-libs-sync.py`'s own
  existing invariant -- `tests/` is refreshed too, but ONLY when the copy
  already carries one of its own (a deliberate per-copy opt-in choice made
  once at `--pointerize` time, never introduced unilaterally on a later
  refresh just because canonical happens to have a `tests/` today).
* **File pointers** -- any single vendored file (e.g. a mirrored Markdown
  doc under `plugins/<plugin>/docs/`) whose first line is an in-language
  HTML-comment marker:
  `<!-- VENDOR_POINTER: source=<repo-relative-path> kind=file -->`. Unlike a
  directory pointer, the pointer *is* the mirrored file itself (its content
  on `dev` is a short human/agent-readable stub, not empty) -- there is no
  separate real copy to remove, so materializing overwrites the stub's
  content in place with the canonical file's bytes.

Both pointer kinds now check into real content in this repo, not just
tests. Directory (lib) pointer adopters: `plugins/agent-worktrees/libs/
lazy-cli-dispatch` and `work-coalescing-singleton` (also vendored via the
extra top-level `worktree-manager/libs/work-coalescing-singleton`), each
using the `src-passthrough` pointer kind (see
`tools/sync-vendored-libs.py`'s own module docstring for that mechanism's
full design). File-pointer adopters: `docs/patterns/entity-relationship-model.md`'s
three mirrors (`plugins/agent-worktrees/docs/`, `plugins/agent-bridge/docs/`,
`plugins/agent-dispatch/docs/`) were converted to real file pointers in
Phase 2 of vendored-doc-pointers, so this tool now expands real content on
every real promotion run, not just in tests.

Usage::

    python tools/materialize_main.py --dest /path/to/main-snapshot
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POINTER_NAME = "VENDOR_POINTER.json"
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)
_FILE_POINTER_RE = re.compile(
    r'^<!--\s*VENDOR_POINTER:\s*source=(\S+)\s+kind=file\s*-->\s*$'
)


def find_pointers(root: Path) -> list[Path]:
    """Every directory/lib pointer under ``root``: ``plugins/*/libs/*`` and
    the extra top-level ``worktree-manager/libs/*`` tree that
    ``sync-vendored-libs.py``/``check-vendored-libs-sync.py`` also scan (see
    those tools' own ``_lib_copies()``/extra-tree handling)."""
    return sorted(
        root.glob("plugins/*/libs/*/" + POINTER_NAME)
    ) + sorted(root.glob("worktree-manager/libs/*/" + POINTER_NAME))


def _file_pointer_source(path: Path) -> str | None:
    """The `source=` value if ``path``'s first line is a file-pointer marker."""
    try:
        with path.open(encoding="utf-8") as f:
            first_line = f.readline()
    except (UnicodeDecodeError, OSError):
        return None
    m = _FILE_POINTER_RE.match(first_line.rstrip("\n"))
    return m.group(1) if m else None


def find_file_pointers(root: Path) -> list[Path]:
    """Every vendored *file* pointer under ``root`` (not directory/lib
    pointers, which are found by ``find_pointers``). ``root`` may be a whole
    repo checkout or a single plugin's directory -- unlike a lib pointer's
    fixed ``plugins/<plugin>/libs/<lib>`` location, a file pointer can live
    anywhere its marker line is found, so callers such as
    ``preview_release.py`` (which materializes one plugin directory, not the
    whole repo) can reuse this unchanged."""
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.name != POINTER_NAME and _file_pointer_source(p)
    )


def find_pointers_in_libs_dir(libs_dir: Path) -> list[Path]:
    """Every directory/lib pointer directly under a single ``libs/`` dir --
    the shape a copied-out payload has (e.g. a self-installed
    ``worktree-manager`` slot's own ``<slot>/libs/*``), unlike
    ``find_pointers()``'s fixed ``plugins/*/libs/*`` /
    ``worktree-manager/libs/*`` repo-root-relative glob."""
    if not libs_dir.is_dir():
        return []
    return sorted(libs_dir.glob("*/" + POINTER_NAME))


def _materialize_one_pointer(pointer_path: Path, *, checkout_root: Path, canonical_root: Path) -> str:
    """Expand a single directory/lib pointer at ``pointer_path`` from
    ``canonical_root``, returning one log line. ``checkout_root`` bounds the
    escape check for the copy's own directory (the tree ``pointer_path``
    itself must stay inside); shared by ``materialize()`` (checkout_root ==
    the whole dest tree) and ``materialize_libs_dir()`` (checkout_root ==
    the single libs/ dir being expanded)."""
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    source_rel = pointer["source"]  # e.g. "libs/zdd"
    lib_copy_dir = pointer_path.parent

    # A symlinked lib_copy_dir pointing at ANOTHER directory still inside
    # checkout_root passes _escapes_root's resolved-path check (its target
    # is legitimately within root) -- but the src_sub removal/copy below
    # would then silently overwrite that OTHER directory's own content and
    # unlink its own pointer marker. Reject the pointer directory itself
    # being a symlink outright, before the escape check even runs. This
    # alone still misses a symlinked ANCESTOR though (e.g. plugins/<plugin>
    # or plugins/<plugin>/libs itself) -- find_pointers()'s own glob
    # already follows such an intermediate symlink to discover the
    # pointer file in the first place, and if it resolves to another
    # directory still inside checkout_root, _escapes_root() (checked
    # next) would accept it too. Check every path component between
    # checkout_root and lib_copy_dir, not just the final directory.
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

    canonical = _resolve_within(canonical_root, source_rel)

    if canonical is None:
        return f"SKIP {lib_copy_dir}: source {source_rel!r} escapes the canonical root -- refusing"
    if not canonical.is_dir():
        return f"SKIP {lib_copy_dir}: canonical {source_rel} not found"

    src_sub = canonical / "src"
    dst_sub = lib_copy_dir / "src"
    # _find_symlink() returns None for a MISSING src_sub too, not just "no
    # symlink found inside it" -- an incomplete/malformed canonical lib
    # with no src/ at all must be refused here, before dst_sub gets
    # deleted below with nothing to replace it: without this check, an
    # incomplete canonical lib would publish a pointer-free copy with NO
    # importable source at all, and the pointer marker would still get
    # unlinked as if the expansion had succeeded.
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

    # A DRY-pointer copy MAY vendor tests/ from canonical the same way
    # (--pointerize) -- refresh it here too if the copy already carries
    # one, otherwise a canonical test change after pointerizing leaves
    # this copy's tests/ stale and promotion would silently snapshot
    # that stale tree into main. Gated on the copy ALREADY having a
    # tests/ dir (not merely on canonical having one): --pointerize
    # records a deliberate choice per copy, and promotion must respect
    # "this copy chose not to vendor tests/" rather than unilaterally
    # introducing one a copy never had. Only ever done for a pointer
    # copy (this whole branch is the pointer-expansion path) -- a real
    # copy's own tests/ is never touched by this function at all.
    # Checks is_symlink() too, not just is_dir() -- a dangling or
    # non-directory symlink there is_dir()==False (it follows the link
    # to a target that isn't there), so an is_dir()-only check would
    # silently ignore it and let it survive untouched into a
    # materialized release.
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

    _remove_path(dst_sub)
    if src_sub.is_dir():
        shutil.copytree(src_sub, dst_sub)

    if refresh_tests:
        _remove_path(dst_tests_sub)
        if tests_sub.is_dir():
            shutil.copytree(tests_sub, dst_tests_sub)

    canon_pp = canonical / "pyproject.toml"
    copy_pp = lib_copy_dir / "pyproject.toml"
    if canon_pp.exists() and copy_pp.exists():
        m = _VERSION_RE.search(canon_pp.read_text(encoding="utf-8"))
        if m:
            text = copy_pp.read_text(encoding="utf-8")
            copy_pp.write_text(_VERSION_RE.sub(rf"\g<1>{m.group(2)}\g<3>", text, count=1),
                               encoding="utf-8")

    pointer_path.unlink()
    return f"OK   {lib_copy_dir} <- {source_rel}"


def materialize(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every pointer found under ``dest`` from ``canonical_root``.

    ``canonical_root`` is a parameter (not hardcoded to ``REPO``) so a test
    can point it at an isolated tree instead of the real checkout."""
    log = [
        _materialize_one_pointer(pointer_path, checkout_root=dest, canonical_root=canonical_root)
        for pointer_path in find_pointers(dest)
    ]
    log.extend(materialize_file_pointers(dest, canonical_root=canonical_root))
    return log


def materialize_libs_dir(libs_dir: Path, *, canonical_root: Path) -> list[str]:
    """Expand every directory/lib pointer directly under a single copied-out
    ``libs/`` dir (not a whole repo checkout shaped tree) -- the case a
    standalone-deployed payload's own vendored libs need (see
    ``worktree_manager.self_install``'s ``_copy_payload``, which calls this
    against a freshly self-installed slot's ``libs/`` using its still-live
    monorepo sibling as ``canonical_root``, since a slot copied out on its
    own has no ``libs/``+``plugins/`` ancestor for the passthrough stub's own
    ``_find_repo_root`` to walk up to at runtime)."""
    return [
        _materialize_one_pointer(pointer_path, checkout_root=libs_dir, canonical_root=canonical_root)
        for pointer_path in find_pointers_in_libs_dir(libs_dir)
    ]





def _escapes_root(candidate: Path, root: Path) -> bool:
    """True when ``candidate``'s resolved (symlink-followed) location is not
    ``root`` itself or a descendant of it."""
    candidate_r = candidate.resolve()
    root_r = root.resolve()
    return candidate_r != root_r and root_r not in candidate_r.parents


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    """The first symlink among ``path`` itself and every ancestor directory
    up to and including ``root``. Checking only ``path`` misses a
    symlinked ANCESTOR (e.g. ``plugins/<plugin>`` or
    ``plugins/<plugin>/libs`` itself): a glob-based discovery like
    ``find_pointers()`` already follows such an intermediate symlink to
    find a pointer file in the first place, and if it resolves to another
    directory still inside ``root``, a resolved-path escape check alone
    would accept it too.

    ``is_symlink()`` is checked BEFORE the resolved-path termination test,
    not after: a symlink whose target happens to RESOLVE to ``root``
    itself (e.g. ``plugins/evil -> ..``) would otherwise short-circuit the
    loop as "reached root, nothing to check" without ever inspecting that
    symlink itself -- exactly the gap an earlier version of this function
    had (checked live: creates ``root/src`` and removes
    ``root/VENDOR_POINTER.json`` through the link)."""
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def _resolve_within(canonical_root: Path, source_rel: str) -> Path | None:
    """Resolve ``source_rel`` against ``canonical_root``, refusing an absolute
    path or any ``../`` traversal that would escape ``canonical_root``
    (including via a symlink). Returns ``None`` when the candidate escapes."""
    if Path(source_rel).is_absolute():
        return None
    candidate = canonical_root / source_rel
    if _escapes_root(candidate, canonical_root):
        return None
    return candidate.resolve()


def _find_symlink(tree: Path) -> str | None:
    """A path (relative to ``tree``, or ``"."`` when ``tree`` itself is the
    symlink) under ``tree`` that is a symlink, or ``None`` if none is found.
    ``_resolve_within`` only validates the pointer's own ``source`` value; a
    legitimate-looking canonical directory can still contain (or itself
    *be*) a symlink (e.g. ``src -> /etc`` or ``src/evil -> /etc``) that
    ``shutil.copytree`` would otherwise silently follow, copying external
    content into the release snapshot. Checking ``tree.is_dir()`` alone is
    not enough: it follows a symlink, so a symlinked ``tree`` itself would
    otherwise pass through unnoticed and only its *descendants* would be
    scanned. A legitimate vendored lib has no reason to contain a symlink
    at all, so any symlink here is refused outright -- simpler than
    distinguishing escaping from non-escaping, and fails closed."""
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
    symlink (including a dangling one, where ``exists()``/``is_dir()`` are
    both ``False`` since they follow the link to a target that isn't
    there). A plain ``if p.exists(): shutil.rmtree(p)`` would silently
    leave a dangling symlink at ``p`` untouched (and then
    ``shutil.copytree`` would fail trying to create a directory where
    that symlink already sits)."""
    if p.is_symlink():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def materialize_file_pointers(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every file pointer found under ``dest`` from ``canonical_root``.

    Unlike a directory/lib pointer, a file pointer *is* the mirrored file:
    there is no separate pointer sidecar to delete, so materializing
    overwrites the stub's content with the canonical file's bytes in place."""
    log: list[str] = []
    for pointer_path in find_file_pointers(dest):
        source_rel = _file_pointer_source(pointer_path)
        canonical = _resolve_within(canonical_root, source_rel)

        if canonical is None:
            log.append(f"SKIP {pointer_path} (file pointer): source {source_rel!r} "
                        "escapes the canonical root -- refusing")
            continue
        if not canonical.is_file():
            log.append(f"SKIP {pointer_path} (file pointer): canonical {source_rel} not found")
            continue

        pointer_path.write_bytes(canonical.read_bytes())
        log.append(f"OK   {pointer_path} (file pointer) <- {source_rel}")
    return log


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in {
        ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo"))}


def build(dest: Path, *, source_root: Path = REPO) -> list[str]:
    if dest.exists():
        shutil.rmtree(dest)
    # symlinks=True: preserve any tracked symlink AS a symlink in the
    # snapshot rather than following it -- the default (False) would
    # silently dereference and embed whatever external content a symlink
    # anywhere in source_root (e.g. a canonical lib's src/) points at,
    # before materialize()'s own per-pointer _find_symlink() check below
    # even runs. A preserved symlink pointing outside dest is inert (a
    # dangling/foreign reference in the snapshot, not embedded bytes); the
    # per-pointer check then still catches and refuses a symlinked
    # canonical lib source specifically.
    shutil.copytree(source_root, dest, ignore=_ignore, symlinks=True)
    return materialize(dest, canonical_root=source_root)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dest", type=Path, required=True,
                     help="directory to build the materialized 'main' snapshot into")
    args = ap.parse_args(argv)

    print(f"Snapshotting {REPO} -> {args.dest} ...")
    log = build(args.dest, source_root=REPO)
    for line in log:
        print(line)
    ok = sum(1 for line in log if line.startswith("OK"))
    print(f"\nMaterialized {ok} pointer(s) into {args.dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
