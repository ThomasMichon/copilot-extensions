"""Shared helpers for the `uv`-editable canonical-reference vendor-pointer
form (``vendor-pointer-generalization`` effort, Phase 1) -- a consumer's
``pyproject.toml`` ``[tool.uv.sources]`` entry whose ``path`` escapes the
consumer's own root (e.g. ``{ path = "../../libs/<lib>", editable = true }``)
instead of vendoring a local ``libs/<lib>`` copy at all.

Split out of ``tools/sync-vendored-libs.py`` (which stays the CLI entry point
for ``--check``/``--uv-editable``) purely to keep that hyphenated script
under this repo's per-module line-count cap (see CONTRIBUTING.md § Code
Style) -- a normal ``import`` is possible here (unlike the hyphenated
scripts, which resort to duplicating tiny helpers) since this file's name has
no hyphen. ``tools/materialize_main.py`` imports this module too (rather
than duplicating ``find_uv_editable_refs``/``escapes_root`` a second time),
since both the dev-time drift guard and the promotion-time rewriter must
agree on exactly which entries this reference form covers.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- see worktree_manager.source_config's own
    # identical fallback for the established pattern.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"

# Consumer trees that sit outside plugins/ but still reference shared libs
# the same way -- mirrors sync-vendored-libs.py's own _EXTRA_CONSUMER_DIRS.
_EXTRA_CONSUMER_DIRS = ("worktree-manager",)

_UV_SOURCES_HEADER_RE = re.compile(r'^\[tool\.uv\.sources\]\s*$', re.MULTILINE)
_TABLE_HEADER_RE = re.compile(r'^\[', re.MULTILINE)


class ManifestUnreadable(Exception):
    """Raised by ``find_uv_editable_refs`` when ``consumer_dir``'s
    ``pyproject.toml`` genuinely EXISTS but cannot be read or parsed --
    distinct from a genuinely absent manifest (a valid no-op for a
    payload-only consumer). A malformed/unreadable manifest must never be
    silently treated as "no references to validate": every caller catches
    this and surfaces it as an explicit problem/refusal instead."""


def escapes_root(candidate: Path, root: Path) -> bool:
    """True when ``candidate``'s resolved (symlink-followed) location is not
    ``root`` itself or a descendant of it. Mirrors ``materialize_main.py``'s
    own ``_escapes_root``/``sync-vendored-libs.py``'s own copy (kept
    separate for the same hyphenated-filename reason those two already
    duplicate small helpers between themselves)."""
    candidate_r = candidate.resolve()
    root_r = root.resolve()
    return candidate_r != root_r and root_r not in candidate_r.parents


def is_safe_lib_name(lib: str) -> bool:
    """True when ``lib`` is a plain single path component -- never empty,
    never ``.``/``..``, and never containing a path separator. A ``lib``
    interpolated into ``libs_dir / lib`` or ``consumer_dir / "libs" / lib``
    without this check lets a crafted value (e.g. ``"../outside"``) treat
    an arbitrary directory as the canonical lib, or delete/overwrite a path
    outside the intended ``libs/`` tree entirely (path traversal)."""
    return bool(lib) and lib not in (".", "..") and Path(lib).name == lib


def iter_consumer_dirs() -> list[tuple[str, Path]]:
    """``(consumer name, consumer dir)`` for every ``plugins/*`` plugin and
    every extra top-level consumer tree (``_EXTRA_CONSUMER_DIRS``) that has
    a ``pyproject.toml``."""
    out: list[tuple[str, Path]] = []
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(PLUGINS_DIR.iterdir()):
            if (plugin / "pyproject.toml").is_file():
                out.append((plugin.name, plugin))
    for extra in _EXTRA_CONSUMER_DIRS:
        consumer_dir = REPO / extra
        if (consumer_dir / "pyproject.toml").is_file():
            out.append((extra, consumer_dir))
    return out


def find_uv_editable_refs(consumer_dir: Path) -> list[tuple[str, str, str, bool]]:
    """Every ``[tool.uv.sources]`` entry in ``consumer_dir/pyproject.toml``
    whose ``path`` escapes ``consumer_dir``'s own root -- the `uv`-editable
    canonical-reference form (as opposed to the ordinary in-tree vendored-
    copy form, which stays within ``consumer_dir`` and is out of scope for
    this function). Returns ``(name, raw_path, lib, editable)`` tuples for
    EVERY escaping entry regardless of whether ``editable`` is actually set
    (a caller must not silently skip an escaping-but-non-editable entry --
    promotion in particular must fail closed on one rather than never
    seeing it at all, which would ship an external path unchanged). An
    absolute ``path`` is included too: joining an absolute path onto
    ``consumer_dir`` yields the absolute path itself, which almost always
    resolves outside ``consumer_dir`` and must be caught the same way a
    relative escaping path is, not silently treated as in-tree. ``editable``
    is exactly ``entry.get("editable") is True`` -- a truthy-but-non-boolean
    TOML value (``"false"``, ``1``) is never treated as the real, required
    ``editable = true``.

    Raises ``ManifestUnreadable`` (never returns ``[]``) when
    ``pyproject.toml`` EXISTS but cannot be read or parsed -- a genuinely
    absent manifest (a valid no-op for a payload-only consumer) is the only
    case that returns ``[]`` for that reason; a symlinked manifest also
    returns ``[]`` (a distinct, already-explicit refusal every caller
    checks for directly via ``.is_symlink()``)."""
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
        # A TOML-valid but structurally malformed manifest (e.g.
        # `[tool.uv] sources = []`) must never crash both --check and
        # promotion with a raw AttributeError from sources.items() below
        # -- fail closed through the same explicit diagnostic every other
        # malformed-manifest case already uses.
        raise ManifestUnreadable(f"{pyproject}: [tool.uv.sources] is not a table")
    consumer_root = consumer_dir.resolve()
    out: list[tuple[str, str, str, bool]] = []
    for name, entry in sources.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        raw_path = entry["path"]
        if not isinstance(raw_path, str):
            # A malformed-but-TOML-valid entry (e.g. `path = 1`) must never
            # crash `--check`/promotion with a raw TypeError from
            # `Path / raw_path` below -- treat it as an unreadable manifest
            # so every caller emits its normal diagnostic and fails closed.
            raise ManifestUnreadable(
                f"{pyproject}: [tool.uv.sources] {name!r}'s path is not a "
                f"string ({raw_path!r})"
            )
        candidate = (consumer_dir / raw_path).resolve()
        if escapes_root(candidate, consumer_root):
            out.append((name, raw_path, Path(raw_path).name, entry.get("editable") is True))
    return out


def _find_symlinked_ancestor(path: Path, root: Path) -> Path | None:
    """The first symlink among ``path`` itself and every ancestor directory
    up to and including ``root`` -- mirrors ``sync-vendored-libs.py``'s and
    ``materialize_main.py``'s own copies (kept separate for the same
    hyphenated-filename reason those two already duplicate small helpers
    between themselves). Used to catch a canonical ``libs/<lib>`` that IS a
    symlink even when its resolved target happens to equal the referenced
    path's own resolved target -- comparing only resolved paths would
    accept that case instead of rejecting the symlink outright, the same
    real-directory invariant ``convert_to_uv_editable()`` already enforces
    on the dev-time conversion side."""
    root_r = root.resolve()
    current = path
    while True:
        if current.is_symlink():
            return current
        if current.resolve() == root_r or current.parent == current:
            return None
        current = current.parent


def _find_symlink(tree: Path) -> str | None:
    """The first path (relative to ``tree``, or ``"."`` when ``tree``
    itself is the symlink) under ``tree`` that is a symlink, or ``None`` if
    none is found. Mirrors ``materialize_main.py``'s and
    ``sync-vendored-libs.py``'s own copies (kept separate for the same
    hyphenated-filename reason those two already duplicate small helpers
    between themselves). ``_find_symlinked_ancestor()`` above only catches
    ``libs/<lib>`` and its ANCESTORS being a symlink -- a symlink NESTED
    somewhere below it (e.g. ``libs/<lib>/src/foo.py -> /external/file``)
    is a distinct hazard this catches: the live `uv`-editable reference
    would expose that nested link directly on `dev`, and a byte comparison
    (``lib_tree_matches()``) would silently follow it too."""
    if tree.is_symlink():
        return "."
    if not tree.is_dir():
        return None
    for entry in sorted(tree.rglob("*")):
        if entry.is_symlink():
            return str(entry.relative_to(tree))
    return None


def uv_editable_problems(consumer: str, consumer_dir: Path) -> list[str]:
    """Validity problems in ``consumer``'s `uv`-editable canonical-reference
    entries: a missing ``editable = true`` (would silently resolve to a
    frozen, non-live copy on `dev` -- the exact hazard the second course
    correction exists to avoid), a referenced canonical ``libs/<lib>`` that
    does not exist, or one that IS a symlink (accepting a symlinked
    canonical lib root would let it point at an external tree instead of a
    real directory). A symlinked ``consumer_dir/pyproject.toml`` itself is
    an explicit problem, not silently "no references to validate" --
    ``find_uv_editable_refs()`` returns ``[]`` for one (fails closed on
    read), which would otherwise make a symlinked manifest look identical
    to a consumer with no `uv`-editable references at all, invisibly
    skipping validation (and letting promotion leave the symlink -- and
    whatever unresolved external source entry it hides -- in the
    snapshot)."""
    pyproject = consumer_dir / "pyproject.toml"
    if pyproject.is_symlink():
        return [
            f"{consumer}: pyproject.toml is a symlink -- refusing to trust "
            "it for uv-editable canonical-reference validation (could hide "
            "an unresolved external source entry from both --check and "
            "promotion)"
        ]
    try:
        refs = find_uv_editable_refs(consumer_dir)
    except ManifestUnreadable as exc:
        return [f"{consumer}: {exc}"]
    problems: list[str] = []
    for name, raw_path, lib, editable in refs:
        if not is_safe_lib_name(lib):
            problems.append(
                f"{consumer}: {name} references {raw_path}, whose final "
                f"path component {lib!r} is not a safe lib name (must be a "
                "single path component, never '..' or containing a "
                "separator) -- refusing"
            )
            continue
        if not editable:
            problems.append(
                f"{consumer}: {name} references {raw_path} outside its own "
                "root but is missing editable = true (would resolve to a "
                "frozen, non-live copy)"
            )
        canonical_unresolved = LIBS_DIR / lib
        bad_ancestor = _find_symlinked_ancestor(canonical_unresolved, REPO)
        canonical = (consumer_dir / raw_path).resolve()
        if bad_ancestor is not None:
            problems.append(
                f"{consumer}: {name} references {raw_path}, but "
                f"{bad_ancestor} is a symlink -- refusing (a canonical lib "
                "root must be a real directory)"
            )
        elif not canonical.is_dir():
            problems.append(
                f"{consumer}: {name} references {raw_path} (resolved "
                f"{canonical}) which does not exist"
            )
        elif canonical_unresolved.resolve() != canonical:
            problems.append(
                f"{consumer}: {name} references {raw_path} (resolved "
                f"{canonical}) which is not libs/{lib}"
            )
        elif not (canonical / "src").is_dir():
            # `--check` must not treat an existing-but-incomplete
            # libs/<lib> as valid: `uv` can't install it, and both
            # convert_to_uv_editable() and promotion already require this,
            # so a canonical lib missing it should never silently pass the
            # dev-time guard.
            problems.append(
                f"{consumer}: {name} references {raw_path} (resolved "
                f"{canonical}), but libs/{lib}/src does not exist"
            )
        elif not (canonical / "pyproject.toml").is_file():
            problems.append(
                f"{consumer}: {name} references {raw_path} (resolved "
                f"{canonical}), but libs/{lib}/pyproject.toml is missing"
            )
        else:
            # A symlink NESTED somewhere below canonical (e.g.
            # libs/<lib>/src/foo.py -> /external/file) is a distinct
            # hazard from libs/<lib> itself (or an ancestor) being a
            # symlink -- the live `uv`-editable reference exposes it
            # directly on `dev`, and it's never caught by the ancestor-
            # only check above.
            nested = _find_symlink(canonical)
            if nested is not None:
                where = f"libs/{lib}" if nested == "." else f"libs/{lib}/{nested}"
                problems.append(
                    f"{consumer}: {name} references {raw_path}, but "
                    f"{where} is a symlink -- refusing (a canonical lib "
                    "tree must contain only real files)"
                )
    return problems


def uv_editable_relpath(consumer_dir: Path, lib: str, *, libs_dir: Path | None = None) -> str:
    """``libs/<lib>`` expressed relative to ``consumer_dir`` (the base every
    ``[tool.uv.sources]`` ``path`` is resolved against) -- e.g.
    ``../../libs/<lib>`` for a ``plugins/<plugin>`` consumer,
    ``../libs/<lib>`` for a ``worktree-manager`` consumer. Always forward-
    slashed: ``os.path.relpath()`` returns native (backslash) separators on
    Windows, which would embed invalid escapes into the TOML basic string
    this value gets written into -- a plain string replace normalizes
    regardless of host OS, matching `uv`'s own portable path form (using
    ``Path(...).as_posix()`` would NOT work here: ``PurePosixPath`` never
    splits on a literal backslash, so it would pass a Windows-shaped path
    through unchanged when running on a POSIX host).

    ``libs_dir`` defaults to the module-level ``LIBS_DIR`` (the real repo's
    own canonical root) -- ``convert_to_uv_editable()`` passes its own
    INJECTED ``libs_dir`` explicitly instead, since it validates and
    removes paths under that caller-provided root: computing the
    replacement path from the global constant instead would let a caller
    using an alternate repository root delete the correct copy while
    writing a reference relative to a different checkout entirely."""
    return os.path.relpath((libs_dir or LIBS_DIR) / lib, consumer_dir).replace("\\", "/")


def uv_sources_table_span(text: str) -> tuple[int, int] | None:
    """The ``(start, end)`` character span of the ``[tool.uv.sources]``
    table body within ``text`` -- from just after its own header line to
    the next ``[...]`` table header (or EOF). Scoping every rewrite to
    this span, rather than the whole file, is what prevents an unrelated
    table that happens to contain an identical-looking
    ``{ path = "libs/<lib>" }`` value from being rewritten by mistake."""
    m = _UV_SOURCES_HEADER_RE.search(text)
    if m is None:
        return None
    start = m.end()
    next_header = _TABLE_HEADER_RE.search(text, start)
    end = next_header.start() if next_header else len(text)
    return start, end


def _uv_source_pattern(lib: str) -> re.Pattern[str]:
    return re.compile(
        r'^([ \t]*[\w.-]+\s*=\s*)\{\s*path\s*=\s*"libs/' + re.escape(lib) + r'"\s*\}[ \t]*$',
        re.MULTILINE,
    )


def can_rewrite_uv_source_to_editable(pyproject_path: Path, lib: str) -> bool:
    """True when ``pyproject_path`` actually has a rewritable, local
    in-tree ``[tool.uv.sources]`` entry for ``lib`` -- a dry validation a
    caller runs BEFORE any destructive action (e.g. deleting the local
    vendored copy), so a real rewrite failure can never happen only after
    the data it would have preserved has already been discarded."""
    text = pyproject_path.read_text(encoding="utf-8")
    span = uv_sources_table_span(text)
    if span is None:
        return False
    start, end = span
    return _uv_source_pattern(lib).search(text[start:end]) is not None


def rewrite_uv_source_to_editable(pyproject_path: Path, lib: str, relpath: str) -> None:
    """Surgically rewrite ``pyproject_path``'s ``[tool.uv.sources]`` entry
    whose ``path`` is the local in-tree ``libs/<lib>`` form into the
    `uv`-editable canonical-reference form -- preserving every other line
    (comments included) and scoped ONLY to the ``[tool.uv.sources]`` table
    body (see ``uv_sources_table_span``), so an identical-looking value in
    an unrelated table is never touched. Matches this repo's existing
    convention (see ``materialize_main.py``'s own ``_VERSION_RE.sub``)
    rather than a full TOML round-trip that would discard hand-authored
    comments. Callers should validate with ``can_rewrite_uv_source_to_editable``
    BEFORE any destructive action -- this function still raises on failure,
    but only as a last-line defense."""
    text = pyproject_path.read_text(encoding="utf-8")
    span = uv_sources_table_span(text)
    if span is None:
        raise SystemExit(f"{pyproject_path}: no [tool.uv.sources] table found")
    start, end = span
    new_table_text, count = _uv_source_pattern(lib).subn(
        lambda m: f'{m.group(1)}{{ path = "{relpath}", editable = true }}',
        text[start:end],
        count=1,
    )
    if count != 1:
        raise SystemExit(
            f'{pyproject_path}: could not find a [tool.uv.sources] entry '
            f'"path = \\"libs/{lib}\\"" to rewrite'
        )
    pyproject_path.write_text(text[:start] + new_table_text + text[end:], encoding="utf-8")


def _file_hashes(root: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every real file anywhere under ``root``
    (recursively), ignoring only generated/ephemeral artifacts
    (``__pycache__``, ``.pyc``/``.pyo``, ``*.egg-info``, plus the same build/tool-cache
    directory names ``materialize_main.py``'s own copytree ``ignore``
    callback excludes -- ``.git``, ``.pytest_cache``, ``.ruff_cache``,
    ``build``, ``dist`` -- so a fresh copytree that already omits these is
    never falsely reported as "not matching" a canonical tree that still
    carries them as ordinary local dev artifacts) -- never a fixed
    allowlist of expected subpaths, so an unexpected extra file (a
    root-level LICENSE, package metadata, or anything else) is never
    silently invisible to a caller that compares two trees for equality.
    Checks the ignored names against the path RELATIVE to ``root`` only
    (never the full absolute path) -- otherwise a checkout merely
    *located* under a directory named e.g. ``build`` would have every
    file's ``.parts`` match that ancestor name too, silently emptying the
    whole result and making any two trees compare as falsely equal."""
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


def lib_tree_matches(canonical: Path, copy_dir: Path) -> bool:
    """True when ``copy_dir``'s complete tree is byte-identical to
    ``canonical``'s -- every file anywhere under either directory,
    including any unexpected extra content (a root-level LICENSE, package
    metadata, or anything else). Used to gate
    ``convert_to_uv_editable()``'s destructive deletion of a real copy:
    comparing only a fixed allowlist of expected subpaths (``src/``,
    ``tests/``, ``README.md``, ``pyproject.toml``) would treat a copy
    carrying ANY additional file as agreeing, then silently discard that
    content once the local copy is removed."""
    return _file_hashes(canonical) == _file_hashes(copy_dir)


def convert_to_uv_editable(
    consumer: str,
    lib: str,
    *,
    repo: Path,
    libs_dir: Path,
    consumer_dir_of,
    find_symlinked_ancestor,
    remove_path,
    is_pointer_copy,
) -> tuple[Path, str]:
    """Convert ``<consumer's own dir>/libs/<lib>`` into the `uv`-editable
    canonical-reference form (vendor-pointer-generalization effort, Phase
    1): delete the local copy entirely (no directory, no stub -- nothing
    remains at that path) and rewrite the consuming ``pyproject.toml``'s
    ``[tool.uv.sources]`` entry from ``{ path = "libs/<lib>" }`` to
    ``{ path = "<relative-to-repo-root>/libs/<lib>", editable = true }``.

    Bidirectional by construction: works identically whether the local copy
    is a real copy (refuses if it has drifted from canonical -- converting
    a drifted copy would silently discard whatever local content it had
    that canonical didn't) or an already-``src-passthrough`` pointer copy
    (never the verified-agreeing "truth" itself, so no drift check applies
    -- it already forwards to canonical at runtime; this only replaces one
    live-reference mechanism with another).

    The rewrite is validated (``can_rewrite_uv_source_to_editable``) BEFORE
    the local copy is ever deleted, so a rewrite failure (e.g. an
    unexpectedly-formatted entry) never happens only after the data it
    would have preserved is already gone.

    The ``repo``/``libs_dir``/``consumer_dir_of``/``find_symlinked_ancestor``/
    ``remove_path``/``is_pointer_copy`` parameters are the caller's
    (``sync-vendored-libs.py``'s) own constants/helpers, injected rather
    than imported -- this module has no hyphen in its filename and could be
    imported directly by the hyphenated CLI script, but the reverse isn't
    true, and duplicating this much validation logic a second time would
    itself risk the two copies drifting apart."""
    if not is_safe_lib_name(lib):
        raise SystemExit(
            f"{lib!r}: not a valid lib name (must be a single path "
            "component, never '..' or containing a separator) -- refusing "
            "before touching any path built from it"
        )
    canonical = libs_dir / lib
    if not canonical.is_dir():
        raise SystemExit(f"{lib}: no canonical libs/{lib}/ to reference from")
    if canonical.is_symlink():
        raise SystemExit(
            f"libs/{lib} is a symlink -- refusing (a canonical lib root "
            "must be a real directory, not a link to an external tree)"
        )
    bad_ancestor = find_symlinked_ancestor(canonical, repo)
    if bad_ancestor is not None:
        raise SystemExit(
            f"{bad_ancestor} is a symlink -- refusing (a canonical lib "
            "root, and every ancestor between it and the repo root, must "
            "be a real directory)"
        )
    # A symlink NESTED somewhere below canonical (e.g.
    # libs/<lib>/src/foo.py -> /external/file) is a distinct hazard from
    # canonical itself (or an ancestor) being a symlink -- the resulting
    # `uv`-editable reference would expose it directly on `dev`, and
    # lib_tree_matches()'s own byte comparison would silently follow it
    # too. Matches materialize_main.py's equivalent recursive check.
    nested = _find_symlink(canonical)
    if nested is not None:
        where = f"libs/{lib}" if nested == "." else f"libs/{lib}/{nested}"
        raise SystemExit(
            f"{where} is a symlink -- refusing (a canonical lib tree must "
            "contain only real files)"
        )
    canon_pp = canonical / "pyproject.toml"
    if not canon_pp.is_file():
        raise SystemExit(f"{lib}: canonical libs/{lib}/pyproject.toml missing")

    consumer_dir = consumer_dir_of(consumer)
    pyproject = consumer_dir / "pyproject.toml"
    if not pyproject.is_file():
        raise SystemExit(f"{consumer}: no pyproject.toml found at {pyproject}")
    if pyproject.is_symlink():
        raise SystemExit(f"{pyproject}: is a symlink -- refusing to rewrite it blindly")
    if not can_rewrite_uv_source_to_editable(pyproject, lib):
        raise SystemExit(
            f'{pyproject}: no [tool.uv.sources] entry "path = \\"libs/{lib}\\"" '
            "to rewrite -- refusing before touching the local copy"
        )

    copy_dir = consumer_dir / "libs" / lib
    bad_ancestor = find_symlinked_ancestor(copy_dir, repo)
    if bad_ancestor is not None:
        raise SystemExit(
            f"{bad_ancestor} is a symlink -- refusing (a vendored copy "
            "root, and every ancestor between it and the repository root, "
            "must be a real directory)"
        )

    if copy_dir.exists() or copy_dir.is_symlink():
        if not is_pointer_copy(copy_dir):
            # A real (non-pointer) copy is this lib's verified-agreeing
            # truth -- refuse to discard it silently if it has drifted
            # from canonical across its COMPLETE tree (src/, tests/,
            # README.md, pyproject.toml -- not just src/, which would miss
            # an independently edited version/metadata/test file and then
            # silently discard it below). A nested symlink anywhere in
            # EITHER tree must be rejected first: lib_tree_matches()'s own
            # byte comparison would otherwise silently follow one,
            # transparently reading whatever external content it points at
            # instead of refusing outright.
            nested = _find_symlink(copy_dir)
            if nested is not None:
                where = str(copy_dir) if nested == "." else f"{copy_dir}/{nested}"
                raise SystemExit(
                    f"{where} is a symlink -- refusing (a vendored copy "
                    "must contain only real files)"
                )
            if not lib_tree_matches(canonical, copy_dir):
                raise SystemExit(
                    f"{lib}: {copy_dir} differs from canonical libs/{lib} -- "
                    "refusing to discard local changes (run "
                    "--restore-canonical first, or reconcile manually)"
                )
        remove_path(copy_dir)

    relpath = uv_editable_relpath(consumer_dir, lib, libs_dir=libs_dir)
    rewrite_uv_source_to_editable(pyproject, lib, relpath)
    return copy_dir, relpath
