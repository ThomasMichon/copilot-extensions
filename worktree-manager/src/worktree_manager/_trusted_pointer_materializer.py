"""Trusted, LOCALLY-DEFINED vendor-pointer materializer for self_install.py.

This is a deliberate, hand-maintained COPY of the legacy directory-pointer
compatibility logic and the still-live `uv`-editable canonical-reference
expansion logic that originally shipped from ``tools/materialize_main.py`` --
NOT a dynamic src-passthrough pointer, and NOT dynamically loaded from a
fetched repository file.

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

Keep the still-shared `uv`-editable logic in sync BY HAND with
``tools/materialize_main.py`` / ``tools/uv_editable_ref.py`` when that
reference-rewrite behavior changes. The legacy directory-pointer path that
only this trusted compatibility reader still carries is independently
maintained here on purpose. This file's whole point is to NOT be kept in sync
via dynamic loading, so no import-time or runtime mechanism enforces
agreement. The shipped compatibility path is therefore covered directly by
``worktree-manager/tests/test_trusted_pointer_materializer.py`` and the
higher-level ``test_self_install.py`` scenarios whenever the legacy
directory-pointer behavior changes, while
``test_trusted_materializer_parity.py`` continues to guard the still-shared
`uv`-editable rewrite logic against drift from the repo tooling copy.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors tools/uv_editable_ref.py's own
    # identical fallback (this module's hand-maintained-copy rationale
    # applies to that import choice too, not just the expansion logic).
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

POINTER_NAME = "VENDOR_POINTER.json"
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)
_UV_SOURCES_HEADER_RE = re.compile(r'^\[tool\.uv\.sources\]\s*$', re.MULTILINE)
_TABLE_HEADER_RE = re.compile(r'^\[', re.MULTILINE)


class ManifestUnreadable(Exception):
    """Raised by ``find_uv_editable_refs`` when a consumer's
    ``pyproject.toml`` genuinely EXISTS but cannot be read or parsed --
    mirrors ``tools/uv_editable_ref.py``'s identically-named exception; see
    that module's docstring for why a malformed manifest must never look
    like "no references at all"."""


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


# ── `uv`-editable canonical-reference expansion ──────────────────────────
#
# Mirrors ``tools/uv_editable_ref.py``'s ``find_uv_editable_refs``/
# ``uv_sources_table_span`` and ``tools/materialize_main.py``'s
# ``materialize_uv_editable_ref_into`` -- see this module's own docstring
# for why these are a hand-maintained duplicate rather than an import.
# Scoped to what a single self-installed payload actually needs: ONE
# consumer (``worktree-manager`` itself, never a whole-repo ``plugins/*``
# sweep, which self_install.py never copies), so there is no
# ``materialize_uv_editable_refs()`` (plural, whole-tree) counterpart here.


def _is_safe_lib_name(lib: str) -> bool:
    """True when ``lib`` is a plain single path component -- mirrors
    ``uv_editable_ref.is_safe_lib_name``; see that function's docstring
    for the path-traversal hazard this guards against."""
    return bool(lib) and lib not in (".", "..") and Path(lib).name == lib


def find_uv_editable_refs(consumer_dir: Path) -> list[tuple[str, str, str, bool]]:
    """Every ``[tool.uv.sources]`` entry in ``consumer_dir/pyproject.toml``
    whose ``path`` escapes ``consumer_dir``'s own root -- mirrors
    ``uv_editable_ref.find_uv_editable_refs``; see that function's
    docstring for the full contract (including why a malformed manifest
    raises ``ManifestUnreadable`` rather than returning ``[]``)."""
    pyproject = consumer_dir / "pyproject.toml"
    if pyproject.is_symlink():
        return []
    if not pyproject.is_file():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ManifestUnreadable(f"{pyproject}: could not read/parse: {exc}") from exc
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ManifestUnreadable(f"{pyproject}: [tool] is not a table")
    uv_table = tool.get("uv", {})
    if not isinstance(uv_table, dict):
        raise ManifestUnreadable(f"{pyproject}: [tool.uv] is not a table")
    sources = uv_table.get("sources", {})
    if not isinstance(sources, dict):
        raise ManifestUnreadable(f"{pyproject}: [tool.uv.sources] is not a table")
    consumer_root = consumer_dir.resolve()
    out: list[tuple[str, str, str, bool]] = []
    for name, entry in sources.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        raw_path = entry["path"]
        if not isinstance(raw_path, str):
            raise ManifestUnreadable(
                f"{pyproject}: [tool.uv.sources] {name!r}'s path is not a "
                f"string ({raw_path!r})"
            )
        candidate = (consumer_dir / raw_path).resolve()
        if _escapes_root(candidate, consumer_root):
            out.append((name, raw_path, Path(raw_path).name, entry.get("editable") is True))
    return out


def uv_sources_table_span(text: str) -> tuple[int, int] | None:
    """The ``(start, end)`` character span of the ``[tool.uv.sources]``
    table body within ``text`` -- mirrors ``uv_editable_ref.
    uv_sources_table_span``."""
    m = _UV_SOURCES_HEADER_RE.search(text)
    if m is None:
        return None
    start = m.end()
    next_header = _TABLE_HEADER_RE.search(text, start)
    end = next_header.start() if next_header else len(text)
    return start, end


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


def _rewrite_uv_editable_source_entry(
    *, pyproject: Path, name: str, raw_path: str, lib: str
) -> str | None:
    """Mirrors ``materialize_main.py``'s identically-named helper -- see
    its docstring for the rewrite contract."""
    text = pyproject.read_text(encoding="utf-8")
    span = uv_sources_table_span(text)
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


def _uv_editable_ignore(_dir: str, names: list[str]) -> set[str]:
    """Mirrors ``materialize_main.py``'s own ``_ignore`` -- excludes build/
    cache artifacts from the canonical-lib-tree copy."""
    return {n for n in names if n in {
        ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo")) or n.endswith(".egg-info")}


def _file_hashes(root: Path) -> dict[str, str]:
    """Mirrors ``uv_editable_ref.py``'s identically-named helper --
    relative-path -> sha256 for every real file anywhere under ``root``,
    ignoring the same build/tool-cache directory names a copytree's own
    ``ignore`` callback excludes (so a fresh copy is never falsely
    reported as "not matching" a canonical tree that still carries them
    as ordinary local dev artifacts)."""
    ignored_dirs = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        rel_parts = set(f.relative_to(root).parts)
        if (
            ignored_dirs & rel_parts
            or any(part.endswith(".egg-info") for part in rel_parts)
            or f.suffix in (".pyc", ".pyo")
        ):
            continue
        out[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def _lib_tree_matches(canonical: Path, copy_dir: Path) -> bool:
    """Mirrors ``uv_editable_ref.py``'s ``lib_tree_matches`` -- True when
    ``copy_dir``'s complete tree is byte-identical to ``canonical``'s."""
    return _file_hashes(canonical) == _file_hashes(copy_dir)


def _materialize_one_uv_editable_ref(
    *, canonical: Path, dest_lib_dir: Path, pyproject: Path, name: str, raw_path: str, lib: str,
) -> str:
    """Mirrors ``materialize_main.py``'s identically-named helper -- see
    its docstring for the preflight-then-copy-then-rewrite contract."""
    if not (canonical / "src").is_dir():
        return f"SKIP {dest_lib_dir}: {canonical}/src not found -- refusing (canonical lib source must exist)"
    if not (canonical / "pyproject.toml").is_file():
        return f"SKIP {dest_lib_dir}: {canonical}/pyproject.toml missing -- refusing"
    stray = _find_symlink(canonical)
    if stray is not None:
        where = str(canonical) if stray == "." else f"{canonical}/{stray}"
        return f"SKIP {dest_lib_dir}: {where} is a symlink -- refusing"

    text = pyproject.read_text(encoding="utf-8")
    span = uv_sources_table_span(text)
    if span is None:
        return f"SKIP {pyproject}: no [tool.uv.sources] table found"
    start, end = span
    pattern = _uv_source_entry_pattern(name, raw_path)
    if pattern.search(text[start:end]) is None:
        return f"SKIP {pyproject}: could not find {name}'s uv-editable entry to rewrite"

    shutil.copytree(canonical, dest_lib_dir, ignore=_uv_editable_ignore)
    error = _rewrite_uv_editable_source_entry(
        pyproject=pyproject, name=name, raw_path=raw_path, lib=lib
    )
    if error is not None:
        return error
    return f"OK   {dest_lib_dir} <- {raw_path}"


def _rewrite_nested_uv_editable_entry(
    *, pyproject: Path, name: str, raw_path: str
) -> str | None:
    """Mirrors ``materialize_main.py``'s identically-named helper -- see
    its docstring for why a NESTED entry (one belonging to a just-copied
    canonical lib's own manifest, not a top-level consumer's) is rewritten
    to drop ``editable = true`` while keeping its ``path`` unchanged."""
    text = pyproject.read_text(encoding="utf-8")
    span = uv_sources_table_span(text)
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


def _materialize_nested_uv_editable_refs(
    dest_lib_dir: Path, *, canonical_root: Path, dest_root: Path,
) -> tuple[list[str], set[str]]:
    """Mirrors ``materialize_main.py``'s identically-named function -- see
    its docstring for the full contract (PR #4372: a canonical lib such as
    ``ssh-manager`` can itself depend on another canonical lib such as
    ``agent-procutil`` via its own escaping `uv`-editable entry; the outer
    rewrite only fixes the CONSUMER's top-level entry, leaving the
    just-copied lib's own nested entry still ``editable = true``).

    Hardened per PR #4372's second review round: (1) the UNRESOLVED
    canonical path is checked for a symlinked ancestor BEFORE ever calling
    ``.resolve()`` on it; (2) ``raw_path`` must resolve to EXACTLY
    ``<consumer>/libs/<nested_lib>`` (derived independently of
    ``raw_path``), not merely "somewhere inside the snapshot root"."""
    log: list[str] = []
    materialized: set[str] = set()
    dest_pyproject = dest_lib_dir / "pyproject.toml"
    if dest_pyproject.is_symlink():
        return ([
            f"SKIP {dest_pyproject}: is a symlink -- refusing to trust it "
            "for nested uv-editable canonical-reference expansion"
        ], materialized)
    try:
        nested_refs = find_uv_editable_refs(dest_lib_dir)
    except ManifestUnreadable as exc:
        return ([f"SKIP {dest_lib_dir}: {exc}"], materialized)
    canonical_root_r = canonical_root.resolve()
    dest_root_r = dest_root.resolve()
    # dest_lib_dir was placed at <dest_consumer_dir>/libs/<lib> by the
    # caller -- the expected sibling location for a nested dependency is
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
        if not _is_safe_lib_name(nested_lib):
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
            # A pre-existing sibling at this path must never be silently
            # trusted as "already materialized" -- accept it only when
            # it's a real directory whose COMPLETE tree is byte-identical
            # to canonical.
            if nested_dest.is_symlink():
                log.append(f"SKIP {nested_dest}: is a symlink -- refusing")
                continue
            if not nested_dest.is_dir():
                log.append(
                    f"SKIP {nested_dest}: already exists but is not a "
                    "directory -- refusing"
                )
                continue
            if not _lib_tree_matches(nested_canonical, nested_dest):
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
            shutil.copytree(nested_canonical, nested_dest, ignore=_uv_editable_ignore)
        error = _rewrite_nested_uv_editable_entry(
            pyproject=dest_pyproject, name=nested_name, raw_path=nested_raw_path,
        )
        if error is not None:
            log.append(error)
            continue
        log.append(f"OK   {nested_dest} <- {nested_raw_path} (nested in {dest_lib_dir})")
        materialized.add(nested_lib)
    return log, materialized


def materialize_uv_editable_ref_into(
    *, source_consumer_dir: Path, dest_consumer_dir: Path, canonical_root: Path,
    dest_root: Path | None = None,
) -> list[str]:
    """Expand every `uv`-editable canonical-reference entry declared in
    ``source_consumer_dir``'s ``pyproject.toml`` into ``dest_consumer_dir``
    -- mirrors ``materialize_main.py``'s identically-named function; see
    its docstring for the full contract. Scoped here to a single consumer
    (self_install.py never copies a whole-repo ``plugins/*`` tree), so
    there is no whole-tree "alias" tracking across multiple consumers --
    only the within-consumer alias case (two ``[tool.uv.sources]`` names in
    the SAME manifest pointing at the same canonical lib) is preserved,
    matching the upstream function's own behavior for a single call."""
    log: list[str] = []
    dest_root_r = (dest_root or dest_consumer_dir).resolve()
    bad_ancestor = _find_symlinked_ancestor(dest_consumer_dir, dest_root_r)
    if bad_ancestor is not None:
        return [f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing"]
    materialized_libs: set[str] = set()
    source_pyproject = source_consumer_dir / "pyproject.toml"
    if source_pyproject.is_symlink():
        return [
            f"SKIP {dest_consumer_dir}: {source_pyproject} is a symlink -- "
            "refusing to trust it for uv-editable canonical-reference "
            "expansion"
        ]
    if canonical_root.is_symlink():
        return [f"SKIP {dest_consumer_dir}: {canonical_root} is a symlink -- refusing"]
    canonical_root_r = canonical_root.resolve()
    try:
        refs = find_uv_editable_refs(source_consumer_dir)
    except ManifestUnreadable as exc:
        return [f"SKIP {dest_consumer_dir}: {exc}"]
    for name, raw_path, lib, editable in refs:
        if not editable:
            log.append(
                f"SKIP {dest_consumer_dir}: {name} references {raw_path} "
                "outside its own root but is missing editable = true -- "
                "refusing to ship an unresolved external reference"
            )
            continue
        if not _is_safe_lib_name(lib):
            log.append(
                f"SKIP {dest_consumer_dir}: {name} references {raw_path}, "
                f"whose final path component {lib!r} is not a safe lib "
                "name -- refusing"
            )
            continue
        canonical_unresolved = canonical_root_r / "libs" / lib
        bad_ancestor = _find_symlinked_ancestor(canonical_unresolved, canonical_root_r)
        if bad_ancestor is not None:
            log.append(f"SKIP {dest_consumer_dir}: {bad_ancestor} is a symlink -- refusing")
            continue
        canonical = (source_consumer_dir / raw_path).resolve()
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
            log.append(f"SKIP {pyproject}: is a symlink -- refusing to rewrite it blindly")
            continue

        if lib in materialized_libs:
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
            # agent-procutil) -- mirrors materialize_main.py's identically
            # -named function; see its docstring for the full rationale
            # (PR #4372). Nested libs successfully handled are folded into
            # `materialized_libs` too, so a LATER top-level entry for that
            # same lib takes the alias (rewrite-only) path, not the hard
            # "already exists" refusal.
            nested_log, nested_materialized = _materialize_nested_uv_editable_refs(
                dest_lib_dir, canonical_root=canonical_root_r, dest_root=dest_root_r,
            )
            log.extend(nested_log)
            materialized_libs.update(nested_materialized)
    return log
