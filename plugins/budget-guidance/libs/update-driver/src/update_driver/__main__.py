"""Finite internal worker; admission remains a trusted in-process adapter API."""

import argparse
import json
from pathlib import Path

from . import (
    DriverError, FAILED_UNSTARTED_DISPOSITION_CAPABILITY, drain, resolve_failed_unstarted,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, epilog=FAILED_UNSTARTED_DISPOSITION_CAPABILITY,
    )
    parser.add_argument("action", choices=["worker", "resolve-failed-unstarted"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--request-id")
    parser.add_argument("--reason")
    parser.add_argument("--authorized-repair", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "resolve-failed-unstarted":
            if not args.request_id or not args.reason:
                parser.error("disposition requires --request-id and --reason")
            request = resolve_failed_unstarted(
                args.request_id, reason=args.reason, authorized=args.authorized_repair, root=args.root,
            )
            print(json.dumps({
                "request_id": request.id, "state": request.state, "exit_code": request.exit_code,
                "error": request.error, "started_at": request.started_at,
                "finished_at": request.finished_at,
            }))
            return 0
        if args.request_id or args.reason or args.authorized_repair:
            parser.error("disposition options require resolve-failed-unstarted")
        return drain(root=args.root)
    except DriverError as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
