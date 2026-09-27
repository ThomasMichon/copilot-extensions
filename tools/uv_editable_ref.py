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


def escapes_root(candidate: Path, root: Path) -> bool:
    """True when ``candidate``'s resolved (symlink-followed) location is not
    ``root`` itself or a descendant of it. Mirrors ``materialize_main.py``'s
    own ``_escapes_root``/``sync-vendored-libs.py``'s own copy (kept
    separate for the same hyphenated-filename reason those two already
    duplicate small helpers between themselves)."""
    candidate_r = candidate.resolve()
    root_r = root.resolve()
    return candidate_r != root_r and root_r not in candidate_r.parents


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
    ``editable = true``."""
    pyproject = consumer_dir / "pyproject.toml"
    if pyproject.is_symlink():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    consumer_root = consumer_dir.resolve()
    out: list[tuple[str, str, str, bool]] = []
    for name, entry in sources.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        raw_path = entry["path"]
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
    problems: list[str] = []
    for name, raw_path, lib, editable in find_uv_editable_refs(consumer_dir):
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
    return problems


def uv_editable_relpath(consumer_dir: Path, lib: str) -> str:
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
    through unchanged when running on a POSIX host)."""
    return os.path.relpath(LIBS_DIR / lib, consumer_dir).replace("\\", "/")


def _uv_sources_table_span(text: str) -> tuple[int, int] | None:
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
    span = _uv_sources_table_span(text)
    if span is None:
        return False
    start, end = span
    return _uv_source_pattern(lib).search(text[start:end]) is not None


def rewrite_uv_source_to_editable(pyproject_path: Path, lib: str, relpath: str) -> None:
    """Surgically rewrite ``pyproject_path``'s ``[tool.uv.sources]`` entry
    whose ``path`` is the local in-tree ``libs/<lib>`` form into the
    `uv`-editable canonical-reference form -- preserving every other line
    (comments included) and scoped ONLY to the ``[tool.uv.sources]`` table
    body (see ``_uv_sources_table_span``), so an identical-looking value in
    an unrelated table is never touched. Matches this repo's existing
    convention (see ``materialize_main.py``'s own ``_VERSION_RE.sub``)
    rather than a full TOML round-trip that would discard hand-authored
    comments. Callers should validate with ``can_rewrite_uv_source_to_editable``
    BEFORE any destructive action -- this function still raises on failure,
    but only as a last-line defense."""
    text = pyproject_path.read_text(encoding="utf-8")
    span = _uv_sources_table_span(text)
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


def _file_hashes(lib_dir: Path, sub: str) -> dict[str, str]:
    """Relative-path -> sha256 for every file under ``lib_dir/sub``."""
    root = lib_dir / sub
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for f in root.rglob("*"):
        if not f.is_file():
            continue
        if "__pycache__" in f.parts or f.suffix in (".pyc", ".pyo"):
            continue
        out[f.relative_to(root).as_posix()] = hashlib.sha256(f.read_bytes()).hexdigest()
    return out


def lib_tree_matches(canonical: Path, copy_dir: Path) -> bool:
    """True when ``copy_dir``'s complete discardable tree -- ``src/``,
    ``tests/``, ``README.md``, and ``pyproject.toml`` -- is byte-identical
    to ``canonical``'s. Used to gate ``convert_to_uv_editable()``'s
    destructive deletion of a real copy: comparing only ``src/`` (as this
    repo's other drift checks historically have) would treat a copy with
    an independently edited version/README/tests as agreeing, then
    silently discard those files once the local copy is removed."""
    for sub in ("src", "tests"):
        if _file_hashes(canonical, sub) != _file_hashes(copy_dir, sub):
            return False
    for fname in ("README.md", "pyproject.toml"):
        a_file, b_file = canonical / fname, copy_dir / fname
        a_bytes = a_file.read_bytes() if a_file.is_file() else None
        b_bytes = b_file.read_bytes() if b_file.is_file() else None
        if a_bytes != b_bytes:
            return False
    return True


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
            # silently discard it below).
            if not lib_tree_matches(canonical, copy_dir):
                raise SystemExit(
                    f"{lib}: {copy_dir} differs from canonical libs/{lib} -- "
                    "refusing to discard local changes (run "
                    "--restore-canonical first, or reconcile manually)"
                )
        remove_path(copy_dir)

    relpath = uv_editable_relpath(consumer_dir, lib)
    rewrite_uv_source_to_editable(pyproject, lib, relpath)
    return copy_dir, relpath

