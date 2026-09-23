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
  syncing ``src/`` and the declared version. Never touches plugin copies.
* ``--materialize``: the FUTURE direction once canonical is restored and kept
  current -- copies top-level ``libs/<lib>`` DOWN into every
  ``plugins/<plugin>/libs/<lib>``. This is the shape the dev/main release
  pipeline's promotion-time generator will eventually call. Refuses to run for
  any lib whose *copies* carry a newer declared version than canonical (the
  "someone edited a copy directly and never touched canonical" hazard), unless
  ``--force`` is passed, so it can never silently regress a consumer. It is
  NOT blocked merely because content differs when canonical is the newer
  side -- that is the normal, expected pre-materialize state.

### DRY vendor pointers (the dev-branch form)

A plugin's vendored copy of a lib may, instead of carrying a real ``src/``,
carry a single ``VENDOR_POINTER.json`` (``{"source": "libs/<lib>", ...}``) --
this is the DRY *dev*-branch form the release-pipeline effort's promotion
step exists to expand (validated end-to-end in a standalone trial clone; see
the effort's Journal). A pointer copy is never a "real copy": it carries no
content of its own, so it is excluded from copies-vs-copies agreement checks
and from ``--restore-canonical``'s "which copy is the truth" selection --
treating an empty pointer directory as truth would silently **wipe
canonical** (a real bug caught during that trial: converting a lib's copies
to pointers and then running ``--restore-canonical`` blindly copied "no
content" up into canonical). ``--materialize`` still fully handles pointer
copies: it expands each one from canonical (writing real ``src/`` + version,
then deleting the now-superseded pointer file) exactly like it refreshes a
real copy. Only ``src/`` is ever touched by a pointer -- ``tests/`` (and
everything else in a copy) is deliberately out of scope, matching this
guard's own existing invariant (it only ever compares ``src/`` across
copies too). No real plugin in this repo has been converted to a pointer yet
-- that conversion is Phase 2 (the actual `dev` cutover), not this tool.

Usage::

    python tools/sync-vendored-libs.py                    # --check (default)
    python tools/sync-vendored-libs.py --restore-canonical # copies -> canonical
    python tools/sync-vendored-libs.py --materialize        # canonical -> copies
"""
from __future__ import annotations

import argparse
import hashlib
import re
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
POINTER_NAME = "VENDOR_POINTER.json"

_IGNORE_PARTS = {"build", ".venv", "__pycache__", "dist"}
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)


def _is_pointer_copy(path: Path) -> bool:
    """True when ``path`` is a DRY vendor-pointer stub, not a real copy."""
    return (path / POINTER_NAME).is_file() and not (path / "src").is_dir()


def _real_copies(paths: list[Path]) -> list[Path]:
    """``paths`` filtered down to real (non-pointer) copies."""
    return [p for p in paths if not _is_pointer_copy(p)]


def _lib_copies() -> dict[str, list[Path]]:
    """Map ``lib name -> [copy paths]``, mirroring check-vendored-libs-sync.py."""
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


def _copy_src(src_lib: Path, dst_lib: Path) -> None:
    src, dst = src_lib / "src", dst_lib / "src"
    if dst.exists():
        shutil.rmtree(dst)
    if src.is_dir():
        shutil.copytree(src, dst)


def _sync_version(src_lib: Path, dst_lib: Path) -> None:
    version = _declared_version(src_lib)
    if version is None:
        return
    pp = dst_lib / "pyproject.toml"
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
    if exit_code == 0:
        print("sync-vendored-libs --check: copies agree with each other "
              "(canonical drift, if any, is reported above as advisory).")
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
            ok, problems = _copies_agree(real)
            if not ok:
                print(f"{lib}: SKIPPED -- copies disagree, fix that first:")
                for p in problems:
                    print(f"  - {p}")
                continue
        truth = real[0]
        canonical = LIBS_DIR / lib
        if not canonical.is_dir():
            print(f"{lib}: no top-level libs/{lib}/ to restore into -- skipping")
            continue
        _copy_src(truth, canonical)
        _sync_version(truth, canonical)
        print(f"{lib}: canonical restored from {truth}")
    return 0


def cmd_materialize(*, force: bool) -> int:
    copies_map = _lib_copies()
    blocked: list[str] = []
    for lib, paths in sorted(copies_map.items()):
        canonical = LIBS_DIR / lib
        if not canonical.is_dir():
            continue
        real = _real_copies(paths)
        reason = None if (force or not real) else _materialize_blocked(lib, canonical, real[0])
        if reason:
            blocked.append(reason)
            continue
        for copy in paths:
            _copy_src(canonical, copy)
            _sync_version(canonical, copy)
            pointer = copy / POINTER_NAME
            if pointer.exists():
                pointer.unlink()
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


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="advisory report (default)")
    mode.add_argument("--restore-canonical", action="store_true",
                       help="copy the (verified-agreeing) vendored copies up into canonical")
    mode.add_argument("--materialize", action="store_true",
                       help="copy canonical down into every vendored copy (refuses drifted libs)")
    ap.add_argument("--force", action="store_true",
                     help="with --materialize, proceed even if canonical looks drifted")
    args = ap.parse_args(argv)

    if args.restore_canonical:
        return cmd_restore_canonical()
    if args.materialize:
        return cmd_materialize(force=args.force)
    return cmd_check()


if __name__ == "__main__":
    raise SystemExit(main())
