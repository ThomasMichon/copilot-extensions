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
tests. File-pointer adopters: `docs/patterns/entity-relationship-model.md`'s
three mirrors (`plugins/agent-worktrees/docs/`, `plugins/agent-bridge/docs/`,
`plugins/agent-dispatch/docs/`) were converted to real file pointers in
Phase 2 of vendored-doc-pointers, so this tool now expands real content on
every real promotion run, not just in tests.

A third, distinct form is expanded too (not a `VENDOR_POINTER.json` pointer
at all): the **`uv`-editable canonical-reference** form
(`vendor-pointer-generalization` effort, Phase 1) -- a consumer's
`pyproject.toml` `[tool.uv.sources]` entry whose `path` escapes the
consumer's own root (e.g. `{ path = "../../libs/<lib>", editable = true }`)
instead of vendoring a local `libs/<lib>` copy at all. `dev` resolves this
live via `uv`'s own dependency resolution; a shipped `main` release cannot
(the referenced path won't exist on the end-user's machine), so promotion
copies canonical's complete lib tree (`src/`, `README.md`, `tests/`, and
`pyproject.toml` itself) into the consumer's own `libs/<lib>/` and
surgically rewrites the entry to the local, non-editable
`{ path = "libs/<lib>" }` form -- see `find_uv_editable_refs()` and
`materialize_uv_editable_ref_into()` below. This is the mechanism that
superseded `src-passthrough` (see `tools/sync-vendored-libs.py`'s own module
docstring for that history); `sync-vendored-libs.py --uv-editable` performs
the dev-time conversion this function's promotion-time expansion mirrors.

Usage::

    python tools/materialize_main.py --dest /path/to/main-snapshot
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier  # noqa: E402
import nested_uv_editable_ref as nuer  # noqa: E402
import uv_editable_ref as uer  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
POINTER_NAME = "VENDOR_POINTER.json"
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)
_FILE_POINTER_RE = re.compile(
    r'^<!--\s*VENDOR_POINTER:\s*source=(\S+)\s+kind=file\s*-->\s*$'
)

# Consumer trees that sit outside plugins/ but still reference shared libs
# the same way -- mirrors sync-vendored-libs.py's own _EXTRA_CONSUMER_DIRS.
_EXTRA_CONSUMER_DIRS = ("worktree-manager",)


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

    # Check the pointer's declared source for a symlinked ancestor BEFORE
    # resolving it: _resolve_within() below only validates that the
    # RESOLVED candidate stays within canonical_root, but if
    # canonical_root/source_rel (e.g. libs/<lib>) is ITSELF a symlink to
    # another directory still inside canonical_root, that check accepts
    # it and returns the resolved (symlink-followed) target -- so every
    # later scan (_find_symlink(canonical / "src") etc.) only ever
    # examines the TARGET's own contents, never noticing the redirect.
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

    canon_pp = canonical / "pyproject.toml"
    copy_pp = lib_copy_dir / "pyproject.toml"
    # canon_pp.is_file()/copy_pp.is_file() (checked further below) both
    # follow symlinks -- a pointer copy with pyproject.toml linked to
    # another file (or canonical's own linked elsewhere) would make
    # read_text()/write_text() follow the link, letting promotion
    # silently read from or overwrite an arbitrary external target while
    # updating the version. Preflighted here, alongside src/tests, before
    # ANY mutation runs below.
    if canon_pp.is_symlink():
        return f"SKIP {lib_copy_dir}: {source_rel}/pyproject.toml is a symlink -- refusing"
    if copy_pp.is_symlink():
        return (
            f"SKIP {lib_copy_dir}: pyproject.toml (destination) is a symlink "
            "-- refusing to write through it blindly"
        )

    # The checks above validate the pieces this function itself KNOWS
    # about (src/, tests/, pyproject.toml) -- but a pointer copy directory
    # can carry other, unrelated entries too (e.g. a stray docs/link), and
    # pointer_path.unlink() below would leave any such symlink sitting
    # untouched in the promoted payload, making the resulting main
    # snapshot not self-contained (it could resolve outside the release).
    # One final blanket scan of the WHOLE copy directory closes this,
    # matching what _copy_payload() already does for self-install
    # (_find_any_symlink(slot)).
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


