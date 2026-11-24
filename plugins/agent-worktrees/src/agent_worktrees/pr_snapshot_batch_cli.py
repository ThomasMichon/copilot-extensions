"""Strict, explicit and stateless optional snapshot-batch command."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time


def dispatch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="pr-watch snapshot-batch", allow_abbrev=False)
    parser.add_argument("repo")
    parser.add_argument("numbers", nargs="+", type=int)
    parser.add_argument("--config", required=True)
    parser.add_argument("--credential-command", required=True)
    parser.add_argument("--max-credential-requests", type=int, default=64)
    parser.add_argument("--seconds", type=int, default=60)
    parser.add_argument("--json", action="store_true", required=True)
    try:
        args = parser.parse_args(argv)
    except SystemExit as error:
        return int(error.code or 0)
    try:
        from .pr_credential_command import command_source
        from .pr_inventory_config import load_binding
        from .providers.github_batch import snapshots
        from .providers.github_batch_query import API_BASE, validate_selection

        validate_selection(args.repo, args.numbers)
        if not 1 <= args.seconds <= 60 or not 1 <= args.max_credential_requests <= 64:
            raise ValueError("invalid batch budget")
        deadline = time.monotonic() + args.seconds
        selected = load_binding(Path(args.config), args.repo, providers=("github",))
        if selected.api_base != API_BASE:
            raise ValueError("snapshot batch supports only the selected GitHub.com API")
        source = command_source(
            args.credential_command, remaining=lambda: deadline - time.monotonic(),
            max_acquisitions=args.max_credential_requests,
        )
        result = snapshots(
            args.repo, args.numbers, api_base=selected.api_base, credential_source=source,
            deadline=deadline, max_requests=args.max_credential_requests,
        )
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError):
        from . import output
        output.err("native snapshot batch selection unavailable or unsupported")
        return 2
