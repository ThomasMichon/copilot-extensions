#!/usr/bin/env python3
"""Explicit live declaration audit using bounded public PyPI/npm HTTPS metadata.

Requires Python 3.11+, packaging (tools/artifact_validation_requirements.txt),
and node/npm for npm ranges. Does not install dependencies, resolve a private
feed, or prove the age of a subsequently resolved installation. This guard
always enforces >=604800 seconds; there is no age-floor disabling flag.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from dependency_age import audit


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--ecosystem", choices=("all", "python", "npm"), default="all",
                        help="explicit diagnostic lane; CI must audit all declarations")
    args = parser.parse_args()
    try:
        count, errors = audit(args.root, ecosystem=args.ecosystem)
    except (ValueError, OSError):
        print("Dependency declarations could not be inventoried; refusing an incomplete audit.")
        return 1
    for error in errors:
        print(error)
    print(f"Dependency age ({args.ecosystem}): {count} declarations, {len(errors)} violations (minimum 7 days).")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
