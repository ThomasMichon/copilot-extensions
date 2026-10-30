"""Offline review-generation CLI, also invokable from an uninstalled source tree."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time

from .review_workspace import MAX_BYTES, MAX_GENERATION_BYTES, build_generation


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("workspace JSON contains a duplicate field")
        value[key] = item
    return value


def _read(path: str, budget: int) -> object:
    stream = sys.stdin.buffer if path == "-" else Path(path).open("rb")
    try:
        raw = stream.read(budget + 1)
    finally:
        if path != "-":
            stream.close()
    if len(raw) > budget:
        raise ValueError("workspace input exceeds its byte budget")
    return json.loads(raw, object_pairs_hook=_unique)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline PR read generations; no credentials, network or review effects.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", allow_abbrev=False)
    load = commands.add_parser("load", allow_abbrev=False)
    load.add_argument("--evidence", required=True, help="version-1 evidence JSON path, or - for stdin")
    sync = commands.add_parser("sync", allow_abbrev=False)
    sync.add_argument("--evidence", required=True)
    sync.add_argument("--previous", required=True, help="previous generation JSON (not sync envelope)")
    args = parser.parse_args(argv)
    start = time.monotonic()
    try:
        if args.command == "capabilities":
            result = {
                "schema": "agent-pull-requests.review-workspace-capabilities", "version": 1,
                "evidence_versions": [1], "generation_versions": [1],
                "providers": ["azure-devops"], "mode": "offline",
                "capabilities": ["normalize", "compare"], "effects": "none",
                "acquisition": False, "source_attachment": False, "submission": False,
            }
        else:
            if args.command == "sync" and args.evidence == args.previous == "-":
                raise ValueError("evidence and previous cannot both consume stdin")
            previous = _read(args.previous, MAX_GENERATION_BYTES) if args.command == "sync" else None
            if args.command == "sync" and not isinstance(previous, dict):
                raise ValueError("sync requires a previous generation object")
            result = build_generation(_read(args.evidence, MAX_BYTES), previous)
            result["duration_ms"] = round((time.monotonic() - start) * 1000, 3)
        print(json.dumps(result, separators=(",", ":"), sort_keys=True, allow_nan=False))
        return 0
    except (json.JSONDecodeError, OSError, UnicodeError, RecursionError) as exc:
        # Input paths and provider data may be private; never echo their bytes.
        print(json.dumps({
            "error": "workspace input is unreadable or is not bounded finite JSON",
            "reason": type(exc).__name__,
            "effects": "none",
        }))
        return 1
    except ValueError as exc:
        print(json.dumps({"error": str(exc), "effects": "none"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
