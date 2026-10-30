"""Offline owner-read request inspector; never establishes a backend binding."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .owner_read import MAX_REQUEST_BYTES, parse_request


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate owner-read request field")
        result[key] = value
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Inspect the owner-read contract offline; no owner adoption or backend access.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("contract", allow_abbrev=False)
    validate = commands.add_parser("validate-request", allow_abbrev=False)
    validate.add_argument("--request", required=True, help="JSON file, or - for stdin")
    args = parser.parse_args(argv)
    try:
        if args.command == "contract":
            result = {
                "schema": "agent-pull-requests.owner-read-contract", "version": 1,
                "request_versions": [1], "response_versions": [1],
                "operational_owner": False, "backend_access": False, "effects": "none",
            }
        else:
            if args.request == "-":
                raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
            else:
                with Path(args.request).open("rb") as stream:
                    raw = stream.read(MAX_REQUEST_BYTES + 1)
            if len(raw) > MAX_REQUEST_BYTES:
                raise ValueError("owner-read request exceeds its byte budget")
            result = {
                "request": parse_request(json.loads(raw, object_pairs_hook=_unique)),
                "owner_adoption": "not_established", "backend_access": False, "effects": "none",
            }
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except (ValueError, OSError, UnicodeError, RecursionError):
        print(json.dumps({"error": "invalid or unavailable owner-read request", "effects": "none"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
