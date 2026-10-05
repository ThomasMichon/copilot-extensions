"""Build and atomically publish a self-install payload into a version slot.

Split out of ``self_install.py`` (#5219 follow-up -- extracted purely to stay
under this repo's per-module line-count cap; no behavior change) to keep the
"build a replacement payload in a sibling staging directory, validate it, then
atomically swap it into place" concern -- and its vendor-pointer/symlink
safety checks -- in one cohesive unit, separate from marker/binstub/manifest
bookkeeping and the higher-level ``self_install()``/``self_update()`` flows
that call into it.
"""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path


def _materialize_payload_pointers(payload_dir: Path, slot: Path) -> None:
    """Expand any vendor-pointer lib copies under a freshly-copied
    ``slot/libs/*``, AND any `uv`-editable canonical-reference entry in
    ``slot/pyproject.toml`` (the mechanism that superseded the
    src-passthrough directory-pointer form -- vendor-pointer-generalization
    effort, Phase 1), into real, self-contained content -- a standalone
    self-installed slot (or a self-updated one fetched via git/tarball) has
    no ``libs/``+``plugins/`` monorepo ancestor of its own, so neither the
    src-passthrough pointer stub's runtime ``_find_repo_root`` walk NOR a
    `uv`-editable entry's escaping relative ``path`` can ever resolve
    there; every pointer/reference MUST be materialized into a real copy
    before (or as part of) publishing this slot, exactly the way a
    `main`-branch release already does.

    Uses ``_trusted_pointer_materializer`` -- a real, statically-shipped
    copy of ``tools/materialize_main.py``'s pointer-expansion core, NOT a
    dynamic load of the fetched ``tools/materialize_main.py`` itself.
    ``self_update`` supports user-configured forks/canary refs (see
    ``source_config.py``), so the fetched tree is untrusted input;
    dynamically loading and executing a file FROM it would hand a
    compromised or merely untrusted update source arbitrary code
    execution with the updater's own privileges. This module's code
    always comes from the already-installed, already-trusted running
    process -- only canonical file BYTES are ever read from the fetch.

    ``payload_dir`` is the SOURCE being copied (still-live at the moment
    this runs, whether a dev checkout or a freshly fetched self_update
    staging tree -- see ``self_update``'s git-clone/tarball paths, both of
    which now guarantee a ``libs/`` sibling next to the payload) -- also
    the correct base a `uv`-editable entry's relative ``path`` was
    authored against, since ``payload_dir`` and ``slot`` share the same
    nesting depth (a plain ``copytree``, not a re-rooted layout). Resolves
    canonical from ``payload_dir.parent`` -- if that ancestor lacks a real
    ``libs/`` (and thus can't provide canonical content), any pointer/
    reference found in the copied slot is an unresolvable, permanently-
    broken import for whoever runs it next, so this raises rather than
    silently shipping it.
    """
    from . import _trusted_pointer_materializer as materializer

    libs_dir = slot / "libs"
    # Discover pointer markers and uv-editable references FIRST, before
    # deciding whether canonical is even reachable -- a normal payload with
    # only real (non-pointer, non-escaping) dependencies has nothing to
    # materialize at all, so there's no reason to fail (or even inspect)
    # canonical reachability for it.
    unresolved = materializer.find_pointers_in_libs_dir(libs_dir) if libs_dir.is_dir() else []
    try:
        uv_refs = materializer.find_uv_editable_refs(payload_dir)
    except materializer.ManifestUnreadable as e:
        raise RuntimeError(f"cannot install this payload: {e}") from e
    if not unresolved and not uv_refs:
        return
    monorepo_root = payload_dir.parent
    has_canonical = (monorepo_root / "libs").is_dir()
    if not has_canonical:
        names = sorted(p.parent.name for p in unresolved) + sorted(
            lib for _name, _raw_path, lib, _editable in uv_refs
        )
        raise RuntimeError(
            f"cannot install this payload: libs/{{{', '.join(names)}}} are "
            "unmaterialized vendor pointers or uv-editable canonical "
            "references, but no monorepo ancestor (libs/) is reachable "
            "from the fetched payload to resolve canonical content from -- "
            "self_update's fetch must provide the full monorepo shape, not "
            "just the worktree-manager/ subtree"
        )
    try:
        log = materializer.materialize_libs_dir(libs_dir, canonical_root=monorepo_root) if unresolved else []
        log.extend(materializer.materialize_uv_editable_ref_into(
            source_consumer_dir=payload_dir, dest_consumer_dir=slot,
            canonical_root=monorepo_root, dest_root=slot,
        ) if uv_refs else [])
    except Exception as e:
        # Normalize ANY materialization failure (a malformed pointer's
        # json.JSONDecodeError/KeyError, an OSError from a copy/remove
        # failure, ...) into the one exception type self_install() knows to
        # catch and translate to its documented action="error" result --
        # letting an unexpected exception type escape here would violate
        # self_update's own best-effort/non-fatal contract just as readily
        # as a raw RuntimeError would.
        raise RuntimeError(
            f"pointer materialization under {libs_dir} failed: {e}"
        ) from e
    failures = [line for line in log if line.startswith("SKIP ")]
    if failures:
        raise RuntimeError(
            "refusing to install this payload: pointer materialization "
            "was rejected for " + "; ".join(failures)
        )


