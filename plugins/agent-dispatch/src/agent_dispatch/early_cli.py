"""Bounded local namespaces that do not need the full coordinator CLI."""

from __future__ import annotations

import argparse
import os

from .delegated_waiter_contract import CALLBACK_COMMAND


def dispatch(argv: list[str]) -> int | None:
    if argv[:1] == [CALLBACK_COMMAND]:
        from .delegated_waiter_callback import main

        return main(argv[1:])
    if (
        argv[:2] not in (["workers", "catalog"], ["workers", "save"])
        or any(name.startswith("AGENT_DISPATCH_TASK_") for name in os.environ)
    ):
        return None
    from .workers_catalog import register_commands

    parser = argparse.ArgumentParser(prog="agent-dispatch")
    sub = parser.add_subparsers(dest="command", required=True)
    workers = sub.add_parser("workers")
    register_commands(workers.add_subparsers(dest="workers_command", required=True))
    args = parser.parse_args(argv)
    return args.func(args)
