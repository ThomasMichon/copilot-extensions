"""Service arguments and explicit native configuration-source binding."""

from __future__ import annotations

import argparse

from . import config
from .configuration import runtime_enabled


def parse_service_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Agent Vault Service")
    parser.add_argument("--foreground", action="store_true", help="Run in foreground")
    parser.add_argument("--persistent", action="store_true", help="Disable inactivity timeout")
    parser.add_argument(
        "--configuration-root",
        help="Native configuration source captured by the launcher; independent of service state cwd",
    )
    parser.add_argument("--tcp-port", type=int, default=None,
                        help=f"TCP port (default: {config.DEFAULT_TCP_PORT})")
    parser.add_argument("--ping", action="store_true", help="Check if service is running")
    parser.add_argument("--stop", action="store_true", help="Stop running service")
    parser.add_argument("--ensure-selected", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--export-handoff", action="store_true",
        help=(
            "Drain-safe cutover (#743): if a daemon is running and reachable "
            "over an owner-gated local transport, print its unlocked-state "
            "handoff payload as one line of JSON to stdout (empty output, exit "
            "0, when there is nothing to hand off -- never raises). Installer-"
            "internal; piped straight into the successor's --handoff-stdin, "
            "never written to disk."
        ),
    )
    parser.add_argument(
        "--handoff-stdin", action="store_true",
        help=(
            "Drain-safe cutover (#743): accept a JSON handoff payload -- via "
            "the AGENT_VAULT_HANDOFF_JSON env var (the systemd-managed "
            "restart path) or, failing that, stdin (the direct-invocation "
            "path) -- and warm this generation with it before serving -- no "
            "re-unlock. Consumed before detaching and applied by the admitted "
            "resident; never touches disk, never logged."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")
    args = parser.parse_args()
    if args.configuration_root is not None:
        config.bind_service_configuration(args.configuration_root)
        if not (args.ping or args.stop or args.export_handoff) and not runtime_enabled():
            parser.error('Agent Vault is inactive; runtime activation requires enabled=true')
    return args
