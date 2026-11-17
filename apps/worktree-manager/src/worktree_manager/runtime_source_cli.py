"""Explicit policy and acquisition diagnostics, not another installation path."""
from __future__ import annotations

import argparse
import json
import sys

from . import runtime_source_config as config
from .runtime_source import acquire


def main(arguments: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="worktree-manager runtime-source", allow_abbrev=False)
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("show")
    setting = sub.add_parser("set")
    setting.add_argument("channel", choices=("main", "stable", "exact", "dev"))
    setting.add_argument("--repo", default=config.source_config.DEFAULT_REPO)
    setting.add_argument("--ref", default="")
    setting.add_argument("--allow-dev", action="store_true")
    sub.add_parser("reset")
    sub.add_parser("resolve", help="fetch and resolve one source; does not install or activate")
    options = parser.parse_args(arguments)
    try:
        if options.action == "set":
            config.set_source(config.RuntimeSource(
                options.channel, options.repo, options.ref, options.allow_dev,
            ))
        elif options.action == "reset":
            config.set_source(config.RuntimeSource())
        selection = config.configured_source()
        if options.action == "resolve":
            with acquire(selection) as resolved:
                document = resolved.document()
        else:
            document = selection.document()
        print(json.dumps(document, sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Runtime source: {error}", file=sys.stderr)
        return 1
