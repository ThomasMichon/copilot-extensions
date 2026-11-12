"""Strict, explicit read-only native repository inventory boundary."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .providers.observation_inventory import inventory


def dispatch(argv: list[str]) -> int:
    """Resolve only a trusted explicit config; inventory never enrolls a target."""
    parser = argparse.ArgumentParser(prog="pr-watch inventory", allow_abbrev=False)
    parser.add_argument("repo", nargs="?")
    parser.add_argument("--contract", action="store_true")
    parser.add_argument("--config")
    parser.add_argument("--credential-command")
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
            if args.repo or args.config or args.credential_command:
                raise ValueError("contract discovery cannot select repository or credentials")
            from .providers.observation_credentials import MAX_SNAPSHOT_REQUESTS
            print(json.dumps({
                "protocol": "agent-worktrees.pr-inventory/v1",
                "providers": ["github", "gitea"], "credential": "observe/per-request",
                "effects": "none", "max_pages": 20, "max_pulls": 1000,
                "snapshot_request_budget": MAX_SNAPSHOT_REQUESTS,
                "snapshot_capture_bytes": 1_048_584,
                "snapshot_credential_budget": {
                    "version": 1, "option": "--max-credential-requests",
                    "maximum": MAX_SNAPSHOT_REQUESTS,
                },
            }))
            return 0
        if not args.repo or not args.config or not args.credential_command or not args.json:
            raise ValueError("inventory requires explicit repository, config, credential command and JSON")
        if type(args.seconds) is not int or not 1 <= args.seconds <= 60:
            raise ValueError("inventory requires 1..60 seconds")
        from .pr_credential_command import command_source
        from .pr_inventory_config import load_binding
        from .providers import get_provider
        deadline = time.monotonic() + args.seconds
        source = command_source(
            args.credential_command, remaining=lambda: deadline - time.monotonic(),
        )
        selected = load_binding(Path(args.config), args.repo)
        result = inventory(
            get_provider(selected.provider), args.repo, api_base=selected.api_base,
            credential_source=source, max_pages=args.max_pages, max_pulls=args.max_pulls,
            deadline=deadline,
        )
        print(json.dumps(result, allow_nan=False))
        return 0
    except (OSError, ValueError, RuntimeError):
        from . import output
        output.err("native repository inventory selection unavailable or unsupported")
        return 2
