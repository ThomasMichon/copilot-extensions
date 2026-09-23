#!/usr/bin/env python3
"""Build the "main release form" from a `dev`-shaped checkout: snapshot the
whole tree, then expand every DRY vendor pointer
(`plugins/<plugin>/libs/<lib>/VENDOR_POINTER.json`) back into a full copy
from its canonical `libs/<lib>` source. This is the whole-repo counterpart of
`preview_release.py`'s single-plugin materialization, and a validated
prototype of the promotion pipeline's core step (Phase 3 of the
dev-branch-release-pipeline effort) -- confirmed byte-for-byte lossless
across all 56 vendored copies of this repo's 10 shared libs in a standalone
trial clone (see the effort's Journal).

No real plugin in this repo carries a vendor pointer yet -- that conversion
is Phase 2 (the actual `dev` cutover), not this tool. Until then this tool
is a no-op against the real checkout (there is nothing to expand) and exists
so the promotion mechanism is ready and tested before it's needed.

Only `src/` is ever touched by a pointer, matching
`check-vendored-libs-sync.py`'s own existing invariant (it only ever
compares `src/` across copies; `tests/` and everything else stay untouched).

Usage::

    python tools/materialize_main.py --dest /path/to/main-snapshot
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POINTER_NAME = "VENDOR_POINTER.json"
_VERSION_RE = re.compile(r'^(\s*version\s*=\s*")([^"]+)(")', re.MULTILINE)


def find_pointers(root: Path) -> list[Path]:
    return sorted(root.glob("plugins/*/libs/*/" + POINTER_NAME))


def materialize(dest: Path, *, canonical_root: Path) -> list[str]:
    """Expand every pointer found under ``dest`` from ``canonical_root``.

    ``canonical_root`` is a parameter (not hardcoded to ``REPO``) so a test
    can point it at an isolated tree instead of the real checkout."""
    log: list[str] = []
    for pointer_path in find_pointers(dest):
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
        source_rel = pointer["source"]  # e.g. "libs/zdd"
        canonical = canonical_root / source_rel
        lib_copy_dir = pointer_path.parent

        if not canonical.is_dir():
            log.append(f"SKIP {lib_copy_dir}: canonical {source_rel} not found")
            continue

        src_sub = canonical / "src"
        dst_sub = lib_copy_dir / "src"
        if dst_sub.exists():
            shutil.rmtree(dst_sub)
        if src_sub.is_dir():
            shutil.copytree(src_sub, dst_sub)

        canon_pp = canonical / "pyproject.toml"
        copy_pp = lib_copy_dir / "pyproject.toml"
        if canon_pp.exists() and copy_pp.exists():
            m = _VERSION_RE.search(canon_pp.read_text(encoding="utf-8"))
            if m:
                text = copy_pp.read_text(encoding="utf-8")
                copy_pp.write_text(_VERSION_RE.sub(rf"\g<1>{m.group(2)}\g<3>", text, count=1),
                                   encoding="utf-8")

        pointer_path.unlink()
        log.append(f"OK   {lib_copy_dir} <- {source_rel}")
    return log


def _ignore(_dir: str, names: list[str]) -> set[str]:
    return {n for n in names if n in {
        ".git", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist",
    } or n.endswith((".pyc", ".pyo"))}


def build(dest: Path, *, source_root: Path = REPO) -> list[str]:
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source_root, dest, ignore=_ignore)
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
