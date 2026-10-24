"""The SSH service's internal foreground container/host entry point."""

import argparse
import asyncio
from pathlib import Path
import sqlite3
import sys

from . import __version__

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(argv)
    from fleet_manager.gateway_service_channel import TransportError
    from fleet_manager.planning import PlanError
    from single_instance_lease import AlreadyRunningError

    from .config import ServiceConfig, SSHServiceError
    from .hosting import run

    try:
        asyncio.run(run(ServiceConfig.load(args.config)))
        return 0
    except (OSError, PlanError, SSHServiceError, TransportError,
            AlreadyRunningError, sqlite3.Error) as exc:
        print("agent-ssh-service: " + type(exc).__name__, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