def _find_any_symlink(tree: Path) -> Path | None:
    """The first path under (and including) ``tree`` that is a symlink, or
    ``None`` if none is found. Unlike the pointer-specific checks in
    ``_materialize_payload_pointers`` (which only ever examine canonical's
    ``src``/``tests`` and the pointer marker itself), this scans the WHOLE
    copied payload: a symlink anywhere else in it (unrelated to any vendor
    pointer) would still survive into the published slot untouched and
    could resolve outside it at runtime -- ``symlinks=True`` on the
    copytree calls preserves such a symlink faithfully rather than
    dereferencing it, but preservation alone doesn't make it SAFE to
    publish; this is the final blanket check before a slot goes live."""
    if tree.is_symlink():
        return tree
    if not tree.is_dir():
        return None
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            return entry
    return None


def _copy_payload(payload_dir: Path, slot: Path) -> None:
    try:
        _copy_payload_unsafe(payload_dir, slot)
    except RuntimeError:
        # Already our one normalized, self_install()-caught boundary type --
        # re-raise as-is (don't re-wrap).
        raise
    except OSError as e:
        # shutil.rmtree(slot)/shutil.copytree() can themselves raise a bare
        # OSError -- most notably a Windows PermissionError (WinError 32)
        # when the slot directory is still held open by another process's
        # cwd (a stranded OR still-legitimately-running mux-daemon pinned
        # there -- see copilot-extensions#4999). self_install() only
        # catches RuntimeError at its boundary (matching
        # _materialize_payload_pointers' own boundary below), so any
        # filesystem failure here must be normalized to that type for
        # self_install()'s cleanup-and-report ("best-effort slot removal,
        # report action='error'") to run instead of this exception
        # escaping uncaught (``_slot_is_complete`` is what then lets a
        # later run detect and repair a slot that cleanup could not fully
        # remove).
        raise RuntimeError(f"copying payload into {slot} failed: {e}") from e