def materialize_uv_editable_ref_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path,
    dest_root: Path | None = None,
) -> list[str]:
    """Expand every `uv`-editable canonical-reference entry declared in
    ``source_consumer_dir``'s ``pyproject.toml`` (the location the entry's
    relative ``path`` was authored against -- the REAL, un-copied consumer
    directory, since a copy such as ``preview_release.py``'s single-plugin
    workdir tree can sit at a different nesting depth where the same
    relative path would resolve somewhere else entirely) into
    ``dest_consumer_dir``: copy canonical's complete lib tree (``src/``,
    ``README.md``, ``tests/``, and ``pyproject.toml`` itself -- the local
    copy has none of these until canonical's own are copied in) into
    ``dest_consumer_dir/libs/<lib>/``, then surgically rewrite
    ``dest_consumer_dir/pyproject.toml``'s entry to the local, non-editable
    ``{ path = "libs/<lib>" }`` form -- restoring exactly what a real
    vendored copy looks like today. This is the promotion-time inverse of
    ``sync-vendored-libs.py``'s ``--uv-editable`` conversion, and the step
    that makes the `uv`-editable form safe in production: a shipped
    ``main`` release never carries a live reference to a path that won't
    exist on the end-user's machine.

    Uses ``uv_editable_ref.find_uv_editable_refs`` (shared with the dev-time
    drift guard, not a second duplicate) -- which returns EVERY escaping
    entry regardless of whether ``editable`` is set. An entry missing
    ``editable = true`` is refused (``SKIP``) here rather than silently
    passed through: skipping it invisibly would let ``promote_release.py``
    ship the external path unchanged in a real release.

    ``source_consumer_dir`` and ``dest_consumer_dir`` are the same
    directory for a whole-repo snapshot (``materialize_main.py``'s own
    ``materialize()`` -- the copytree preserved the same nesting depth as
    the real checkout, so the authored relative path still resolves
    correctly against the copy itself) but differ for
    ``preview_release.py``'s single-plugin preview (source is the real
    ``plugins/<plugin>``, dest is a bare ``workdir/<plugin>`` with no
    monorepo ancestor of its own).

    ``dest_root`` is the actual snapshot root every destination-side
    ancestor check is bounded against (default: ``dest_consumer_dir``
    itself, for ``preview_release.py``'s single-consumer caller, which has
    no deeper "dest/plugins/<plugin>" structure of its own). For a
    whole-repo snapshot, ``materialize_uv_editable_refs()`` passes the real
    ``dest`` tree root -- checking only up to ``dest_consumer_dir`` would
    miss a symlinked ``dest/plugins`` itself (``build()`` preserves tracked
    symlinks, and this function's caller discovers consumer dirs via a
    symlink-following ``is_dir()``), letting it redirect the lib copy and
    manifest rewrite outside the intended snapshot entirely."""
    log: list[str] = []
    dest_root_r = (dest_root or dest_consumer_dir).resolve()
    bad_ancestor = _find_symlinked_ancestor(dest_consumer_dir, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing"]
    materialized_libs: set[str] = set()  # libs already copied THIS call (alias tracking)
    source_pyproject = source_consumer_dir / "pyproject.toml"
    if source_pyproject.is_symlink():
        # find_uv_editable_refs() fails closed on a symlinked pyproject.toml
        # by returning [] -- which would otherwise make this look
        # identical to "no uv-editable references at all" and silently
        # leave the symlink (and whatever unresolved external source entry
        # it hides) in the promoted snapshot untouched.
        return [
            f"SKIP {dest_consumer_dir}: {source_pyproject} is a symlink -- "
            "refusing to trust it for uv-editable canonical-reference "
            "expansion"
        ]
    if canonical_root.is_symlink():
        # canonical_root.resolve() below would otherwise silently discard
        # this fact -- every later _find_symlinked_ancestor() walk in this
        # function terminates AT canonical_root_r, so a symlinked
        # canonical_root itself would never be inspected, letting its
        # external target be treated as trusted and copied during
        # promotion.
        return [f"SKIP {dest_consumer_dir}: {canonical_root} is a symlink -- refusing"]
    canonical_root_r = canonical_root.resolve()
    try:
        refs = uer.find_uv_editable_refs(source_consumer_dir)
    except uer.ManifestUnreadable as exc:
        # A manifest that EXISTS but can't be read/parsed must never look
        # identical to "no references at all" (which would let promotion
        # emit no SKIP and ship an unresolved external path unchanged) --
        # refuse the whole consumer explicitly instead.
        return [f"SKIP {dest_consumer_dir}: {exc}"]
    for name, raw_path, lib, editable in refs:
        if not editable:
            log.append(
                f"SKIP {dest_consumer_dir}: {name} references {raw_path} "
                "outside its own root but is missing editable = true -- "
                "refusing to ship an unresolved external reference"
            )
            continue
        if not uer.is_safe_lib_name(lib):
            log.append(
                f"SKIP {dest_consumer_dir}: {name} references {raw_path}, "
                f"whose final path component {lib!r} is not a safe lib "
                "name -- refusing"
            )
            continue
        # Check the UNRESOLVED, canonical (".."-free) libs/<lib> path for a
        # symlinked ancestor BEFORE ever calling .resolve() on anything
        # derived from raw_path -- resolving first would silently follow
        # (and erase) a symlink along the way, so a later ancestor check
        # against the already-resolved path could never detect it. Every
        # copy this function performs reads from canonical_root_r/libs/lib
        # directly (never from raw_path's own literal route), so checking
        # THAT constructed path is what actually matters, not raw_path's
        # own possibly `..`-laden text.
        canonical_unresolved = canonical_root_r / "libs" / lib
        bad_ancestor = _find_symlinked_ancestor(canonical_unresolved, canonical_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing")
            continue
        canonical = (source_consumer_dir / raw_path).resolve()
        # Require the resolved path to be EXACTLY canonical_root/libs/<lib>
        # -- not merely "somewhere inside canonical_root". A looser
        # containment check would accept an escaping entry pointing at,
        # say, ../../plugins/other and copy that OTHER plugin's tree into
        # this consumer's libs/<lib>/, rewriting it as a shared-lib
        # dependency it never was.
        if canonical != canonical_unresolved.resolve():
            log.append(
                f"SKIP {dest_consumer_dir}: {name} references {raw_path} "
                f"(resolved {canonical}) which is not canonical_root/libs/{lib} "
                "-- refusing"
            )
            continue
        if not canonical.is_dir():
            log.append(
                f"SKIP {dest_consumer_dir}: {name} references {raw_path} "
                f"(resolved {canonical}) which does not exist"
            )
            continue
        symlink_found = _find_symlink(canonical)
        if symlink_found is not None:
            where = str(canonical) if symlink_found == "." else f"{canonical}/{symlink_found}"
            log.append(f"SKIP {dest_consumer_dir}: {where} is a symlink -- refusing")
            continue

        dest_lib_dir = dest_consumer_dir / "libs" / lib
        bad_ancestor = _find_symlinked_ancestor(dest_lib_dir, dest_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_lib_dir}: {bad_ancestor} is a symlink -- refusing")
            continue

        pyproject = dest_consumer_dir / "pyproject.toml"
        if pyproject.is_symlink():
            # write_text() below would otherwise follow the link and
            # overwrite an external target -- the same class of gap the
            # existing destination-side symlink checks for the lib tree
            # already close, applied to the destination MANIFEST itself.
            log.append(f"SKIP {pyproject}: is a symlink -- refusing to rewrite it blindly")
            continue

        if lib in materialized_libs:
            # A second [tool.uv.sources] name pointing at the SAME
            # canonical lib another entry already copied this run (an
            # alias) -- rewrite this entry too, without recopying a tree
            # that's already there. Treating this as "already exists --
            # refusing to overwrite" would leave the second alias's
            # escaping reference un-rewritten in the promoted snapshot.
            error = _rewrite_uv_editable_source_entry(
                pyproject=pyproject, name=name, raw_path=raw_path, lib=lib
            )
            log.append(error if error is not None else f"OK   {dest_lib_dir} <- {raw_path} (alias)")
            continue
        if dest_lib_dir.exists() or dest_lib_dir.is_symlink():
            log.append(f"SKIP {dest_lib_dir}: already exists -- refusing to overwrite")
            continue

        result = _materialize_one_uv_editable_ref(
            canonical=canonical, dest_lib_dir=dest_lib_dir, pyproject=pyproject,
            name=name, raw_path=raw_path, lib=lib,
        )
        log.append(result)
        if result.startswith("OK"):
            materialized_libs.add(lib)
            # Fix up any `uv`-editable reference the just-copied canonical
            # lib declares FOR ITSELF (e.g. ssh-manager depending on
            # agent-procutil) -- found in review (PR #4372): without this,
            # the consumer's own entry gets rewritten to the local
            # non-editable form above, but a nested entry inside the
            # copied lib's own pyproject.toml is left untouched, still
            # `editable = true` -- a real release then has the SAME path
            # required both editable and non-editable at once, and `uv`
            # refuses to resolve it. Nested libs it successfully handles
            # are folded into `materialized_libs` too -- a LATER top-level
            # entry for that same lib (the consumer's own direct
            # dependency, processed after this one) must take the
            # "alias" (rewrite-only) path below, not the hard "already
            # exists" refusal, since the nested step already placed it at
            # the exact same sibling location a top-level entry would.
            nested_log, nested_materialized = nuer.materialize_nested_uv_editable_refs(
                dest_lib_dir, canonical_root=canonical_root_r, dest_root=dest_root_r,
            )
            log.extend(nested_log)
            materialized_libs.update(nested_materialized)
    return log


def _uv_source_entry_pattern(name: str, raw_path: str) -> re.Pattern[str]:
    """Compiled pattern matching a ``[tool.uv.sources]`` entry for ``name``
    whose ``path`` is ``raw_path`` with ``editable = true`` -- tolerant of
    the realistic TOML syntax variations ``find_uv_editable_refs()``'s own
    real ``tomllib`` parse already accepts, so discovery and rewriting stay
    in sync (a valid entry that parses correctly must never make the
    rewrite silently fail to find it, which would make promotion emit an
    unresolvable ``SKIP``): a bare or quoted key, a double-quoted (basic)
    or single-quoted (literal) path string, either ``path``/``editable``
    key order, and an optional trailing comment after the closing brace.
    Does not attempt full arbitrary-TOML-formatting support (e.g. a
    differently-escaped basic string encoding the same value) -- this
    remains a surgical regex rewrite (this repo's own established
    convention, preserving hand-authored comments) rather than a full
    TOML-aware editor."""
    escaped_name = re.escape(name)
    escaped_path = re.escape(raw_path)
    key = r'(?:"' + escaped_name + r'"|\'' + escaped_name + r"'|" + escaped_name + r')'
    path_value = r'(?:"' + escaped_path + r'"|\'' + escaped_path + r"')"
    return re.compile(
        r'^([ \t]*' + key + r'\s*=\s*)\{\s*(?:'
        r'path\s*=\s*' + path_value + r'\s*,\s*editable\s*=\s*true'
        r'|editable\s*=\s*true\s*,\s*path\s*=\s*' + path_value +
        r')\s*\}[ \t]*(?:#.*)?$',
        re.MULTILINE,
    )


def _rewrite_uv_editable_source_entry(
    *, pyproject: Path, name: str, raw_path: str, lib: str
) -> str | None:
    """Rewrite ``pyproject``'s ``[tool.uv.sources]`` entry named ``name``
    (whose ``path`` is ``raw_path``, ``editable = true``) to the local,
    non-editable ``{ path = "libs/<lib>" }`` form -- the manifest-only half
    of ``_materialize_one_uv_editable_ref()``'s work, split out so an alias
    entry (a second ``[tool.uv.sources]`` name pointing at the same
    canonical lib another entry already materialized) can be rewritten
    without recopying a tree that is already there. Scoped to the
    ``[tool.uv.sources]`` table's own span (never the whole file) and
    accepts either key order, same as the full materializer. Returns an
    error string on failure, or ``None`` on success."""
    text = pyproject.read_text(encoding="utf-8")
    span = uer.uv_sources_table_span(text)
    if span is None:
        return f"SKIP {pyproject}: no [tool.uv.sources] table found"
    start, end = span
    pattern = _uv_source_entry_pattern(name, raw_path)
    if pattern.search(text[start:end]) is None:
        return f"SKIP {pyproject}: could not find {name}'s uv-editable entry to rewrite"
    new_table_text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "libs/{lib}" }}', text[start:end], count=1
    )
    assert count == 1  # already confirmed via the preflight search() above
    pyproject.write_text(text[:start] + new_table_text + text[end:], encoding="utf-8")
    return None


