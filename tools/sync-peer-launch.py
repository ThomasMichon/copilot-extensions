#!/usr/bin/env python3
"""Synchronize the packaged same-cell peer boundary and its canonical source."""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SOURCE = REPO / "libs" / "peer-launch" / "peer_launch.py"
DESTINATIONS = (
    REPO / "plugins" / "agent-dispatch" / "src" / "agent_dispatch" / "peer_launch.py",
    REPO / "plugins" / "agent-codespaces" / "src" / "agent_codespaces" / "_peer_launch.py",
    REPO / "plugins" / "agent-containers" / "src" / "agent_containers" / "_peer_launch.py",
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    different = [p for p in DESTINATIONS if not p.is_file() or p.read_bytes() != SOURCE.read_bytes()]
    if args.check:
        for path in different:
            print(f"Out of sync: {path.relative_to(REPO)}")
        if not different:
            print("peer-launch: canonical and all packaged vendors in sync")
        return int(bool(different))
    for path in different:
        shutil.copyfile(SOURCE, path)
    print(f"peer-launch: synchronized {len(different)} vendor(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
