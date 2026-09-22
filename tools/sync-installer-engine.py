#!/usr/bin/env python3
"""Vendor the canonical installer-engine files into opted-in adopter plugins.

The installer engine is a **vendored** source surface, not a runtime
cross-plugin dependency: every adopting plugin ships its own byte-identical
copy under ``scripts/installer-engine.*`` because marketplace plugins are
installed independently. The canonical sources live under
``libs/installer-engine/`` and this tool keeps adopters in sync.

Usage::

    python tools/sync-installer-engine.py          # copy canonical -> plugins
    python tools/sync-installer-engine.py --check  # verify in sync
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL_DIR = REPO / "libs" / "installer-engine"
FILES = ("installer-engine.ps1", "installer-engine.sh")
# Add future adopters here as later rollout phases land. The list stays explicit
# so the canonical engine can migrate one plugin at a time.
ADOPTERS = ("agent-pull-requests",)


def vendor_pairs() -> list[tuple[Path, Path]]:
    return [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "scripts" / name,
        )
        for plugin in ADOPTERS
        for name in FILES
    ]


def verify() -> list[str]:
    problems: list[str] = []
    for source, destination in vendor_pairs():
        relative = destination.relative_to(REPO).as_posix()
        if not source.is_file():
            problems.append(f"canonical source missing: {source.relative_to(REPO)}")
        elif not destination.is_file():
            problems.append(f"{relative} is missing")
        elif destination.read_bytes() != source.read_bytes():
            problems.append(f"{relative} differs from {source.relative_to(REPO)}")
        elif os.name != "nt" and stat.S_IMODE(destination.stat().st_mode) != stat.S_IMODE(
            source.stat().st_mode
        ):
            problems.append(f"{relative} mode differs from {source.relative_to(REPO)}")
    return problems


def sync() -> list[str]:
    written: list[str] = []
    for source, destination in vendor_pairs():
        if not source.is_file():
            raise FileNotFoundError(f"canonical source missing: {source}")
        content_matches = destination.is_file() and destination.read_bytes() == source.read_bytes()
        mode_matches = destination.is_file() and (
            os.name == "nt"
            or stat.S_IMODE(destination.stat().st_mode) == stat.S_IMODE(source.stat().st_mode)
        )
        if content_matches and mode_matches:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not content_matches:
            shutil.copyfile(source, destination)
        if os.name != "nt":
            shutil.copymode(source, destination)
        written.append(destination.relative_to(REPO).as_posix())
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify vendored copies without changing files",
    )
    arguments = parser.parse_args()
    if arguments.check:
        problems = verify()
        if problems:
            print("installer-engine vendoring is out of sync:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("\nRun: python tools/sync-installer-engine.py", file=sys.stderr)
            return 1
        print(f"installer-engine files in sync across {len(ADOPTERS)} adopter(s).")
        return 0

    written = sync()
    if written:
        print(f"Synced installer-engine files ({len(written)} file(s)):")
        for path in written:
            print(f"  + {path}")
    else:
        print("Installer-engine vendoring already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
