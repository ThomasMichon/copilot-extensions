#!/usr/bin/env python3
"""Keep standalone payload readers on the canonical configuration SDK."""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def vendor_pairs():
    pairs = [
        (
            REPO / "libs" / "configuration-layers" / "src" / package / filename,
            REPO / "plugins" / plugin / "scripts" / "configuration-layers"
            / "src" / package / filename,
        )
        for plugin in ("agent-conduct-guidance", "agent-index", "agent-vault")
        for package in ("configuration_layers", "configuration_context")
        for filename in (
            ("__init__.py", "runtime.py", "readiness.py")
            if package == "configuration_context" else ("__init__.py",)
        )
    ]
    probe = REPO / "libs" / "configuration-layers" / "src" / "configuration_context" / "readiness.py"
    pairs.extend(
        (probe, plugin / "scripts" / "versioned_runtime_probe.py")
        for plugin in sorted((REPO / "plugins").iterdir())
        if (plugin / "pyproject.toml").is_file() and (plugin / "installer-readiness.json").is_file()
    )
    return pairs


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
