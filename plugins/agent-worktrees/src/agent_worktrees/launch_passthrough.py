"""Validation of arguments passed to an interactive Copilot launch."""

from __future__ import annotations

import argparse

REJECTED_COPILOT_ARGS = ("--acp", "--stdio", "-p", "--prompt", "-i", "--interactive")


def add_launch_passthrough_args(p: argparse.ArgumentParser) -> None:
    """Register the launch-passthrough flags shared by `embody` and `copilot`."""
    p.add_argument(
        "--copilot-process-launcher", default=None, metavar="PATH",
        help="One-launch executable wrapper for direct Copilot or normalized "
        "setup. Requires an absolute executable path; custom templates and "
        "configured copilot_path overrides fail closed.",
    )
    p.add_argument(
        "--copilot-arg", dest="copilot_args", action="append", default=[],
        metavar="ARG",
        help="Extra argument appended to the launched Copilot command line "
        "(repeatable; use the `--copilot-arg=--flag=value` form for values that "
        "start with '-'). For example `--copilot-arg=--plugin-dir=/path` stages "
        "a plugin into the session, `--copilot-arg=--no-ask-user` for an "
        "unattended one. Mode-changing flags (--acp, --stdio, -p/--prompt, "
        "-i/--interactive) are refused -- use --seed for the first turn.",
    )
    p.add_argument(
        "--bridge-scope-id", dest="bridge_scope_id", default=None, metavar="ID",
        help="Identity the launched session registers with agent-bridge "
        "(exported as AGENT_BRIDGE_SCOPE_ID; default: `agent-worktrees get "
        "session-scope-id`). A venue launcher sets a venue-qualified id "
        "(e.g. `anchor-<repo>@<codespace>`) so sessions on different venues of "
        "the same repo stay distinguishable on the host bridge. Does not change "
        "the local mux session name.",
    )


def passthrough_error(args: argparse.Namespace) -> str | None:
    for arg in getattr(args, "copilot_args", None) or []:
        if str(arg).split("=", 1)[0] in REJECTED_COPILOT_ARGS:
            return (
                f"--copilot-arg {arg!r} is not allowed: it changes the launched "
                "session's mode (embody always launches the interactive TUI; "
                "use --seed for the first turn)"
            )
    return None