def _copy_payload_unsafe(payload_dir: Path, slot: Path) -> None:
    # Deferred to avoid a module-load-time circular import: self_install.py
    # imports this module for _copy_payload/_copy_payload_unsafe, so these
    # names are only resolvable once self_install.py has finished defining
    # them (guaranteed true by the time any CALL here actually happens).
    from .self_install import _SLOT_KEY_FILES, _mark_slot_complete

    if payload_dir.is_symlink():
        # symlinks=True on the copytree below only protects symlinks
        # encountered DURING the walk of payload_dir's own tree -- it
        # cannot protect payload_dir being a symlink ITSELF (shutil.
        # copytree always creates dst as a real directory, so there's
        # nowhere for a preserved-root-symlink object to go). In the
        # git-backed self_update path, a checked-out worktree-manager/
        # dir could itself be a symlink; the earlier
        # (payload/"pyproject.toml").is_file() check would still pass
        # (it follows the link), copytree would dereference it, and
        # _find_any_symlink(staging) afterward would see no link at all
        # (the staged root is never included in its own scan).
        raise RuntimeError(
            f"refusing to install this payload: {payload_dir} is a "
            "symlink -- a payload root must be a real directory, not a "
            "link to an external tree"
        )
    # Build the replacement content in a sibling STAGING directory first,
    # and only atomically swap it into `slot` once proven complete -- never
    # rmtree + recopy `slot` in place (#5219). This closes two distinct
    # failure modes at once:
    #
    # 1. Any failure while building the replacement (an unresolvable
    #    vendor-pointer, a missing key file, a transient OS error) leaves a
    #    previously-good `slot` completely untouched -- there is nothing to
    #    clean up and nothing to repair on the next attempt.
    # 2. On Windows, `slot` can be the CURRENTLY-RUNNING interpreter's own
    #    directory (see `_currently_running_from` -- self_install() refuses
    #    to even attempt this case explicitly, but this swap is also safe
    #    on its own merits): `shutil.rmtree` cannot delete an open
    #    `python.exe` (`WinError 5: Access is denied`), but `os.replace`
    #    CAN rename a directory containing one out from under the running
    #    process -- the same trick ordinary Windows self-updaters rely on.
    #    The running process's already-open file handles stay valid after
    #    the rename.
    staging = slot.with_name(f"{slot.name}.staging.{os.getpid()}.{uuid.uuid4().hex}")
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    try:
        ignore = shutil.ignore_patterns(".git", ".venv", "__pycache__", "*.pyc")
        # symlinks=True: a payload staged by self_update's tarball fetch may
        # carry a preserved (not dereferenced -- see _fetch_via_tarball's own
        # symlinks=True) symlink; copying it here with the default
        # symlinks=False would dereference it at this second hop, still
        # smuggling external content into the published slot one step later
        # and defeating _find_any_symlink's downstream rejection.
        shutil.copytree(payload_dir, staging, ignore=ignore, symlinks=True)
        bad = _find_any_symlink(staging)
        if bad is not None:
            raise RuntimeError(
                f"refusing to install this payload: {bad} is a symlink -- a "
                "self-installed slot must contain only real files (a symlink "
                "anywhere in it could resolve outside the slot at runtime)"
            )
        _materialize_payload_pointers(payload_dir, staging)
        missing = [rel for rel in _SLOT_KEY_FILES if not (staging / rel).is_file()]
        if missing:
            raise RuntimeError(
                f"refusing to mark {staging} complete: payload is missing "
                + ", ".join(missing)
            )
        # Published only here, as proof the STAGED copy is complete --
        # strictly before it is swapped into `slot` below, so the swap only
        # ever publishes an already-proven-complete replacement.
        _mark_slot_complete(staging)
        _swap_in_staged_slot(staging, slot)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def _swap_in_staged_slot(staging: Path, slot: Path) -> None:
    """Atomically publish ``staging`` as ``slot``.

    Two renames, never one rmtree + copy: if ``slot`` already exists, it is
    first renamed out of the way to a ``retired`` sibling name (a directory
    rename never requires closing files already open inside it), then
    ``staging`` is renamed into ``slot``'s now-vacated name. If the second
    rename fails, the retired directory is renamed straight back so a
    previously-working install is never left stripped (#5219's requested
    fix 3). Reuses :func:`~worktree_manager.self_install._replace_with_retry`
    -- the same transient-``PermissionError``-retrying ``os.replace`` the
    control-plane provider manifest writer already relies on.
    """
    from .self_install import _replace_with_retry

    retired: Path | None = None
    if slot.exists():
        retired = slot.with_name(f"{slot.name}.retired.{os.getpid()}.{uuid.uuid4().hex}")
        _replace_with_retry(slot, retired)
    try:
        _replace_with_retry(staging, slot)
    except OSError:
        if retired is not None:
            _replace_with_retry(retired, slot)
        raise
    if retired is not None:
        shutil.rmtree(retired, ignore_errors=True)
