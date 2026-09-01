"""Preserve user activation while refreshing Copilot plugin inventory."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence

from plugin_activation import (
    ActivationSnapshot,
    PluginStateError,
    capture,
    installed_plugin_identities,
    restore,
    run_install_preserving_activation,
)

__all__ = [
    "ActivationSnapshot",
    "PluginStateError",
    "capture",
    "installed_plugin_identities",
    "restore",
    "run_install_preserving_activation",
]


def _parse_copilot_command(raw: str | None) -> list[str]:
    """Decode a validated command prefix supplied by a platform installer."""
    if raw is None:
        return ["copilot"]
    try:
        command = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PluginStateError(f"invalid Copilot command JSON: {exc}") from exc
    if (
        not isinstance(command, list)
        or not command
        or not all(isinstance(arg, str) and arg for arg in command)
    ):
        raise PluginStateError(
            "Copilot command JSON must be a non-empty array of non-empty strings"
        )
    return command


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("identity")
    command = parser.add_mutually_exclusive_group()
    command.add_argument("--copilot")
    command.add_argument("--copilot-command-json")
    args = parser.parse_args(argv)
    try:
        copilot_command = (
            _parse_copilot_command(args.copilot_command_json)
            if args.copilot_command_json is not None
            else [args.copilot or "copilot"]
        )
        result = run_install_preserving_activation(
            [
                *copilot_command,
                "plugin",
                "install",
                args.identity,
            ],
            args.identity,
        )
    except PluginStateError as exc:
        parser.error(str(exc))
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(_main())
