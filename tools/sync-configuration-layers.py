#!/usr/bin/env python3
"""Keep standalone payload readers on the canonical configuration SDK."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def vendor_pairs():
    return [
        (
            REPO / "libs" / "configuration-layers" / "src" / package / "__init__.py",
            REPO / "plugins" / plugin / "scripts" / "configuration-layers"
            / "src" / package / "__init__.py",
        )
        for plugin in ("agent-conduct-guidance", "agent-index")
        for package in ("configuration_layers", "configuration_context")
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    changed = []
    for source, destination in vendor_pairs():
        if not destination.is_file() or destination.read_bytes() != source.read_bytes():
            changed.append((source, destination))
    if args.check:
        for _, destination in changed:
            print(f"Out of sync: {destination.relative_to(REPO)}")
        return int(bool(changed))
    for source, destination in changed:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    print(f"configuration-layers: synchronized {len(changed)} payload source(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
