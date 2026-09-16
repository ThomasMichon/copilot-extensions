#!/usr/bin/env python3
"""Rank the module-size baseline's offenders for componentization triage.

``tools/check-module-size.py`` enforces the 1,000-line cap / shrink-only
baseline; it answers "did anything grow?". This script answers the different
question a breakdown effort needs: "what's the worst of what's already
grandfathered in, and in what order should it get split?"

Several baselined files are **identical vendored copies** of one canonical
primitive (see ``tools/sync-installation-context.py``,
``tools/sync-versioned-runtime.py``, ``tools/check-vendored-libs-sync.py``) --
splitting the canonical source fixes every copy at its next sync, so counting
each copy as an independent offender would misrepresent the backlog. This
script groups baselined files by content hash and reports one row per distinct
file, folding duplicate vendored copies into a `` (+N vendored copies)`` note
on the shortest/canonical-looking path in the group.

Usage::

    python tools/rank-module-size.py              # top offenders by size
    python tools/rank-module-size.py --near-cap 25 # files within N lines of
                                                    # their own baselined ceiling
    python tools/rank-module-size.py --limit 40    # show more/fewer rows
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_CHECK_MODULE_SIZE = REPO / "tools" / "check-module-size.py"


def _load_check_module_size():
    spec = importlib.util.spec_from_file_location(
        "check_module_size", _CHECK_MODULE_SIZE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _content_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _group_vendored_copies(paths: list[str]) -> dict[str, list[str]]:
    """Map each distinct-content file to the other paths sharing its content."""
    by_hash: dict[str, list[str]] = {}
    for rel in paths:
        digest = _content_hash(REPO / rel)
        if digest is None:
            continue
        by_hash.setdefault(digest, []).append(rel)
    canonical_groups: dict[str, list[str]] = {}
    for members in by_hash.values():
        members_sorted = sorted(members, key=lambda p: (len(p), p))
        canonical = members_sorted[0]
        canonical_groups[canonical] = members_sorted[1:]
    return canonical_groups


def build_rows(cms) -> list[tuple[int, int, int, str, int]]:
    """Return ``(lines, cap, margin, path, vendored_copy_count)`` rows."""
    baseline = cms._load_baseline()
    groups = _group_vendored_copies(list(baseline))
    rows = []
    for path, copies in groups.items():
        cap = baseline[path]
        lines = cms._line_count(path)
        rows.append((lines, cap, cap - lines, path, len(copies)))
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--limit", type=int, default=20, help="how many rows to print (default 20)"
    )
    parser.add_argument(
        "--near-cap",
        type=int,
        metavar="N",
        help="instead of ranking by size, list files within N lines of their "
        "own baselined ceiling (the ones most likely to fail next)",
    )
    args = parser.parse_args()

    cms = _load_check_module_size()
    rows = build_rows(cms)

    if args.near_cap is not None:
        near = sorted(
            (r for r in rows if 0 <= r[2] <= args.near_cap), key=lambda r: r[2]
        )
        print(f"Files within {args.near_cap} lines of their baselined ceiling:")
        for lines, cap, margin, path, copies in near[: args.limit]:
            copy_note = f"  (+{copies} vendored copies)" if copies else ""
            print(f"  margin={margin:>4}  {lines}/{cap}  {path}{copy_note}")
        if not near:
            print("  (none)")
        return 0

    ranked = sorted(rows, key=lambda r: r[0], reverse=True)
    print(
        f"Top {min(args.limit, len(ranked))} of {len(ranked)} distinct baselined "
        "files, by line count (cap=1000; vendored duplicates folded in):"
    )
    for lines, cap, margin, path, copies in ranked[: args.limit]:
        copy_note = f"  (+{copies} vendored copies)" if copies else ""
        over_by = lines - 1000
        print(f"  {lines:>7} lines  {over_by:>7}-over-cap  {path}{copy_note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
