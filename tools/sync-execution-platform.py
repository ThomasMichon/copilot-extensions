#!/usr/bin/env python3
"""Publish the canonical stdlib platform classifier for pre-runtime admission."""

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "libs/machine-transport/src/machine_transport/execution_platform.py"
TARGET = ROOT / "plugins/agent-worktrees/scripts/execution_platform.py"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = SOURCE.read_bytes()
    if args.check:
        if not TARGET.is_file() or TARGET.read_bytes() != content:
            print("Standalone execution platform classifier is out of sync")
            return 1
    else:
        TARGET.write_bytes(content)
    print("Standalone execution platform classifier is synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
