#!/usr/bin/env python3
"""Vendor the canonical installation-context bootstrap into its Phase 3 exemplars.

The files remain non-operative until each plugin explicitly changes its
installer and payload-invocation contract. This tool only guarantees that the
standalone payload has a byte-identical copy available when that later cutover
occurs.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CANONICAL_DIR = REPO / "libs" / "installation-context"
FILES = (
    "installation_context.py",
    "installation-context.sh",
    "installation-context.ps1",
    "json-query.awk",
)
ADOPTERS = ("agent-machines", "agent-index")


def vendor_pairs() -> list[tuple[Path, Path]]:
    return [
        (
            CANONICAL_DIR / name,
            REPO / "plugins" / plugin / "scripts" / "installation-context" / name,
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
        content_matches = (
            destination.is_file() and destination.read_bytes() == source.read_bytes()
        )
        mode_matches = (
            destination.is_file()
            and (
                os.name == "nt"
                or stat.S_IMODE(destination.stat().st_mode)
                == stat.S_IMODE(source.stat().st_mode)
            )
        )
        if content_matches and mode_matches:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not content_matches:
            shutil.copyfile(source, destination)
        os.chmod(destination, source.stat().st_mode & 0o777)
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
            print("installation-context vendoring is out of sync:", file=sys.stderr)
            for problem in problems:
                print(f"  - {problem}", file=sys.stderr)
            print("\nRun: python tools/sync-installation-context.py", file=sys.stderr)
            return 1
        print(
            "installation-context files in sync across "
            f"{len(ADOPTERS)} non-operative adopters."
        )
        return 0

    written = sync()
    if written:
        print(f"Synced installation-context files ({len(written)} file(s)):")
        for path in written:
            print(f"  + {path}")
    else:
        print("Installation-context vendoring already in sync.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
