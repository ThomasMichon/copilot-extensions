"""The SSH service's internal foreground container/host entry point."""

import argparse
import asyncio
from pathlib import Path
import sqlite3
import subprocess
import sys

from . import __version__


async def _foreground(config) -> int:
    from .hosting import UnsettledRelay, run

    try:
        await run(config)
        return 0
    except UnsettledRelay as unsettled:
        print("agent-ssh-service: native-relay-cleanup-unconfirmed; "
              "foreground custody and writer lease retained", file=sys.stderr)
        await unsettled.settle()
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    from fleet_manager.gateway_service_channel import TransportError
    from fleet_manager.planning import PlanError
    from single_instance_lease import AlreadyRunningError

    from .config import ServiceConfig, SSHServiceError
    try:
        return asyncio.run(_foreground(ServiceConfig.load(args.config)))
    except (OSError, PlanError, SSHServiceError, TransportError,
            AlreadyRunningError, sqlite3.Error, subprocess.SubprocessError, RuntimeError) as exc:
        print("agent-ssh-service: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
