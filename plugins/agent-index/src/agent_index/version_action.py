"""Lazy package-version rendering for the command parser."""

from __future__ import annotations

import argparse
import sys
from typing import Any


class LazyVersionAction(argparse.Action):
    """Resolve the package version only when ``--version`` is requested."""

    def __init__(
        self, option_strings: list[str], dest: str = argparse.SUPPRESS,
        default: Any = argparse.SUPPRESS, help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings=option_strings, dest=dest, default=default,
            nargs=0, help=help,
        )

    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: Any, option_string: str | None = None,
    ) -> None:
        from . import __version__

        parser._print_message(f"{__version__}\n", sys.stdout)
        parser.exit()


def add_version_argument(parser: argparse.ArgumentParser) -> None:
    """Register package-version output without resolving it during parser setup."""
    parser.add_argument(
        "--version", action=LazyVersionAction,
        help="show program's version number and exit",
    )
