#!/usr/bin/env python3
"""Keep top-level ``libs/<lib>`` canonical sources and their per-plugin
``plugins/<plugin>/libs/<lib>`` vendored copies from silently drifting apart.

``check-vendored-libs-sync.py`` already guards one half of this invariant: it
verifies every *copy* of a lib is byte-identical to every other copy. It does
**not** compare copies against the top-level ``libs/<lib>`` canonical source at
all -- so canonical can drift from what's actually shipped without any guard
ever noticing. That already happened in practice (see the dev-branch-release-
pipeline effort's Journal): ``libs/ssh-manager`` sat at ``0.1.0-dev12`` while
every real vendored copy had independently advanced to ``0.1.0-dev21`` and
picked up an entire missing module (``relay_channel.py``). Materializing FROM
that canonical source today would silently regress every consumer.

This tool closes that gap in three modes:

* ``--check`` (default, safe): report canonical-vs-copies drift as advisory
  (does not fail CI -- no existing workflow depends on this invariant yet, and
  a hard failure here would surprise unrelated PRs). Also re-runs the
  existing copies-vs-copies check.
* ``--restore-canonical``: the safe direction *today*, given that copies are
  the verified-consistent, actually-shipped truth. Copies a lib's first copy
  (after confirming all copies agree) up into the top-level ``libs/<lib>``,
  creating that canonical root when missing and replacing its complete
  filtered lib tree (``src/``, ``tests/`` when present, ``README.md``,
  ``pyproject.toml``, and any other real tracked files, while still ignoring
  build/cache cruft). Never touches plugin copies.
* ``--materialize``: the FUTURE direction once canonical is restored and kept
  current -- copies top-level ``libs/<lib>`` DOWN into every
  ``plugins/<plugin>/libs/<lib>``. This is the shape the dev/main release
  pipeline's promotion-time generator will eventually call. Refuses to run for
  any lib whose *copies* carry a newer declared version than canonical (the
  "someone edited a copy directly and never touched canonical" hazard), unless
  ``--force`` is passed, so it can never silently regress a consumer. It is
  NOT blocked merely because content differs when canonical is the newer
  side -- that is the normal, expected pre-materialize state.

### Legacy directory-pointer compatibility

Older checkouts or fixtures may still carry a ``VENDOR_POINTER.json`` beside a
vendored lib copy. That shape is retired repo-wide: this tool no longer writes
it, and ``tools/materialize_main.py`` / ``tools/preview_release.py`` no longer
expand it during promotion or preview. ``sync-vendored-libs.py`` still
recognizes the legacy form only so compatibility helpers such as
``--restore-canonical``, ``--materialize``, and ``--uv-editable`` can reason
about an already-existing pointer copy instead of treating it as a real source
tree.

A legacy pointer copy is never a "real copy": its ``src/`` (if any) is never
this lib's real, verified-agreeing content, so it stays excluded from
copies-vs-copies agreement checks and from ``--restore-canonical``'s "which
copy is the truth" selection -- treating a pointer's own content as truth
would silently **wipe canonical**. ``--materialize`` still knows how to
refresh such a legacy copy in-place for compatibility purposes: it replaces
the marker with real ``src/`` + version content from canonical, and refreshes
``tests/`` only when the copy already carried its own copy of them.

Two pointer *kinds* exist, both identified purely by ``VENDOR_POINTER.json``:

* **bare** -- ``src/`` doesn't exist at all. Never actually installable
  (`uv pip install -e .` fails outright: "does not appear to be a Python
  project", confirmed empirically) -- unusable for any plugin still
  developed/tested on `dev` (i.e. every real plugin today), only ever safe
  for a lib whose consuming plugin has itself been fully retired from `dev`.
* **src-passthrough** (``--pointerize``, agent-cli-lazy-dispatch Phase 2's
  first real adopter, ``plugins/agent-worktrees/libs/lazy-cli-dispatch``) --
  carries a real, importable ``src/<pkg>/__init__.py`` marked with a
  ``# VENDOR_POINTER: source=libs/<lib> kind=src-passthrough`` first-line
  comment, whose body sets its own package ``__path__`` to canonical's real
  ``libs/<lib>/src/<pkg>`` directory. Every import of the vendored package
  resolves through ordinary Python import machinery straight to canonical's
  real modules -- `uv pip install -e .`, `run-plugin-tests.py`, and CI's own
  test-runner job all keep working unmodified on `dev`, with zero copy-drift
  risk (there is nothing to keep in sync; editing canonical takes effect
  immediately). This tool's legacy ``--materialize`` compatibility mode still
  expands it into a real byte-identical copy exactly like the bare kind.

A THIRD, distinct mechanism -- not a `VENDOR_POINTER.json` pointer at all --
also exists: the **`uv`-editable canonical-reference form**
(`vendor-pointer-generalization` effort, Phase 1), the current, active
mechanism for real vendored libs (superseding `src-passthrough` above --
see this module's own Journal history for why). ``--uv-editable CONSUMER
LIB`` converts a copy (real or `src-passthrough`) into this form: the local
`plugins/<plugin>/libs/<lib>` (or `worktree-manager/libs/<lib>`) copy is
deleted entirely -- no directory, no stub -- and the consuming
`pyproject.toml`'s `[tool.uv.sources]` entry is rewritten from
`{ path = "libs/<lib>" }` to `{ path = "<relative-to-repo-root>/libs/<lib>",
editable = true }`, which `uv` resolves live to canonical on `dev`
independent of the top-level package's own editable-ness. `--check` also
validates every such reference (missing `editable = true`, a missing/
symlinked canonical target). `tools/uv_editable_ref.py` holds the shared
implementation (used by this script and by `tools/materialize_main.py`'s
promotion-time expansion back into a real copy); see its own module
docstring for the full design.

Legacy `src-passthrough` pointer copies may still be recognized here for
compatibility with older checkouts or fixtures, but this tool no longer writes
that form: the repo retired `--pointerize` once the last real consumer moved
to either the `uv`-editable canonical-reference form or a full real vendored
copy.

Usage::

    python tools/sync-vendored-libs.py                    # --check (default)
    python tools/sync-vendored-libs.py --restore-canonical # copies -> canonical
    python tools/sync-vendored-libs.py --materialize        # canonical -> copies
    python tools/sync-vendored-libs.py --uv-editable agent-worktrees lazy-cli-dispatch
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uv_editable_ref as uer  # noqa: E402
from vendored_lib_inventory import is_artifact_only  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
POINTER_NAME = "VENDOR_POINTER.json"

# Consumer trees that sit outside plugins/ but still vendor shared libs the
# same way -- kept in one place so every lib-copy-locating path (agreement
# checks, --materialize, --pointerize) resolves a consumer consistently
# instead of each hardcoding "plugins/<x>" and silently missing these.
_EXTRA_CONSUMER_DIRS = uer._EXTRA_CONSUMER_DIRS

_IGNORE_PARTS = {
    ".git",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
}
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)

def _is_pointer_copy(path: Path) -> bool:
    """True when ``path`` is a DRY vendor-pointer copy, not a real copy.

    A pointer copy is identified solely by ``VENDOR_POINTER.json``'s
    presence, regardless of whether ``src/`` exists: the original ("bare")
    pointer kind carries no ``src/`` at all, while the working
    "src-passthrough" kind (now legacy-only) carries a
    real, importable ``src/<pkg>/__init__.py`` stub that forwards every
    import to canonical at runtime via ``__path__`` -- so `uv pip install
    -e .` and ordinary test imports keep working on `dev, unlike the bare
    kind. Either way its ``src/`` (if any) is never this lib's real,
    verified-agreeing content, so it must stay excluded from copies-vs-
    copies comparison and from ``--restore-canonical``'s truth selection.
    """
    return (path / POINTER_NAME).is_file()


def _real_copies(paths: list[Path]) -> list[Path]:
    """``paths`` filtered down to real (non-pointer) copies."""
    return [p for p in paths if not _is_pointer_copy(p)]


def _consumer_dir(consumer: str) -> Path:
    """Resolve ``consumer``'s own root directory: a normal
    ``plugins/<consumer>`` plugin, or one of the extra top-level trees
    (``_EXTRA_CONSUMER_DIRS``) that vendor shared libs the same way without
    living under ``plugins/``. Raises ``SystemExit`` for an unknown
    consumer rather than silently resolving a nonexistent path."""
    if consumer in _EXTRA_CONSUMER_DIRS:
        return REPO / consumer
    plugin_dir = PLUGINS_DIR / consumer
    if plugin_dir.is_dir():
        return plugin_dir
    raise SystemExit(
        f"{consumer}: not a known consumer (neither plugins/{consumer} nor "
        f"one of the extra top-level trees {_EXTRA_CONSUMER_DIRS})"
    )


def _lib_copies() -> dict[str, list[Path]]:
    """Inventory source copies without resurrecting retired cache-only paths."""
    copies: dict[str, list[Path]] = {}
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(PLUGINS_DIR.iterdir()):
            libs = plugin / "libs"
            if not libs.is_dir():
                continue
            for lib in sorted(libs.iterdir()):
                if lib.is_dir() and not is_artifact_only(lib):
                    copies.setdefault(lib.name, []).append(lib)
    for extra in _EXTRA_CONSUMER_DIRS:
        libs = REPO / extra / "libs"
        if not libs.is_dir():
            continue
        for lib in sorted(libs.iterdir()):
            if lib.is_dir() and not is_artifact_only(lib):
                copies.setdefault(lib.name, []).append(lib)
    return copies


def _src_files(lib_dir: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every file under ``<lib>/src``."""
    src = lib_dir / "src"
    out: dict[str, str] = {}
    if not src.is_dir():
        return out
    for f in src.rglob("*"):
        if not f.is_file():
            continue
        if _IGNORE_PARTS & set(f.relative_to(src).parts):
            continue
        if f.suffix in (".pyc", ".pyo") or ".egg-info" in str(f):
            continue
        rel = f.relative_to(src).as_posix()
        out[rel] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _declared_version(lib_dir: Path) -> str | None:
    pp = lib_dir / "pyproject.toml"
    if not pp.exists():
        return None
    m = _VERSION_RE.search(pp.read_text(encoding="utf-8"))
    return m.group(2) if m else None


def _copies_agree(paths: list[Path]) -> tuple[bool, list[str]]:
    """True + [] when every copy's src/ and version match; else False + problems."""
    problems: list[str] = []
    maps = [_src_files(p) for p in paths]
    ref_map = maps[0]
    for other_map, other_path in zip(maps[1:], paths[1:], strict=True):
        for rel in sorted(set(ref_map) | set(other_map)):
            if ref_map.get(rel) != other_map.get(rel):
                problems.append(
                    f"src/{rel} differs between {paths[0].name} and {other_path.name} copies"
                )
    versions = {str(p): _declared_version(p) for p in paths}
    if len(set(versions.values())) > 1:
        problems.append(f"version skew across copies: {versions}")
    return (not problems), problems


def _full_trees_agree(paths: list[Path]) -> tuple[bool, list[str]]:
    """True + [] when every copy's complete filtered tree matches."""
    problems: list[str] = []
    ref = paths[0]
    for other in paths[1:]:
        if not uer.lib_tree_matches(ref, other):
            problems.append(f"complete filtered tree differs: {ref} vs {other}")
    return (not problems), problems


def _canonical_drift(lib: str, copies: list[Path]) -> list[str]:
    """Advisory diff between top-level ``libs/<lib>`` and its (agreed) copies."""
    canonical = LIBS_DIR / lib
    if not canonical.is_dir():
        return [f"{lib}: no top-level canonical libs/{lib}/ (copies are the only source)"]
    canon_map = _src_files(canonical)
    copy_map = _src_files(copies[0])
    problems: list[str] = []
    for rel in sorted(set(canon_map) | set(copy_map)):
        if canon_map.get(rel) != copy_map.get(rel):
            problems.append(f"{lib}: src/{rel} differs between canonical and vendored copies")
    canon_ver = _declared_version(canonical)
    copy_ver = _declared_version(copies[0])
    if canon_ver != copy_ver:
        problems.append(f"{lib}: version skew -- canonical={canon_ver} copies={copy_ver}")
    return problems


def _find_symlink(tree: Path) -> str | None:
    """The first path (relative to ``tree``, or ``"."`` when ``tree`` itself
    is the symlink) under ``tree`` that is a symlink, or ``None`` if none is
    found. Mirrors ``materialize_main.py``'s own ``_find_symlink`` (kept as
    a separate small copy rather than a cross-module import, since this
    script's hyphenated filename can't be a normal ``import`` target).
    ``tree.is_dir()`` alone is not enough: it follows a symlink, so a
    symlinked ``tree`` itself would otherwise pass through unnoticed."""
    if tree.is_symlink():
        return "."
    if not tree.is_dir():
        return None
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            return str(entry.relative_to(tree))
    return None


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    """Mirrors ``materialize_main.py``'s own ``_find_symlinked_ancestor``
    (kept as a separate small copy for the same hyphenated-filename reason
    as ``_find_symlink`` above). The first symlink among ``path`` itself
    and every ancestor directory up to and including ``root``. Checking
    only the final directory misses a symlinked ANCESTOR (e.g.
    ``plugins/<plugin>`` or ``libs`` itself): ``_lib_copies()``'s own
    directory discovery already follows such an intermediate symlink, and
    if it resolves to another directory still inside ``root``, a
    resolved-path escape check alone would accept it too. ``is_symlink()``
    is checked BEFORE the resolved-path termination test, not after: a
    symlink whose target happens to RESOLVE to ``root`` itself would
    otherwise short-circuit as "reached root, nothing to check" without
    ever inspecting that symlink itself."""
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def _remove_path(p: Path) -> None:
    """Remove ``p`` whatever it is -- a real directory, a real file, or a
    symlink (including a dangling one, where ``exists()``/``is_dir()`` are
    both ``False`` since they follow the link to a target that isn't
    there). ``shutil.rmtree`` alone cannot remove a symlink (even one that
    resolves to a directory), and a plain existence check would silently
    leave a dangling symlink behind."""
    if p.is_symlink():
        p.unlink()
    elif p.is_dir():
        shutil.rmtree(p)
    elif p.exists():
        p.unlink()


def _safe_replace_tree(src: Path, dst: Path, *, label: str) -> None:
    """Replace ``dst`` with a copy of ``src``, refusing any symlink under
    ``src`` (``shutil.copytree``'s default ``symlinks=False`` follows and
    dereferences a symlink, which would otherwise let a malicious or
    accidental symlink under a canonical lib's ``src/``/``tests/`` leak
    arbitrary filesystem content into a vendored copy or a shipped
    release -- a legitimate canonical lib has no reason to contain one at
    all) and any symlink AT ``dst`` itself (a dangling/non-directory
    symlink there would otherwise survive untouched, since ``is_dir()``
    is ``False`` for it and no branch below would ever remove or replace
    it). Validates ``src`` (and detects a symlinked ``dst``) BEFORE
    removing anything, so a rejected copy leaves the previous ``dst``
    content intact rather than destroying it and leaving nothing behind."""
    found = _find_symlink(src)
    if found is not None:
        where = label if found == "." else f"{label}/{found}"
        raise SystemExit(
            f"{where} is a symlink -- refusing (a canonical lib source "
            "must contain only real files)"
        )
    if dst.is_symlink():
        raise SystemExit(
            f"{label} (destination) is a symlink -- refusing to replace it "
            "blindly (a vendored copy must contain only real files)"
        )
    _remove_path(dst)
    if src.is_dir():
        shutil.copytree(src, dst)


def _copy_src(src_lib: Path, dst_lib: Path) -> None:
    _safe_replace_tree(src_lib / "src", dst_lib / "src", label=f"{src_lib.name}/src")


def _copy_tests(src_lib: Path, dst_lib: Path) -> None:
    """Copy ``src_lib``'s ``tests/`` into ``dst_lib``, the same way
    ``_copy_src`` copies ``src/``. Used when refreshing an already-vendored
    legacy pointer copy's ``tests/``: callers must gate the call on
    ``dst_lib``'s own ``tests/`` already existing themselves (see
    ``cmd_materialize()``). They never unilaterally introduce ``tests/`` for a
    copy that never had one -- that per-copy choice, once made, is preserved
    across every later legacy-pointer materialize run. Never used for a real
    (non-pointer) copy's own, possibly independently authored ``tests/``."""
    _safe_replace_tree(src_lib / "tests", dst_lib / "tests", label=f"{src_lib.name}/tests")


def _copy_lib_tree(src_lib: Path, dst_lib: Path) -> None:
    """Replace ``dst_lib`` with a filtered copy of the whole ``src_lib`` tree."""
    bad_ancestor = _find_symlinked_ancestor(src_lib, REPO)
    if bad_ancestor is not None:
        raise SystemExit(
            f"{bad_ancestor} is a symlink -- refusing (a canonical lib root, "
            "and every ancestor between it and the repository root, must be "
            "a real directory)"
        )
    found = _find_symlink(src_lib)
    if found is not None:
        where = src_lib.name if found == "." else f"{src_lib.name}/{found}"
        raise SystemExit(
            f"{where} is a symlink -- refusing (a vendored lib copy promoted "
            "to canonical must contain only real files)"
        )
    bad_ancestor = _find_symlinked_ancestor(dst_lib, REPO)
    if bad_ancestor is not None:
        raise SystemExit(
            f"{bad_ancestor} is a symlink -- refusing (a canonical lib root, "
            "and every ancestor between it and the repository root, must be "
            "a real directory)"
        )
    if dst_lib.is_symlink():
        raise SystemExit(
            f"libs/{dst_lib.name} (destination) is a symlink -- refusing to "
            "replace it blindly (a canonical lib root must be a real directory)"
        )
    _remove_path(dst_lib)

    def _ignore(_root: str, names: list[str]) -> set[str]:
        ignored = {name for name in names if name in _IGNORE_PARTS}
        ignored.update(name for name in names if name.endswith((".pyc", ".pyo")))
        ignored.update(name for name in names if name.endswith(".egg-info"))
        return ignored

    shutil.copytree(src_lib, dst_lib, ignore=_ignore)


def _sync_version(src_lib: Path, dst_lib: Path) -> None:
    src_pp = src_lib / "pyproject.toml"
    pp = dst_lib / "pyproject.toml"
    # Both is_file()/exists() checks below follow a symlink -- a linked
    # src_lib/pyproject.toml or dst_lib/pyproject.toml would make
    # read_text()/write_text() silently follow it, letting this update an
    # arbitrary external target's version field. Silently no-op (matching
    # this function's existing "missing pyproject.toml is a silent no-op"
    # contract) rather than raise, since callers already preflight the
    # trees that matter for their own destructive operations; this is a
    # last-line defense for the version-sync step specifically.
    if src_pp.is_symlink() or pp.is_symlink():
        return
    version = _declared_version(src_lib)
    if version is None:
        return
    if not pp.exists():
        return
    text = pp.read_text(encoding="utf-8")
    pp.write_text(_VERSION_RE.sub(rf"\g<1>{version}\g<3>", text, count=1), encoding="utf-8")


def _version_cmp(a: str | None, b: str | None) -> int | None:
    """-1/0/1 if ``a`` is older/equal/newer than ``b``; ``None`` if unparseable."""
    if a is None or b is None:
        return None
    try:
        from packaging.version import Version
        va, vb = Version(a), Version(b)
        return (va > vb) - (va < vb)
    except (ValueError, TypeError):
        return None


def _materialize_blocked(lib: str, canonical: Path, first_copy: Path) -> str | None:
    """Reason string if materializing ``lib`` from canonical would be unsafe.

    The unsafe case is specifically "copies moved ahead of canonical without
    canonical ever being updated" (drift introduced by direct copy edits) --
    materializing that stale canonical down would regress every consumer. It is
    *not* unsafe for canonical to differ from copies when canonical is the
    newer side: that is simply the normal pre-materialize state after a
    legitimate canonical-only change, and is exactly what --materialize exists
    to propagate.
    """
    canon_ver = _declared_version(canonical)
    copy_ver = _declared_version(first_copy)
    canon_map = _src_files(canonical)
    copy_map = _src_files(first_copy)
    content_differs = canon_map != copy_map
    cmp = _version_cmp(canon_ver, copy_ver)
    if cmp is None:
        # Can't order the versions -- fall back to content equality as the gate.
        if content_differs:
            return f"{lib}: versions unorderable (canonical={canon_ver} copies={copy_ver}) and content differs"
        return None
    if cmp > 0:
        return None  # canonical is newer -- normal pre-materialize drift, proceed
    if cmp < 0:
        return f"{lib}: copies ({copy_ver}) are newer than canonical ({canon_ver}) -- run --restore-canonical first"
    if content_differs:
        return f"{lib}: same version ({canon_ver}) but content differs -- bump one side"
    return None


def cmd_check() -> int:
    copies_map = _lib_copies()
    exit_code = 0
    for lib, paths in sorted(copies_map.items()):
        real = _real_copies(paths)
        pointers = [p for p in paths if p not in real]
        if len(real) >= 2:
            ok, problems = _copies_agree(real)
            if not ok:
                exit_code = 1
                print(f"{lib}: COPIES OUT OF SYNC")
                for p in problems:
                    print(f"  - {p}")
        if pointers:
            print(f"{lib}: {len(pointers)} DRY pointer copy/copies "
                  f"(excluded from agreement check): "
                  + ", ".join(str(p) for p in pointers))
        if real:
            drift = _canonical_drift(lib, real)
            if drift:
                print(f"{lib}: canonical drift (advisory, does not fail this check)")
                for d in drift:
                    print(f"  - {d}")
    uv_editable_found = False
    for consumer, consumer_dir in uer.iter_consumer_dirs():
        try:
            refs = uer.find_uv_editable_refs(consumer_dir)
        except uer.ManifestUnreadable as exc:
            exit_code = 1
            uv_editable_found = True
            print(f"{consumer}: INVALID uv-editable canonical reference(s)")
            print(f"  - {consumer}: {exc}")
            continue
        # A symlinked pyproject.toml must be validated too, even though
        # find_uv_editable_refs() fails closed on it by returning [] --
        # otherwise it would look identical to a consumer with no
        # uv-editable references at all, silently skipping validation.
        if not refs and not (consumer_dir / "pyproject.toml").is_symlink():
            continue
        uv_editable_found = True
        problems = uer.uv_editable_problems(consumer, consumer_dir)
        if problems:
            exit_code = 1
            print(f"{consumer}: INVALID uv-editable canonical reference(s)")
            for p in problems:
                print(f"  - {p}")
    if exit_code == 0:
        suffix = " (uv-editable references, if any, verified valid.)" if uv_editable_found else ""
        print("sync-vendored-libs --check: copies agree with each other "
              "(canonical drift, if any, is reported above as advisory)."
              + suffix)
    return exit_code


def cmd_restore_canonical() -> int:
    copies_map = _lib_copies()
    for lib, paths in sorted(copies_map.items()):
        real = _real_copies(paths)
        if not real:
            print(f"{lib}: all copies are DRY pointers -- nothing to restore "
                  "(canonical is already the only source)")
            continue
        if len(real) >= 2:
            ok, problems = _full_trees_agree(real)
            if not ok:
                print(f"{lib}: SKIPPED -- copies disagree, fix that first:")
                for p in problems:
                    print(f"  - {p}")
                continue
        truth = real[0]
        canonical = LIBS_DIR / lib
        canonical.parent.mkdir(parents=True, exist_ok=True)
        _copy_lib_tree(truth, canonical)
        print(f"{lib}: canonical restored from {truth}")
    return 0


def cmd_materialize(*, force: bool) -> int:
    copies_map = _lib_copies()
    blocked: list[str] = []
    for lib, paths in sorted(copies_map.items()):
        canonical = LIBS_DIR / lib
        if not canonical.is_dir():
            continue
        if canonical.is_symlink():
            blocked.append(
                f"{lib}: libs/{lib} is a symlink -- refusing (a canonical "
                "lib root must be a real directory, not a link to an "
                "external tree)"
            )
            continue
        real = _real_copies(paths)
        reason = None if (force or not real) else _materialize_blocked(lib, canonical, real[0])
        if reason:
            blocked.append(reason)
            continue
        for copy in paths:
            try:
                bad_ancestor = _find_symlinked_ancestor(copy, REPO)
                if bad_ancestor is not None:
                    # _lib_copies() follows symlinks in plugins/<plugin>
                    # and libs while discovering `copy` -- checking only
                    # the final copy path missed a symlinked ANCESTOR,
                    # which could make --materialize write through to
                    # another consumer (or outside the checkout) via
                    # _copy_src()/pointer.unlink() below, overwriting the
                    # TARGET's own src/version and unlinking the TARGET's
                    # pointer marker.
                    raise SystemExit(
                        f"{bad_ancestor} is a symlink -- refusing (a vendored "
                        "copy root, and every ancestor between it and the "
                        "checkout root, must be a real directory)"
                    )
                pointer = copy / POINTER_NAME
                copy_tests = copy / "tests"
                refresh_tests = pointer.exists() and (
                    copy_tests.is_dir() or copy_tests.is_symlink()
                )
                # Preflight BOTH replacement trees before writing anything:
                # _copy_src() already mutates src/ before _copy_tests() gets
                # a chance to reject a canonical tests/ symlink, which would
                # otherwise leave this copy in a mixed state (fresh src/,
                # stale tests/, pointer marker still present) that a retry
                # or another consumer could observe. Validate first, mutate
                # only once nothing here would fail.
                canon_src = canonical / "src"
                if not canon_src.is_dir():
                    # _safe_replace_tree() removes the destination and then
                    # silently does nothing when src is missing -- without
                    # this check, an incomplete canonical lib with no src/
                    # at all would delete this copy's importable source,
                    # find nothing to replace it with, and still unlink the
                    # pointer marker below as if expansion had succeeded,
                    # publishing a broken copy.
                    raise SystemExit(
                        f"{canonical.name}/src not found -- refusing "
                        "(canonical lib source must exist)"
                    )
                src_bad = _find_symlink(canon_src)
                if src_bad is not None:
                    where = f"{canonical.name}/src" if src_bad == "." else f"{canonical.name}/src/{src_bad}"
                    raise SystemExit(
                        f"{where} is a symlink -- refusing (a canonical "
                        "lib source must contain only real files)"
                    )
                if refresh_tests:
                    if copy_tests.is_symlink():
                        raise SystemExit(
                            f"{copy}/tests (destination) is a symlink -- "
                            "refusing to replace it blindly (a vendored "
                            "copy must contain only real files)"
                        )
                    tests_bad = _find_symlink(canonical / "tests")
                    if tests_bad is not None:
                        where = (
                            f"{canonical.name}/tests" if tests_bad == "."
                            else f"{canonical.name}/tests/{tests_bad}"
                        )
                        raise SystemExit(
                            f"{where} is a symlink -- refusing (a canonical "
                            "lib source must contain only real files)"
                        )

                # _sync_version() guards internally against a symlinked
                # canonical or destination pyproject.toml (returning
                # without writing), but that alone isn't enough: this
                # loop would still continue on to _copy_src() (mutating
                # src/) and unlink the pointer marker below as though
                # everything succeeded, leaving a pointer-free copy with
                # an unsafe linked pyproject.toml surviving untouched.
                # Preflight both paths here, alongside src/tests.
                canon_pp = canonical / "pyproject.toml"
                copy_pp = copy / "pyproject.toml"
                if canon_pp.is_symlink():
                    raise SystemExit(
                        f"{canonical.name}/pyproject.toml is a symlink -- "
                        "refusing (a canonical lib's metadata must be a "
                        "real file)"
                    )
                if copy_pp.is_symlink():
                    raise SystemExit(
                        f"{copy}/pyproject.toml (destination) is a symlink "
                        "-- refusing to write through it blindly"
                    )

                # The checks above only scan src/, tests/, and
                # pyproject.toml -- an unrelated symlink anywhere else in
                # the pointer copy (e.g. docs/link) is never individually
                # enumerated, so it would survive untouched as
                # _copy_src()/pointer.unlink() proceed, leaving a
                # non-self-contained copy. Mirrors materialize_main.py's
                # and preview_release.py's own final blanket scan.
                stray = _find_symlink(copy)
                if stray is not None:
                    where = str(copy) if stray == "." else f"{copy}/{stray}"
                    raise SystemExit(
                        f"{where} is a symlink -- refusing (a vendored "
                        "copy must contain only real files)"
                    )

                _copy_src(canonical, copy)
                _sync_version(canonical, copy)
                if pointer.exists():
                    # Only refresh a DRY-pointer copy's tests/, and only if it
                    # already carries one -- it was vendored from canonical at
                    # --pointerize time as a deliberate per-copy choice, and
                    # would otherwise go stale on every later canonical test
                    # change, with promotion silently snapshotting the stale
                    # tree into main. A copy that never carried tests/ (its
                    # --pointerize chose not to vendor it, e.g. because its
                    # only consumer never discovers libs/*/tests/) must not
                    # gain one unilaterally just because canonical has one. A
                    # real copy's own tests/ may be independently authored and
                    # is left untouched regardless.
                    if refresh_tests:
                        _copy_tests(canonical, copy)
                    pointer.unlink()
            except SystemExit as exc:
                # _copy_src()/_copy_tests() fail closed (raise) on a
                # symlink under canonical -- catch per-copy so one lib's
                # rejected copy doesn't abort materializing every other
                # lib in the same --materialize run, matching this
                # command's existing "collect every problem, report them
                # all together" contract for the drift-refusal case above.
                blocked.append(f"{lib}: {copy}: {exc}")
        print(f"{lib}: materialized into {len(paths)} copy/copies from canonical")
    if blocked:
        print(
            "Refused to materialize (would regress consumers):\n  - "
            + "\n  - ".join(blocked)
            + "\nRun --restore-canonical first for any lib where copies moved "
            "ahead, or pass --force if you have verified canonical is correct.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_uv_editable(consumer: str, lib: str) -> int:
    copy_dir, relpath = uer.convert_to_uv_editable(
        consumer,
        lib,
        repo=REPO,
        libs_dir=LIBS_DIR,
        consumer_dir_of=_consumer_dir,
        find_symlinked_ancestor=_find_symlinked_ancestor,
        remove_path=_remove_path,
        is_pointer_copy=_is_pointer_copy,
    )
    print(f"{lib}: converted {copy_dir.relative_to(REPO)} to uv-editable ({relpath})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="advisory report (default)")
    mode.add_argument("--restore-canonical", action="store_true",
                       help="copy the (verified-agreeing) vendored copies up into canonical")
    mode.add_argument("--materialize", action="store_true",
                       help="copy canonical down into every vendored copy (refuses drifted libs)")
    mode.add_argument("--uv-editable", nargs=2, metavar=("CONSUMER", "LIB"),
                       help="convert <CONSUMER>/libs/<LIB> (a real copy or a "
                            "src-passthrough pointer copy) into the uv-editable "
                            "canonical-reference form: delete the local copy entirely "
                            "and rewrite <CONSUMER>'s pyproject.toml [tool.uv.sources] "
                            "entry to a path = \"<relative>/libs/<LIB>\", editable = true "
                            "reference")
    ap.add_argument("--force", action="store_true",
                     help="with --materialize, proceed even if canonical looks drifted")
    args = ap.parse_args(argv)

    if args.uv_editable:
        consumer, lib = args.uv_editable
        return cmd_uv_editable(consumer, lib)
    if args.restore_canonical:
        return cmd_restore_canonical()
    if args.materialize:
        return cmd_materialize(force=args.force)
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
