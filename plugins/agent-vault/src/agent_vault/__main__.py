"""CLI entry point for agent-vault."""

from __future__ import annotations

import sys


def cli_main(argv: list[str] | None = None) -> int | None:
    from .cli import main as command_main

    return command_main(argv)


def main() -> int:
    """Run the agent-vault command-line interface."""
    from .invocation import invoke_cli, parse_invocation

    arguments = sys.argv[1:]
    if parse_invocation(arguments)[1] == ["invocation-contract", "--json"]:
        return invoke_cli(cli_main, arguments) or 0
    return cli_main() or 0


if __name__ == "__main__":
    sys.exit(main())
