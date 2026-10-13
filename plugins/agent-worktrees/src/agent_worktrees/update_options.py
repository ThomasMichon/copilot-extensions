"""Extra ``update`` CLI options, kept out of ``update_cli`` for its size cap."""

from __future__ import annotations

import argparse


def add_update_options(p: argparse.ArgumentParser) -> None:
    p.add_argument(
        "--force-runtime",
        action="append",
        default=None,
        metavar="NAME",
        help="Re-deploy the named runtime even when the deployed version already "
        "matches the payload (repeatable; --force still forces every runtime)",
    )


def forced_runtimes(args: argparse.Namespace) -> frozenset[str]:
    return frozenset(getattr(args, "force_runtime", None) or ())


def forwarded_flags(args: argparse.Namespace) -> list[str]:
    """The options above, rebuilt for forwarding through the Manager seam."""
    flags: list[str] = []
    for name in getattr(args, "force_runtime", None) or ():
        flags += ["--force-runtime", name]
    return flags
