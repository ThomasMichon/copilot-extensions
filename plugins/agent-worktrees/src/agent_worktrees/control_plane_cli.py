"""Read an explicitly selected control-plane declaration without local binding."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from machine_transport import ControlPlaneError, detect_platform, load_control_plane


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees knowledge control-plane",
        description="Read harness/knowledge roles from an explicit topology; never bind or launch.",
    )
    parser.add_argument("--path", required=True, help="The explicitly selected machines.yaml")
    parser.add_argument("--json", action="store_true", help="Emit the versioned role declaration")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    source = Path(args.path).expanduser().resolve()
    try:
        declaration = load_control_plane(source)
        if declaration is None:
            raise ControlPlaneError(f"no control_plane declaration in {source}")
    except ControlPlaneError as exc:
        if args.json:
            print(json.dumps({"version": 1, "error": str(exc)}))
        else:
            print(f"Control-plane resolution failed: {exc}", file=sys.stderr)
        return 3
    result = {
        "version": 1,
        "source": str(source),
        "platform": detect_platform(),
        "control_plane": declaration.to_dict(),
    }
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Harness: {declaration.harness}")
        print(f"Knowledge: {declaration.knowledge or '(not declared)'}")
        print(f"Legacy launch projection: {declaration.legacy_project or '(not declared)'}")
    return 0