def _materialize_one_uv_editable_ref(
    *, canonical: Path, dest_lib_dir: Path, pyproject: Path, name: str, raw_path: str, lib: str,
) -> str:
    """Copy ``canonical``'s complete lib tree into ``dest_lib_dir`` and
    rewrite ``pyproject``'s entry to the local non-editable form --
    preflighted (via the same regex the rewrite itself uses, AND a check
    that canonical actually has the required ``src/`` + ``pyproject.toml``)
    BEFORE any file is copied, so a rewrite failure -- or a malformed
    canonical lib -- never happens only after the canonical tree has
    already been (partially) copied in. Mirrors the existing directory/
    file pointer materializer's own fail-closed behavior for an incomplete
    canonical lib (missing ``src/``).

    The rewrite is scoped to the ``[tool.uv.sources]`` table's own span
    (``uv_editable_ref.uv_sources_table_span`` -- the same scoping the
    dev-time conversion side uses), never the whole ``pyproject.toml`` file,
    so an identical-looking value in an unrelated table can never be
    rewritten by mistake. It also accepts either key order
    (``path``-then-``editable`` or ``editable``-then-``path``) on a single
    line -- ``find_uv_editable_refs()`` parses real TOML and accepts both,
    so the rewrite must too, or a valid `uv`-editable entry authored with
    the other order would pass ``--check`` yet make promotion emit an
    unresolvable ``SKIP``."""
    if not (canonical / "src").is_dir():
        return f"SKIP {dest_lib_dir}: {canonical}/src not found -- refusing (canonical lib source must exist)"
    if not (canonical / "pyproject.toml").is_file():
        return f"SKIP {dest_lib_dir}: {canonical}/pyproject.toml missing -- refusing"
    # lib_tree_matches() (the dev-time conversion's drift gate) compares
    # canonical's COMPLETE tree, not a fixed allowlist of subpaths -- the
    # copy performed here must match that promise exactly, or a canonical
    # lib carrying any extra file (a root-level LICENSE, package data, or
    # anything else) would silently disappear from the promoted snapshot
    # even though the dev-time side treats it as real, load-bearing
    # content. Refuse any symlink anywhere in canonical up front (the
    # existing directory/file pointer materializer's own safeguard,
    # applied here too) rather than letting shutil.copytree silently
    # follow one.
    stray = _find_symlink(canonical)
    if stray is not None:
        where = str(canonical) if stray == "." else f"{canonical}/{stray}"
        return f"SKIP {dest_lib_dir}: {where} is a symlink -- refusing"

    # Preflight the rewrite BEFORE copying anything, same as before --
    # _rewrite_uv_editable_source_entry() itself would happily run after
    # the copy, but a preflight-only dry check first keeps the "never
    # partially copy, then fail the rewrite" guarantee intact without
    # duplicating the whole regex here.
    text = pyproject.read_text(encoding="utf-8")
    span = uer.uv_sources_table_span(text)
    if span is None:
        return f"SKIP {pyproject}: no [tool.uv.sources] table found"
    start, end = span
    pattern = _uv_source_entry_pattern(name, raw_path)
    if pattern.search(text[start:end]) is None:
        return f"SKIP {pyproject}: could not find {name}'s uv-editable entry to rewrite"

    shutil.copytree(canonical, dest_lib_dir, ignore=_ignore)
    error = _rewrite_uv_editable_source_entry(
        pyproject=pyproject, name=name, raw_path=raw_path, lib=lib
    )
    if error is not None:
        return error
    return f"OK   {dest_lib_dir} <- {raw_path}"


