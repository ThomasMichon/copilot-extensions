"""Explicit installed-package role planning; never bootstrap or mutate a host."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import time

from .artifact_staging import _bytes
from .komodo_roles import KomodoRoleProvider
from .planning import MAX_BYTES, PlanError, canonical, decode
from .role_deployment import plan_role_deployment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--fleet-id", required=True)
    parser.add_argument("--machine-limit", type=int, required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 0 if exc.code == 0 else 1
    try:
        result = plan_role_deployment(
            decode(_bytes(args.template.absolute(), MAX_BYTES)),
            decode(_bytes(args.config.absolute(), MAX_BYTES)),
            decode(_bytes(args.inventory.absolute(), MAX_BYTES)),
            providers={"komodo": KomodoRoleProvider()}, fleet_id=args.fleet_id,
            machine_limit=args.machine_limit, now=time.time(),
        )
        serialized = canonical(result, output=True).decode("utf-8")
    except PlanError as exc:
        print(f"fleet-manager role-plan: {exc}", file=sys.stderr)
        return 1
    print(serialized)
    return 2 if result["blockers"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
