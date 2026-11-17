"""Lazy version action without importing package metadata during parser setup."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import sys
from typing import Any


class LazyVersionAction(argparse.Action):
    """Resolve the installed version only when the version flag is selected."""

    def __init__(
        self, option_strings: Sequence[str], dest: str = argparse.SUPPRESS,
        default: Any = argparse.SUPPRESS, help: str | None = None,
    ) -> None:
        super().__init__(
            option_strings=option_strings, dest=dest, default=default,
            nargs=0, help=help,
        )

    def __call__(
        self, parser: argparse.ArgumentParser, namespace: argparse.Namespace,
        values: str | Sequence[Any] | None, option_string: str | None = None,
    ) -> None:
        from . import __version__

        parser._print_message(f"agent-machines {__version__}\n", sys.stdout)
        parser.exit()
