#!/usr/bin/env python3
"""Build the "main release form" from a `dev`-shaped checkout: snapshot the
whole tree, then materialize every remaining dev-only reference into the
self-contained shipped form. This is the whole-repo counterpart of
`preview_release.py`'s single-plugin materialization, and a validated
prototype of the promotion pipeline's core step (Phase 3 of the
dev-branch-release-pipeline effort).

Two still-live dev-only forms are expanded here:

* **File pointers** -- any single vendored file (e.g. a mirrored Markdown
  doc under `plugins/<plugin>/docs/`) whose first line is an in-language
  HTML-comment marker:
  `<!-- VENDOR_POINTER: source=<repo-relative-path> kind=file -->`. The
  pointer *is* the mirrored file itself, so materializing overwrites the stub
  in place with the canonical file's bytes.
* **`uv`-editable canonical references** -- a consumer's `pyproject.toml`
  `[tool.uv.sources]` entry whose `path` escapes the consumer's own root
  (for example `{ path = "../../libs/<lib>", editable = true }`) instead of
  vendoring a local `libs/<lib>` copy at all. `dev` resolves this live via
  `uv`; a shipped `main` release cannot, so promotion copies canonical's
  complete lib tree into the consumer's own `libs/<lib>/` and rewrites the
  entry to the local, non-editable `{ path = "libs/<lib>" }` form.

A third materialized surface carries `agent-worktrees`' packaged
launch-wrapper fallback: on `dev`, the authoritative wrapper scripts live only
under `worktree-manager/bin/`; promotion copies the files listed in
`plugins/agent-worktrees/launch-wrapper-assets.json` into
`plugins/agent-worktrees/bin/` so a shipped payload never depends on a sibling
`worktree-manager/` checkout being present.

Usage::

    python tools/materialize_main.py --dest /path/to/main-snapshot
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import materialize_installer_engine as mie  # noqa: E402
import materialize_launch_wrapper_assets as mlwa  # noqa: E402
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
_EXTRA_CONSUMER_DIRS = uer._EXTRA_CONSUMER_DIRS


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
    """Every vendored *file* pointer under ``root``.

    ``root`` may be a whole repo checkout or a single plugin's directory --
    unlike a shared-lib reference's fixed ``plugins/<plugin>/pyproject.toml``
    location, a file pointer can live anywhere its marker line is found, so
    callers such as ``preview_release.py`` (which materializes one plugin
    directory, not the whole repo) can reuse this unchanged.
    """
    if not root.is_dir():
        return []
    return sorted(
        p for p in root.rglob("*")
        if p.is_file() and p.name != POINTER_NAME and _file_pointer_source(p)
    )


def find_retired_directory_pointers(root: Path) -> list[Path]:
    """Every retired directory-pointer marker still present under ``root``,
    a whole-repo-shaped tree (``plugins/<name>/libs/<lib>/`` or
    ``worktree-manager/libs/<lib>/``)."""
    return sorted(root.glob("plugins/*/libs/*/" + POINTER_NAME)) + [
        pointer
        for consumer in _EXTRA_CONSUMER_DIRS
        for pointer in sorted(root.glob(f"{consumer}/libs/*/{POINTER_NAME}"))
    ]


def find_retired_directory_pointers_in_plugin(plugin_dir: Path) -> list[Path]:
    """Every retired directory-pointer marker still present under a SINGLE
    plugin's own directory (e.g. ``preview_release.py``'s per-plugin
    ``dest``, which already IS ``plugins/<name>`` -- not a whole-repo root,
    so ``find_retired_directory_pointers``'s ``plugins/*/libs/*/`` glob can
    never match inside it)."""
    return sorted(plugin_dir.glob("libs/*/" + POINTER_NAME))


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
    return mie.materialize_ref_into(
        source_consumer_dir=source_consumer_dir,
        dest_consumer_dir=dest_consumer_dir,
        canonical_root=canonical_root,
        dest_root=dest_root,
    )


def materialize_installer_engine_refs(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand canonical installer-engine references across every plugin."""
    return mie.materialize_refs(dest, canonical_root=canonical_root)


def materialize_launch_wrapper_assets_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path,
    dest_root: Path | None = None,
) -> list[str]:
    return mlwa.materialize_into(
        source_consumer_dir=source_consumer_dir,
        dest_consumer_dir=dest_consumer_dir,
        canonical_root=canonical_root,
        dest_root=dest_root,
    )


def materialize_launch_wrapper_assets(dest: Path, *, canonical_root: Path) -> list[str]:
    """Materialize packaged launch-wrapper assets for every adopting plugin."""
    return mlwa.materialize_all(dest, canonical_root=canonical_root)


def materialize(dest: Path, *, canonical_root: Path) -> list[str]:
    """Materialize every still-live dev-only reference found under ``dest``.

    ``canonical_root`` is a parameter (not hardcoded to ``REPO``) so a test
    can point it at an isolated tree instead of the real checkout."""
    log = [
        f"SKIP {pointer_path}: retired directory-pointer kind still present -- refusing"
        for pointer_path in find_retired_directory_pointers(dest)
    ]
    log.extend(materialize_file_pointers(dest, canonical_root=canonical_root))
    log.extend(materialize_uv_editable_refs(dest, canonical_root=canonical_root))
    log.extend(materialize_installer_engine_refs(dest, canonical_root=canonical_root))
    log.extend(materialize_launch_wrapper_assets(dest, canonical_root=canonical_root))
    return log


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
    ``plugins/<plugin>/libs`` itself): a glob-based discovery or
    ``Path.is_dir()`` probe can already follow such an intermediate symlink
    before a later write step sees it, and if it resolves to another
    directory still inside ``root``, a resolved-path escape check alone would
    accept it too.

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
    skipped = any(line.startswith("SKIP") for line in log)
    print(f"\nMaterialized {ok} artifact(s) into {args.dest}")
    return 1 if skipped else 0


if __name__ == "__main__":
    raise SystemExit(main())
