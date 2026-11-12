"""Explicit selected-population reads; no project, registry or credential fallback."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from pr_filters import Expression, MAX_BYTES

from .providers.observation_population import contract, population, validate


def dispatch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr-watch population", allow_abbrev=False)
    parser.add_argument("repo", nargs="?")
    parser.add_argument("--contract", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--credential-command")
    parser.add_argument("--filter-file")
    parser.add_argument("--max-pages", type=int, default=4)
    parser.add_argument("--max-pulls", type=int, default=200)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--json", action="store_true")
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code or 0)
    try:
        if args.contract:
            if any((args.repo, args.config, args.credential_command, args.filter_file)) or not args.json:
                raise ValueError("passive contract cannot select execution inputs")
            print(json.dumps(contract()))
            return 0
        if not all((args.repo, args.config, args.credential_command, args.filter_file, args.json)):
            raise ValueError("population requires explicit bounded inputs and JSON")
        if not 1 <= args.seconds <= 60:
            raise ValueError("population requires 1..60 seconds")
        deadline = time.monotonic() + args.seconds
        path = Path(args.filter_file)
        if not path.is_absolute():
            raise ValueError("filter reference must be absolute")
        with path.open("rb") as stream:
            expression = Expression.loads(stream.read(MAX_BYTES + 1))
        from .pr_inventory_config import load_binding
        selected = load_binding(Path(args.config), args.repo, providers=("azure-devops",))
        validate(selected.provider, args.repo, selected.api_base, expression,
                 args.max_pages, args.max_pulls)
        from .pr_credential_command import command_source
        source = command_source(
            args.credential_command, remaining=lambda: deadline - time.monotonic(),
        )
        result = population(
            selected.provider, args.repo, api_base=selected.api_base, expression=expression,
            credential_source=source, max_pages=args.max_pages, max_pulls=args.max_pulls,
            deadline=deadline,
        )
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError):
        print("native population selection unavailable or unsupported", file=sys.stderr)
        return 2
