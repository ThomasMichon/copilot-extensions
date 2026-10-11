"""Validation of arguments passed to an interactive Copilot launch."""

from __future__ import annotations

import argparse

REJECTED_COPILOT_ARGS = ("--acp", "--stdio", "-p", "--prompt", "-i", "--interactive")


def passthrough_error(args: argparse.Namespace) -> str | None:
    for arg in getattr(args, "copilot_args", None) or []:
        if str(arg).split("=", 1)[0] in REJECTED_COPILOT_ARGS:
            return (
                f"--copilot-arg {arg!r} is not allowed: it changes the launched "
                "session's mode (embody always launches the interactive TUI; "
                "use --seed for the first turn)"
            )
    return None
