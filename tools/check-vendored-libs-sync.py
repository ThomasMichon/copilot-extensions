#!/usr/bin/env python3
"""Guard vendored libs shared across plugins against silent drift (dotfiles #929).

Several shared libraries (``ssh-manager``, ``credential-relay``, ``zdd``,
``config-migrate``, ``plugin-resolve``) are **vendored per plugin** -- each
plugin carries its own copy under ``plugins/<plugin>/libs/<lib>`` because a
plugin installed standalone from the marketplace can only reference libs inside
its own directory (``[tool.uv.sources] <lib> = { path = "libs/<lib>" }``).

The hazard: every copy publishes the SAME distribution name and version
(``agent-ssh-manager==0.1.0-devN``). When agent-bridge's installer also installs
a sibling plugin (agent-codespaces) into the same venv, whichever copy is
installed last wins -- and if the copies' **source** has drifted, the daemon
silently runs the wrong code. This actually happened: a change added to
agent-bridge's ssh-manager (``build_remote_exec_args``) was not synced to
agent-codespaces's copy, so a redeploy that reinstalled the sibling downgraded
``ssh_manager`` and crashed the daemon on every CodeSpace dispatch with
``ImportError: cannot import name 'build_remote_exec_args'`` (dotfiles #929).

This check freezes the only invariant that keeps that safe:

* every lib with multiple *real* vendored copies must have a **byte-identical
  ``src/`` tree** across those copies (the importable surface -- the thing that
  actually gets installed and imported),
* any real vendored copy that coexists with a DRY ``VENDOR_POINTER.json`` copy,
  OR with a `uv`-editable canonical-reference pointer in some other consumer's
  own ``pyproject.toml`` (vendor-pointer-generalization effort -- no local
  copy directory at all; see ``uv_editable_ref.find_uv_editable_refs``), must
  also stay byte-identical to the top-level canonical ``libs/<lib>`` source,
  because neither pointer form is itself a shipped source tree. (Before this
  fix, ONLY the ``VENDOR_POINTER.json`` form pulled canonical into the
  comparison -- a lib with only real copies plus `uv`-editable pointers, like
  ``plugin-activation``, could have its canonical source edited without ever
  being re-synced into its real copies, and this check still reported OK
  because it never looked at canonical at all. PR #4942 review.) and
* all compared trees must declare the **same version** (same name + same
  version + same source => pip/uv can dedupe them and no "last writer wins on
  identical version" skew is possible).

Deliberately NOT compared: ``pyproject.toml`` build-dependency pins and
``README`` -- Dependabot bumps a single copy's build deps at a time and that is
harmless as long as the source and version agree. (The version line *is*
checked, separately, from each pyproject.)

Usage::

    python tools/check-vendored-libs-sync.py          # verify (CI / pre-push)
    python tools/check-vendored-libs-sync.py --list     # show the vendored-lib map
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uv_editable_ref as uer  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
POINTER_NAME = "VENDOR_POINTER.json"

# Subtrees under a lib copy that are build/artifact noise, never source of truth.
_IGNORE_PARTS = {"build", ".venv", "__pycache__", "dist"}
_VERSION_RE = re.compile(r'^\s*version\s*=\s*"([^"]+)"', re.MULTILINE)


def _lib_copies() -> dict[str, list[Path]]:
    """Map ``lib name -> [copy paths]`` for every vendored-lib copy.

    Scans two shapes: ``plugins/<plugin>/libs/*`` (the common case) and any
    other top-level package directory that vendors libs directly under its
    own ``libs/`` (e.g. ``worktree-manager/libs/*``, which isn't a
    marketplace plugin and isn't nested under ``plugins/``).
    """
    copies: dict[str, list[Path]] = {}
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(PLUGINS_DIR.iterdir()):
            libs = plugin / "libs"
            if not libs.is_dir():
                continue
            for lib in sorted(libs.iterdir()):
                if lib.is_dir():
                    copies.setdefault(lib.name, []).append(lib)
    for extra in ("worktree-manager",):
        libs = REPO / extra / "libs"
        if not libs.is_dir():
            continue
        for lib in sorted(libs.iterdir()):
            if lib.is_dir():
                copies.setdefault(lib.name, []).append(lib)
    return copies


def _is_pointer_copy(lib_dir: Path) -> bool:
    return (lib_dir / POINTER_NAME).is_file()


def _editable_pointer_consumers() -> dict[str, list[str]]:
    """Map ``lib name -> [consumer names]`` for every consumer that references
    ``lib`` via a `uv`-editable canonical-reference pointer in its own
    ``pyproject.toml`` (vendor-pointer-generalization effort) -- no local
    ``libs/<lib>`` copy directory at all, so ``_lib_copies()`` never sees
    these consumers. Mirrors ``check-version-bump.py``'s own identical scan
    (``_vendored_consumers``'s editable-ref half), including its symlink
    preflight: ``iter_consumer_dirs()`` filters by ``pyproject.toml.is_file()``,
    which is ALSO False for a symlink (valid or dangling) or a symlink-to-
    directory -- so a symlinked manifest would otherwise vanish from the scan
    before ``find_uv_editable_refs``'s own symlink check ever saw it, letting
    a stale real copy pass with canonical silently omitted from comparison
    (PR #4954 review)."""
    candidate_dirs: list[tuple[str, Path]] = []
    if PLUGINS_DIR.is_dir():
        candidate_dirs.extend(
            (p.name, p) for p in sorted(PLUGINS_DIR.iterdir()) if p.is_dir()
        )
    candidate_dirs.extend(
        (extra, REPO / extra) for extra in uer._EXTRA_CONSUMER_DIRS
        if (REPO / extra).is_dir()
    )
    for name, consumer_dir in candidate_dirs:
        pyproject = consumer_dir / "pyproject.toml"
        if pyproject.is_symlink():
            raise SystemExit(
                f"check-vendored-libs-sync: {pyproject} is a symlink -- "
                f"cannot safely determine {name}'s uv-editable consumers; "
                "replace it with a real file."
            )
    consumers: dict[str, list[str]] = {}
    for name, consumer_dir in uer.iter_consumer_dirs():
        try:
            refs = uer.find_uv_editable_refs(consumer_dir)
        except uer.ManifestUnreadable as exc:
            # Fail closed, same rationale as check-version-bump.py's
            # identical call site: an unreadable manifest could genuinely
            # reference a lib this check must not silently treat as having
            # no editable-pointer consumers.
            raise SystemExit(
                f"check-vendored-libs-sync: {exc} -- cannot safely determine "
                f"{name}'s uv-editable consumers; fix its pyproject.toml "
                "[tool.uv.sources] table."
            ) from exc
        for _source_name, _raw_path, lib, editable in refs:
            if editable:
                consumers.setdefault(lib, []).append(name)
    return consumers


def _src_files(lib_dir: Path) -> dict[str, str]:
    """Relative-path -> sha256 for every file under ``<lib>/src`` (artifacts skipped)."""
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
    return m.group(1) if m else None


def _is_cross_checked(
    real_paths: list[Path], pointer_paths: list[Path], editable_consumers: list[str],
) -> bool:
    """True when this lib has more than one copy/consumer this check
    actually cross-verifies: 2+ real copies, or real copies alongside a
    pointer in EITHER form (a ``VENDOR_POINTER.json`` copy or a `uv`-
    editable canonical-reference consumer). Shared by ``verify()``,
    ``_print_list()``, and ``main()``'s success count so the three never
    drift apart on what counts as "checked" (PR #4954 review: the inventory
    and success-count predicates had fallen out of sync with ``verify()``'s
    own, newly-expanded one)."""
    return len(real_paths) >= 2 or bool((pointer_paths or editable_consumers) and real_paths)


def verify() -> list[str]:
    """Return human-readable problems; empty means the check passes."""
    problems: list[str] = []
    base = PLUGINS_DIR.parent  # REPO in production; the tmp root under test
    editable_consumers_by_lib = _editable_pointer_consumers()
    for lib, paths in _lib_copies().items():
        pointer_paths = [p for p in paths if _is_pointer_copy(p)]
        real_paths = [p for p in paths if not _is_pointer_copy(p)]
        editable_consumers = editable_consumers_by_lib.get(lib, [])
        compare_paths = list(real_paths)
        canonical = LIBS_DIR / lib
        # Pointer copies (VENDOR_POINTER.json OR a `uv`-editable canonical
        # reference with no local copy at all) never carry the real
        # importable source tree; once a lib is mixed real+pointer in either
        # form, canonical becomes the only meaningful byte-identical
        # reference for the real copies.
        if (pointer_paths or editable_consumers) and real_paths:
            if canonical.is_dir():
                compare_paths.insert(0, canonical)
            else:
                problems.append(
                    f"{lib}: canonical libs/{lib} missing while real and pointer "
                    "copies coexist -- cannot verify the real copy stays in sync"
                )
                continue
        if len(compare_paths) < 2:
            continue
        rel_names = [
            f"libs/{lib}" if p == canonical else p.relative_to(base).as_posix()
            for p in compare_paths
        ]

        # 1) src/ trees must be byte-identical across all copies.
        maps = [_src_files(p) for p in compare_paths]
        ref_map, ref_name = maps[0], rel_names[0]
        for other_map, other_name in zip(maps[1:], rel_names[1:], strict=True):
            all_rel = set(ref_map) | set(other_map)
            for rel in sorted(all_rel):
                a, b = ref_map.get(rel), other_map.get(rel)
                if a is None:
                    problems.append(
                        f"{lib}: src/{rel} missing in {ref_name} "
                        f"(present in {other_name})"
                    )
                elif b is None:
                    problems.append(
                        f"{lib}: src/{rel} missing in {other_name} "
                        f"(present in {ref_name})"
                    )
                elif a != b:
                    problems.append(
                        f"{lib}: src/{rel} DIFFERS between {ref_name} and {other_name} "
                        "-- re-sync the vendored copies"
                    )

        # 2) declared versions must all match. Pointer copies are excluded
        # from the src/ byte comparison above, but their declared versions
        # still matter: a mixed real+pointer set that publishes the same
        # distribution under different versions is still install-order skew.
        version_paths = list(compare_paths)
        if pointer_paths:
            version_paths.extend(pointer_paths)
        versions = {
            (
                f"libs/{lib}" if p == canonical else p.relative_to(base).as_posix()
            ): _declared_version(p)
            for p in version_paths
        }
        distinct = {v for v in versions.values() if v is not None}
        if len(distinct) > 1:
            detail = ", ".join(f"{n}={v}" for n, v in versions.items())
            problems.append(
                f"{lib}: version skew across copies ({detail}) "
                "-- bump all copies to the same version"
            )
        missing_ver = [n for n, v in versions.items() if v is None]
        if missing_ver:
            problems.append(f"{lib}: no version declared in {', '.join(missing_ver)}")
    return problems


def _print_list() -> None:
    base = PLUGINS_DIR.parent
    editable_consumers_by_lib = _editable_pointer_consumers()
    for lib, paths in sorted(_lib_copies().items()):
        pointer_paths = [p for p in paths if _is_pointer_copy(p)]
        real_paths = [p for p in paths if not _is_pointer_copy(p)]
        editable_consumers = editable_consumers_by_lib.get(lib, [])
        if not _is_cross_checked(real_paths, pointer_paths, editable_consumers):
            continue
        compare_root = real_paths[0] if real_paths else paths[0]
        ver = _declared_version(compare_root) or "?"
        extras = []
        if real_paths:
            extras.append(f"{len(real_paths)} real")
        if pointer_paths:
            extras.append(f"{len(pointer_paths)} pointer")
        if editable_consumers:
            extras.append(f"{len(editable_consumers)} uv-editable")
        print(f"{lib}  (v{ver}, {', '.join(extras)}):")
        for p in real_paths:
            print(f"    {p.relative_to(base)}")
        if pointer_paths:
            print("    [pointer copies excluded from byte-identity comparison]")
            for p in pointer_paths:
                print(f"    {p.relative_to(base)}")
        if editable_consumers:
            print("    [uv-editable consumers, no local copy -- canonical IS their payload]")
            for name in editable_consumers:
                print(f"    {name} (via libs/{lib})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--list", action="store_true", help="print the vendored-lib map and exit")
    args = ap.parse_args()
    if args.list:
        _print_list()
        return 0
    problems = verify()
    if problems:
        print("check-vendored-libs-sync: FAILED", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            "\nRe-sync the drifted vendored lib copies so every plugin ships the "
            "same source (and version). A source change to one copy MUST be "
            "propagated to all, with a version bump -- otherwise a sibling-plugin "
            "install can silently downgrade the shared package (dotfiles #929).",
            file=sys.stderr,
        )
        return 1
    editable_consumers_by_lib = _editable_pointer_consumers()
    shared = {}
    for lib, paths in _lib_copies().items():
        pointer_paths = [p for p in paths if _is_pointer_copy(p)]
        real_paths = [p for p in paths if not _is_pointer_copy(p)]
        editable_consumers = editable_consumers_by_lib.get(lib, [])
        if _is_cross_checked(real_paths, pointer_paths, editable_consumers):
            shared[lib] = paths
    print(f"check-vendored-libs-sync: OK ({len(shared)} shared libs in sync).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
