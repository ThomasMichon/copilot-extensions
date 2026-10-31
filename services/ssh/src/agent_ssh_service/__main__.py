"""The SSH service's internal foreground container/host entry point."""

import argparse
import os
from pathlib import Path
import sys

from . import __version__


async def _foreground(config) -> int:
    from agent_procutil.daemon_instances import admit_daemon_instance

    from .hosting import UnsettledRelay, run

    with admit_daemon_instance(
        "agent-ssh-service", source=__file__, purpose="production",
        runtime_root=Path(os.path.abspath(config.to_dict()["data_dir"])),
    ):
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
    import asyncio
    import sqlite3
    import subprocess

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
