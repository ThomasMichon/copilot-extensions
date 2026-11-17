"""Finite internal worker; admission remains a trusted in-process adapter API."""

import argparse
from pathlib import Path

from . import DriverError, drain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["worker"])
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    try:
        return drain(root=args.root)
    except DriverError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
