"""Explain Fleet installation demand from a knowledge repository; make no changes."""
import argparse
import json
from pathlib import Path
import sys

from .registered_suite import fleet_service_roles


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--knowledge-root", required=True, type=Path)
    parser.add_argument("--target", required=True)
    args = parser.parse_args(argv)
    config = args.knowledge_root.absolute() / ".copilot-extensions" / "fleet-manager" / "config.json"
    try:
        roles = fleet_service_roles(config, args.target)
    except (OSError, ValueError) as error:
        print(f"Unresolved Fleet demand for {args.target}: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"target": args.target, "config": str(config), "service_roles": roles,
                      "app": "fleet-manager", "install_required": bool(roles)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
