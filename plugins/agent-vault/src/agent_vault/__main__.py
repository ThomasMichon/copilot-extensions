"""CLI entry point for agent-vault."""

from __future__ import annotations

import sys

from .cli import main as cli_main


def main() -> int:
    """Run the agent-vault command-line interface."""
    return cli_main() or 0


if __name__ == "__main__":
    sys.exit(main())
