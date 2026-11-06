"""The SSH service's internal foreground container/host entry point."""

import argparse
import os
from pathlib import Path
import sys
from typing import TYPE_CHECKING

from . import __version__

if TYPE_CHECKING:
    from .config import ServiceConfig
    from .local_control import LocalControl

async def _foreground(config: "ServiceConfig", *, lifecycle: "LocalControl | None" = None) -> int:
    from agent_procutil.daemon_instances import admit_daemon_instance

    from .hosting import UnsettledRelay, run

    with admit_daemon_instance(
        "agent-ssh-service", source=__file__, purpose="production",
        runtime_root=Path(os.path.abspath(config.to_dict()["data_dir"])),
    ):
        try:
            if lifecycle is None:
                await run(config)
            else:
                await run(config, lifecycle=lifecycle)
            return 0
        except UnsettledRelay as unsettled:
            print("agent-ssh-service: native-relay-cleanup-unconfirmed; "
                  "foreground custody and writer lease retained", file=sys.stderr)
            await unsettled.settle()
            return 1


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in {"__source-start", "__local-serve", "__local-stop"}:
        from .source_start import dispatch

        try:
            return dispatch(argv)
        except (OSError, ValueError, RuntimeError) as error:
            from .config import SSHServiceError

            diagnostic = str(error) if isinstance(error, SSHServiceError) or str(error) == (
                "installed-Source-provider-unsupported"
            ) else type(error).__name__
            print("agent-ssh-service: " + diagnostic, file=sys.stderr)
            return 1
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
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
