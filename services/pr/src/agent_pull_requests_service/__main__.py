"""Explicit standalone service commands; no installation or automatic activation."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for operation in ("provision", "serve"):
        command = commands.add_parser(operation)
        command.add_argument("--config", type=Path, required=True)
    health = commands.add_parser("ready", help="Read bounded-age passive local readiness")
    health.add_argument("--status", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from .hosting import ready, run

        if args.command == "ready":
            return 0 if ready(args.status) else 1
        from .config import load_config

        config = load_config(args.config)
        if args.command == "provision":
            native = config.root / "watch-subscriptions.json"
            if native.exists() or native.is_symlink():
                raise ValueError("native and replay state roots must be separate")
            config.store().provision()
        else:
            asyncio.run(run(config))
        return 0
    except (Exception, KeyboardInterrupt) as error:
        # Operator-supplied references and provider errors can contain secrets.
        print(
            f"agent-pull-requests-service: {args.command} unavailable "
            f"({type(error).__name__}); state retained", file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
