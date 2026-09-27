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
no hyphen.
"""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"

# Consumer trees that sit outside plugins/ but still reference shared libs
# the same way -- mirrors sync-vendored-libs.py's own _EXTRA_CONSUMER_DIRS.
_EXTRA_CONSUMER_DIRS = ("worktree-manager",)


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


def find_uv_editable_refs(consumer_dir: Path) -> list[tuple[str, str, str]]:
    """Every ``[tool.uv.sources]`` entry in ``consumer_dir/pyproject.toml``
    whose ``path`` escapes ``consumer_dir``'s own root -- the `uv`-editable
    canonical-reference form (as opposed to the ordinary in-tree vendored-
    copy form, which stays within ``consumer_dir`` and is out of scope for
    this function). Returns ``(name, raw_path, lib)`` tuples regardless of
    whether the entry is well-formed; ``uv_editable_problems`` below judges
    validity."""
    pyproject = consumer_dir / "pyproject.toml"
    if pyproject.is_symlink():
        return []
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return []
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    consumer_root = consumer_dir.resolve()
    out: list[tuple[str, str, str]] = []
    for name, entry in sources.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        raw_path = entry["path"]
        if Path(raw_path).is_absolute():
            continue
        candidate = (consumer_dir / raw_path).resolve()
        if escapes_root(candidate, consumer_root):
            out.append((name, raw_path, Path(raw_path).name))
    return out


def uv_editable_problems(consumer: str, consumer_dir: Path) -> list[str]:
    """Validity problems in ``consumer``'s `uv`-editable canonical-reference
    entries: a missing ``editable = true`` (would silently resolve to a
    frozen, non-live copy on `dev` -- the exact hazard the second course
    correction exists to avoid) or a referenced canonical ``libs/<lib>``
    that does not exist."""
    pyproject = consumer_dir / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        return [f"{consumer}: could not parse pyproject.toml: {exc}"]
    sources = data.get("tool", {}).get("uv", {}).get("sources", {})
    problems: list[str] = []
    for name, raw_path, lib in find_uv_editable_refs(consumer_dir):
        entry = sources[name]
        if not entry.get("editable"):
            problems.append(
                f"{consumer}: {name} references {raw_path} outside its own "
                "root but is missing editable = true (would resolve to a "
                "frozen, non-live copy)"
            )
        canonical = (consumer_dir / raw_path).resolve()
        if not canonical.is_dir():
            problems.append(
                f"{consumer}: {name} references {raw_path} (resolved "
                f"{canonical}) which does not exist"
            )
        elif (LIBS_DIR / lib).resolve() != canonical:
            problems.append(
                f"{consumer}: {name} references {raw_path} (resolved "
                f"{canonical}) which is not libs/{lib}"
            )
    return problems


def uv_editable_relpath(consumer_dir: Path, lib: str) -> str:
    """``libs/<lib>`` expressed relative to ``consumer_dir`` (the base every
    ``[tool.uv.sources]`` ``path`` is resolved against) -- e.g.
    ``../../libs/<lib>`` for a ``plugins/<plugin>`` consumer,
    ``../libs/<lib>`` for a ``worktree-manager`` consumer."""
    return os.path.relpath(LIBS_DIR / lib, consumer_dir)


def rewrite_uv_source_to_editable(pyproject_path: Path, lib: str, relpath: str) -> None:
    """Surgically rewrite ``pyproject_path``'s ``[tool.uv.sources]`` entry
    whose ``path`` is the local in-tree ``libs/<lib>`` form into the
    `uv`-editable canonical-reference form -- preserving every other line
    (comments included), matching this repo's existing convention (see
    ``materialize_main.py``'s own ``_VERSION_RE.sub``) rather than a full
    TOML round-trip that would discard hand-authored comments."""
    text = pyproject_path.read_text(encoding="utf-8")
    pattern = re.compile(
        r'^([ \t]*[\w.-]+\s*=\s*)\{\s*path\s*=\s*"libs/' + re.escape(lib) + r'"\s*\}[ \t]*$',
        re.MULTILINE,
    )
    new_text, count = pattern.subn(
        lambda m: f'{m.group(1)}{{ path = "{relpath}", editable = true }}',
        text,
        count=1,
    )
    if count != 1:
        raise SystemExit(
            f'{pyproject_path}: could not find a [tool.uv.sources] entry '
            f'"path = \\"libs/{lib}\\"" to rewrite'
        )
    pyproject_path.write_text(new_text, encoding="utf-8")


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
    src_files,
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

    The ``repo``/``libs_dir``/``consumer_dir_of``/``find_symlinked_ancestor``/
    ``remove_path``/``is_pointer_copy``/``src_files`` parameters are the
    caller's (``sync-vendored-libs.py``'s) own constants/helpers, injected
    rather than imported -- this module has no hyphen in its filename and
    could be imported directly by the hyphenated CLI script, but the
    reverse isn't true, and duplicating this much validation logic a
    second time would itself risk the two copies drifting apart."""
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
            # from canonical (mirrors sync-vendored-libs.py's own
            # _materialize_blocked() "would this regress a consumer"
            # hazard, but in the opposite direction: here the local copy
            # is what would be discarded).
            canon_map = src_files(canonical)
            copy_map = src_files(copy_dir)
            if canon_map != copy_map:
                raise SystemExit(
                    f"{lib}: {copy_dir} differs from canonical libs/{lib} -- "
                    "refusing to discard local changes (run "
                    "--restore-canonical first, or reconcile manually)"
                )
        remove_path(copy_dir)

    relpath = uv_editable_relpath(consumer_dir, lib)
    rewrite_uv_source_to_editable(pyproject, lib, relpath)
    return copy_dir, relpath
