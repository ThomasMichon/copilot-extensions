"""Positive receiver capability fence for independently upgraded launchers."""

from __future__ import annotations

import argparse
from typing import Any

from .client_protocol_floor import protocol_floored_request

HOSTING_CAPABILITY = "repository_agent_hosting"
HOSTING_CAPABILITY_VERSION = 1


def add_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--require-agent-hosting", action="store_true",
        help="Refuse unless the receiving daemon enforces repository agent-hosting "
        "policy, including after endpoint re-resolution.",
    )


def configure_client(client: Any, args: argparse.Namespace) -> None:
    if getattr(args, "require_agent_hosting", False):
        if getattr(args, "cli", False):
            raise ValueError("--require-agent-hosting is not supported with --cli")
        client._request = protocol_floored_request(
            client, 0, capabilities={HOSTING_CAPABILITY: HOSTING_CAPABILITY_VERSION},
        )
