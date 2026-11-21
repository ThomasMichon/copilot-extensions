"""Explicit legacy collection through the executing runtime's retained owner."""
from __future__ import annotations

import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any

CAPABILITY = "agent-procutil.python-cache-reclamation/v1"


def add_parser(actions: Any) -> None:
    parser = actions.add_parser("collect", help="Inspect or reclaim qualified legacy cache prefixes")
    parser.add_argument("--root", action="append", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--drain", action="store_true")
    parser.add_argument("--max-seconds", type=float)
    parser.add_argument("--max-bytes", type=int)
    parser.add_argument("--max-entries", type=int, default=262144)
    parser.add_argument("--max-namespaces", type=int, default=512)
    parser.add_argument("--min-age-hours", type=float, default=168)
    parser.add_argument("--details", action="store_true")
    parser.add_argument("--contract", action="store_true")
    parser.add_argument("--json", action="store_true")


def retained_owner() -> Any:
    prefix = Path(sys.prefix)
    if prefix.name == ".venv":
        prefix = prefix.parent
    script = prefix / ".runtime-bootstrap" / "versioned_runtime.py"
    if script.parent.is_symlink() or script.is_symlink() or not script.is_file():
        raise RuntimeError("The executing runtime has no retained cache-maintenance owner")
    spec = importlib.util.spec_from_file_location("_cache_collection_runtime", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("The executing runtime's cache-maintenance owner cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run(args: Any) -> int:
    try:
        runtime = retained_owner()
        supported = (
            getattr(runtime, "PYTHON_CACHE_RECLAMATION_CAPABILITY", None) == CAPABILITY
            and callable(getattr(runtime, "collect_python_cache", None))
        )
        if args.contract:
            print(json.dumps({"contract": CAPABILITY, "supported": supported,
                              "dry_run_default": True, "max_seconds": 3600}))
            return 0 if supported else 2
        if not supported:
            raise RuntimeError("The executing runtime does not support qualified cache collection; update it first")
        if not args.root or len(args.root) != 1 or not args.root[0].is_absolute():
            raise ValueError("collect requires exactly one absolute --root")
        if not math.isfinite(args.min_age_hours) or args.min_age_hours < 0:
            raise ValueError("--min-age-hours must be finite and non-negative")
        result = runtime.collect_python_cache(
            args.root[0], apply=args.apply, drain=args.drain, seconds=args.max_seconds,
            byte_limit=args.max_bytes, entries=args.max_entries, namespaces=args.max_namespaces,
            min_age_seconds=args.min_age_hours * 3600, details=args.details,
        )
        if args.json:
            print(json.dumps(result, sort_keys=True))
        else:
            summary = result["summary"]
            unmeasured = summary["unmeasured_prefixes"]
            print(
                f"Legacy bytecode: {summary['measured_bytes']} measured bytes, "
                f"{summary['eligible_bytes']} snapshot-eligible, "
                f"{summary['held_bytes']} held, {summary['removed_bytes']} removed; "
                f"{unmeasured if unmeasured is not None else 'unknown'} unmeasured prefixes."
            )
            print(f"Liveness: {result['liveness']}; pending: {result['pending']}.")
            for reason, count in sorted(result["reasons"].items()):
                print(f"  {reason}: {count}")
            print("Dry-run observations never authorize apply; every effect revalidates current proof.")
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"python-cache collect: {error}", file=sys.stderr)
        return 2
