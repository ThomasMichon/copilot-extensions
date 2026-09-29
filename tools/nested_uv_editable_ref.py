"""Promotion-time fixup for a NESTED `uv`-editable canonical-reference
entry -- one a canonical lib declares for ITSELF, not a top-level consumer
(vendor-pointer-generalization effort, Phase 1; found in review, PR #4372:
`ssh-manager` depends on `agent-procutil`, both real canonical libs).

Split out of ``tools/materialize_main.py`` purely to keep that module
under this repo's per-module line-count cap (see CONTRIBUTING.md § Code
Style) -- a normal ``import`` is possible here (unlike the hyphenated
scripts, which resort to duplicating tiny helpers) since this file's name
has no hyphen.

``materialize_uv_editable_ref_into()``'s outer rewrite (in
``materialize_main.py``) only fixes the CONSUMER's own top-level
``[tool.uv.sources]`` entry -- a just-copied canonical lib's own nested
entry is left untouched, still ``editable = true``. A real `main` release
would then require the SAME path both editable (the nested entry) and
non-editable (the consumer's rewritten entry) at once, which `uv` refuses
to resolve. ``materialize_nested_uv_editable_refs()`` below fixes this up
right after the outer copy succeeds.
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path

import uv_editable_ref as uer


def _escapes_root(candidate: Path, root: Path) -> bool:
    """Mirrors ``materialize_main.py``'s/``uv_editable_ref.py``'s own
    identically-purposed helper (kept separate for the same reason those
    two already duplicate small helpers between themselves)."""
    candidate_r = candidate.resolve()
    root_r = root.resolve()
    return candidate_r != root_r and root_r not in candidate_r.parents


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    """Mirrors ``uv_editable_ref.py``'s/``materialize_main.py``'s own
    identically-purposed helper (kept separate for the same reason those
    two already duplicate small helpers between themselves, rather than
    reaching into another module's private members)."""
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def _find_symlink(tree: Path) -> str | None:
    """Mirrors ``uv_editable_ref.py``'s/``materialize_main.py``'s own
    identically-purposed helper."""
    if tree.is_symlink():
        return "."
    if not tree.is_dir():
        return None
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            return str(entry.relative_to(tree))
    return None


def _uv_source_entry_pattern(name: str, raw_path: str) -> re.Pattern[str]:
    """Mirrors ``materialize_main.py``'s identically-named helper -- see
    its docstring for the TOML syntax variations tolerated."""
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


def _ignore(_dir: str, names: list[str]) -> set[str]:
    """Mirrors ``materialize_main.py``'s own ``_ignore``."""
    return {n for n in names if n in {
        ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo")) or n.endswith(".egg-info")}


def rewrite_nested_uv_editable_entry(
    *, pyproject: Path, name: str, raw_path: str
) -> str | None:
    """Rewrite a NESTED `uv`-editable entry -- one belonging to a
    just-copied canonical lib's OWN manifest (e.g. `ssh-manager` depending
    on `agent-procutil`), not a top-level consumer's -- to drop
    ``editable = true`` while keeping its ``path`` UNCHANGED. Unlike
    ``materialize_main.py``'s ``_rewrite_uv_editable_source_entry`` (which
    rewrites a top-level consumer's entry to the different ``libs/<lib>``
    form, since that consumer's own directory structure doesn't otherwise
    have a ``libs/<lib>`` at all), a nested reference's relative path
    already correctly resolves once its target lib is materialized at the
    matching relative depth -- see ``materialize_nested_uv_editable_refs``'s
    own docstring for why."""
    text = pyproject.read_text(encoding="utf-8")
    span = uer.uv_sources_table_span(text)
    if span is None:
        return f"SKIP {pyproject}: no [tool.uv.sources] table found"
    start, end = span
    pattern = _uv_source_entry_pattern(name, raw_path)
    if pattern.search(text[start:end]) is None:
        return f"SKIP {pyproject}: could not find {name}'s uv-editable entry to rewrite"
    new_table_text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "{raw_path}" }}', text[start:end], count=1
    )
    assert count == 1  # already confirmed via the preflight search() above
    pyproject.write_text(text[:start] + new_table_text + text[end:], encoding="utf-8")
    return None