def materialize_uv_editable_refs(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every `uv`-editable canonical-reference entry found across
    every consumer under ``dest`` (a whole-repo snapshot: every
    ``plugins/*`` and the extra top-level ``worktree-manager`` tree) --
    reads each entry's authored relative path against ``canonical_root``
    (the real checkout the snapshot was copied from), same as the existing
    directory/file pointer expansion above."""
    log: list[str] = []
    dest_plugins = dest / "plugins"
    if dest_plugins.is_dir():
        for dest_consumer_dir in sorted(p for p in dest_plugins.iterdir() if p.is_dir()):
            source_consumer_dir = canonical_root / "plugins" / dest_consumer_dir.name
            log.extend(materialize_uv_editable_ref_into(
                source_consumer_dir=source_consumer_dir,
                dest_consumer_dir=dest_consumer_dir,
                canonical_root=canonical_root,
                dest_root=dest,
            ))
    for extra in _EXTRA_CONSUMER_DIRS:
        dest_extra = dest / extra
        if dest_extra.is_dir():
            log.extend(materialize_uv_editable_ref_into(
                source_consumer_dir=canonical_root / extra,
                dest_consumer_dir=dest_extra,
                canonical_root=canonical_root,
                dest_root=dest,
            ))
    return log


def materialize_installer_engine_ref_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path,
    dest_root: Path | None = None,
) -> list[str]:
    """Expand a plugin's canonical installer-engine source line into ``dest``.

    On ``dev`` the wrapper directly sources ``libs/installer-engine`` via a
    relative path that escapes the plugin root. Promotion restores the shipped
    self-contained form by copying canonical back into ``scripts/`` and
    rewriting the source line to the local ``installer-engine.{ps1,sh}``.
    """
    log: list[str] = []
    dest_root_r = (dest_root or dest_consumer_dir).resolve()
    bad_ancestor = _find_symlinked_ancestor(dest_consumer_dir, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing"]

    for ext in ("ps1", "sh"):
        source_script = source_consumer_dir / "scripts" / f"install.{ext}"
        dest_script = dest_consumer_dir / "scripts" / f"install.{ext}"
        match_count = ier.source_match_count(source_script, ext)
        is_registered_adopter = source_consumer_dir.name in ier.ADOPTERS
        if is_registered_adopter and match_count == 0:
            log.append(
                f"SKIP {dest_script}: registered adopter does not source installer-engine "
                "in this language variant"
            )
            continue
        if match_count > 1:
            log.append(
                f"SKIP {dest_script}: found {match_count} installer-engine source "
                "lines; expected exactly one"
            )
            continue
        ref = ier.find_engine_ref(source_script, ext)
        if ref is None:
            if is_registered_adopter:
                log.append(
                    f"SKIP {dest_script}: installer-engine source line is malformed or "
                    "unrecognized"
                )
            continue
        if not ier.ref_escapes_plugin_root(ref, source_consumer_dir):
            if is_registered_adopter and not ier.is_local_ref(ref, source_consumer_dir):
                log.append(
                    f"SKIP {dest_script}: installer-engine reference {ref.raw_path} "
                    f"(resolved {ref.resolved()}) is neither the exact local form "
                    f"{ier.local_line(ext)!r} nor the canonical reference form"
                )
            if ier.is_local_ref(ref, source_consumer_dir):
                continue
            continue
        if canonical_root.is_symlink():
            log.append(f"SKIP {dest_consumer_dir}: {canonical_root} is a symlink -- refusing")
            continue
        canonical_root_r = canonical_root.resolve()
        canonical_unresolved = canonical_root_r / "libs" / "installer-engine" / ref.file_name
        bad_ancestor = _find_symlinked_ancestor(canonical_unresolved, canonical_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_script}: {bad_ancestor} is a symlink -- refusing")
            continue
        canonical = ier.canonical_file(ext, repo_root=canonical_root)
        if not canonical.is_file():
            log.append(
                f"SKIP {dest_consumer_dir}: canonical source missing: "
                f"{canonical.relative_to(canonical_root)}"
            )
            continue
        if ref.resolved() != canonical.resolve():
            log.append(
                f"SKIP {dest_script}: installer-engine reference {ref.raw_path} "
                f"(resolved {ref.resolved()}) is not "
                f"{canonical.relative_to(canonical_root)}"
            )
            continue
        if canonical.is_symlink():
            log.append(f"SKIP {dest_script}: {canonical} is a symlink -- refusing")
            continue
        if dest_script.is_symlink():
            log.append(f"SKIP {dest_script}: is a symlink -- refusing to rewrite it blindly")
            continue
        dest_engine = dest_consumer_dir / "scripts" / ref.file_name
        bad_ancestor = _find_symlinked_ancestor(dest_engine, dest_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_engine}: {bad_ancestor} is a symlink -- refusing")
            continue
        if dest_engine.exists() or dest_engine.is_symlink():
            log.append(f"SKIP {dest_engine}: already exists -- refusing to overwrite")
            continue

        dest_engine.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(canonical, dest_engine)
        if os.name != "nt":
            shutil.copymode(canonical, dest_engine)
        if not ier.rewrite_to_local(dest_script, ext):
            try:
                dest_engine.unlink()
            except OSError:
                pass
            log.append(f"SKIP {dest_script}: could not find installer-engine entry to rewrite")
            continue
        log.append(f"OK   {dest_engine} <- {canonical.relative_to(canonical_root)}")
    return log


def materialize_installer_engine_refs(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand canonical installer-engine references across every plugin."""
    log: list[str] = []
    dest_plugins = dest / "plugins"
    if not dest_plugins.is_dir():
        return log
    for plugin in ier.ADOPTERS:
        dest_consumer_dir = dest_plugins / plugin
        if not dest_consumer_dir.is_dir():
            continue
        log.extend(materialize_installer_engine_ref_into(
            source_consumer_dir=canonical_root / "plugins" / plugin,
            dest_consumer_dir=dest_consumer_dir,
            canonical_root=canonical_root,
            dest_root=dest,
        ))
    return log


def materialize(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every pointer found under ``dest`` from ``canonical_root``.

    ``canonical_root`` is a parameter (not hardcoded to ``REPO``) so a test
    can point it at an isolated tree instead of the real checkout."""
    log = [
        _materialize_one_pointer(pointer_path, checkout_root=dest, canonical_root=canonical_root)
        for pointer_path in find_pointers(dest)
    ]
    log.extend(materialize_file_pointers(dest, canonical_root=canonical_root))
    log.extend(materialize_uv_editable_refs(dest, canonical_root=canonical_root))
    log.extend(materialize_installer_engine_refs(dest, canonical_root=canonical_root))
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
        # find_file_pointers()'s own is_file() check follows a symlink, and
        # pointer_path.write_bytes() below would follow it again -- a
        # symlinked pointer path (or an ancestor between it and dest)
        # would let materialization overwrite the symlink's TARGET outside
        # the snapshot, the same class of gap already fixed for the
        # directory-pointer path.
        bad_ancestor = _find_symlinked_ancestor(pointer_path, dest)
        if bad_ancestor is not None:
            log.append(
                f"SKIP {pointer_path} (file pointer): {bad_ancestor} is a "
                "symlink -- refusing (a file pointer, and every ancestor "
                "between it and dest, must be a real file/directory)"
            )
            continue
        source_rel = _file_pointer_source(pointer_path)
        if not Path(source_rel).is_absolute():
            unresolved_candidate = canonical_root / source_rel
            bad_ancestor = _find_symlinked_ancestor(unresolved_candidate, canonical_root)
            if bad_ancestor is not None:
                log.append(
                    f"SKIP {pointer_path} (file pointer): {bad_ancestor} is a "
                    "symlink -- refusing (a canonical file source, and every "
                    "ancestor between it and the canonical root, must be a "
                    "real file/directory)"
                )
                continue
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
        ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo")) or n.endswith(".egg-info")}


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