def materialize_nested_uv_editable_refs(
    dest_lib_dir: Path, *, canonical_root: Path, dest_root: Path,
) -> tuple[list[str], set[str]]:
    """After copying a canonical lib's own tree into ``dest_lib_dir``, fix
    up any `uv`-editable canonical-reference entry THAT LIB ITSELF
    declares. See this module's own docstring for the full rationale.

    The nested entry's ``raw_path`` (e.g. ``../agent-procutil``) is
    authored relative to the canonical lib's OWN directory (a sibling
    under ``libs/``). Since ``dest_lib_dir`` was copied to the exact same
    relative depth under its consumer (``<consumer>/libs/<lib>``,
    mirroring ``canonical_root/libs/<lib>``), that identical relative path
    resolves correctly to ``<consumer>/libs/<nested_lib>`` -- the SAME
    location a consumer's own direct dependency on that lib (if any) would
    also materialize to. If nothing has materialized it there yet, this
    copies it in; either way, the nested entry is rewritten to drop
    ``editable = true`` (its ``path`` needs no change -- see
    ``rewrite_nested_uv_editable_entry``).

    Mirrors ``materialize_main.py``'s own outer top-level containment
    checks exactly (found in review, PR #4372): (1) the UNRESOLVED
    canonical path is checked for a symlinked ancestor BEFORE ever calling
    ``.resolve()`` on it -- resolving first would silently follow (and
    erase) a symlink along the way; (2) ``raw_path`` must resolve to
    EXACTLY ``<consumer>/libs/<nested_lib>`` (derived independently of
    ``raw_path`` itself), not merely "somewhere inside the snapshot root"
    -- a looser check would accept an escaping entry such as
    ``../../plugins/other`` and copy canonical's `<nested_lib>` content
    into that OTHER project's directory while leaving the nested manifest
    pointing at the wrong path, silently shipping the wrong project.

    Returns ``(log, materialized_libs)`` -- ``materialized_libs`` names
    every ``nested_lib`` this call successfully handled (copied and/or
    rewrote), so the caller can fold it into its own alias-tracking: a
    LATER top-level entry for that same lib must take the rewrite-only
    "alias" path, not the hard "already exists" refusal, since this
    function already placed it at the exact sibling location a top-level
    entry would."""
    log: list[str] = []
    materialized: set[str] = set()
    dest_pyproject = dest_lib_dir / "pyproject.toml"
    if dest_pyproject.is_symlink():
        return ([
            f"SKIP {dest_pyproject}: is a symlink -- refusing to trust it "
            "for nested uv-editable canonical-reference expansion"
        ], materialized)
    try:
        nested_refs = uer.find_uv_editable_refs(dest_lib_dir)
    except uer.ManifestUnreadable as exc:
        return ([f"SKIP {dest_lib_dir}: {exc}"], materialized)
    canonical_root_r = canonical_root.resolve()
    dest_root_r = dest_root.resolve()
    # dest_lib_dir was placed at <dest_consumer_dir>/libs/<lib> by the
    # caller (materialize_main.py's own materialize_uv_editable_ref_into) --
    # the expected sibling location for a nested dependency is
    # <dest_consumer_dir>/libs/<nested_lib>, computed independently of
    # nested_raw_path so a crafted raw_path can never redirect the copy.
    dest_consumer_dir = dest_lib_dir.parent.parent.resolve()
    for nested_name, nested_raw_path, nested_lib, nested_editable in nested_refs:
        if not nested_editable:
            log.append(
                f"SKIP {dest_pyproject}: {nested_name} references {nested_raw_path} "
                "outside its own root but is missing editable = true -- "
                "refusing to ship an unresolved external reference"
            )
            continue
        if not uer.is_safe_lib_name(nested_lib):
            log.append(
                f"SKIP {dest_pyproject}: {nested_name} references "
                f"{nested_raw_path}, whose final path component "
                f"{nested_lib!r} is not a safe lib name -- refusing"
            )
            continue
        # Check the UNRESOLVED canonical path for a symlinked ancestor
        # BEFORE ever calling .resolve() on anything derived from it --
        # resolving first would silently follow (and erase) a symlink
        # along the way.
        canonical_unresolved = canonical_root_r / "libs" / nested_lib
        bad_ancestor = _find_symlinked_ancestor(canonical_unresolved, canonical_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_pyproject}: {bad_ancestor} is a symlink -- refusing")
            continue
        nested_canonical = canonical_unresolved.resolve()

        # Independently derive the EXPECTED sibling location (never from
        # nested_raw_path itself) before resolving anything -- also check
        # its own ancestors for a symlink up front, same as the canonical
        # side above.
        nested_dest_expected = dest_consumer_dir / "libs" / nested_lib
        bad_ancestor = _find_symlinked_ancestor(nested_dest_expected, dest_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {nested_dest_expected}: {bad_ancestor} is a symlink -- refusing")
            continue
        nested_dest = (dest_lib_dir / nested_raw_path).resolve()
        # Require the resolved path to be EXACTLY the expected sibling --
        # not merely "somewhere inside dest_root". A looser containment
        # check would accept an escaping raw_path (e.g.
        # ../../plugins/other) and copy canonical's <nested_lib> content
        # into that OTHER location while the manifest still points at the
        # wrong path.
        if nested_dest != nested_dest_expected.resolve():
            log.append(
                f"SKIP {dest_pyproject}: {nested_name} references "
                f"{nested_raw_path} (resolved {nested_dest}) which is not "
                f"<consumer>/libs/{nested_lib} -- refusing"
            )
            continue
        if not nested_canonical.is_dir():
            log.append(
                f"SKIP {dest_pyproject}: {nested_name} references "
                f"{nested_raw_path} (resolved canonical {nested_canonical}) "
                "which does not exist"
            )
            continue
        if nested_dest.exists() or nested_dest.is_symlink():
            # A pre-existing sibling at this path (from an earlier alias,
            # a stale prior release, or -- found in review -- a
            # non-directory artifact entirely) must never be silently
            # trusted as "already materialized": accept it only when it's
            # a real directory whose COMPLETE tree is byte-identical to
            # canonical, exactly the same guarantee a fresh copy would
            # provide. Anything else (a file, a symlink, or a directory
            # with mismatched/stale content) is refused outright rather
            # than rewriting the manifest over unverified content.
            if nested_dest.is_symlink():
                log.append(f"SKIP {nested_dest}: is a symlink -- refusing")
                continue
            if not nested_dest.is_dir():
                log.append(
                    f"SKIP {nested_dest}: already exists but is not a "
                    "directory -- refusing"
                )
                continue
            if not uer.lib_tree_matches(nested_canonical, nested_dest):
                log.append(
                    f"SKIP {nested_dest}: already exists but does not "
                    "match canonical -- refusing"
                )
                continue
        else:
            if not (nested_canonical / "src").is_dir():
                log.append(
                    f"SKIP {nested_dest}: {nested_canonical}/src not found "
                    "-- refusing (canonical lib source must exist)"
                )
                continue
            stray = _find_symlink(nested_canonical)
            if stray is not None:
                where = str(nested_canonical) if stray == "." else f"{nested_canonical}/{stray}"
                log.append(f"SKIP {nested_dest}: {where} is a symlink -- refusing")
                continue
            shutil.copytree(nested_canonical, nested_dest, ignore=_ignore)
        error = rewrite_nested_uv_editable_entry(
            pyproject=dest_pyproject, name=nested_name, raw_path=nested_raw_path,
        )
        if error is not None:
            log.append(error)
            continue
        log.append(f"OK   {nested_dest} <- {nested_raw_path} (nested in {dest_lib_dir})")
        materialized.add(nested_lib)
    return log, materialized
