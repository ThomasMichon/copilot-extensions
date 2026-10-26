"""Isolated-interpreter entry point for the shared Linux lifecycle protocol."""

from __future__ import annotations

import argparse
import base64
import math
from pathlib import Path
import sys

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent_procutil import _daemon_instance_linux as linux
from agent_procutil import _daemon_instance_store as store
from agent_procutil import _daemon_instance_clock as lease_clock
from agent_procutil.daemon_instances import DaemonInstanceReceipt


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("check")
    timing = subcommands.add_parser("lease-fields")
    timing.add_argument("ttl", type=float)
    timing.add_argument("maximum", type=float)
    supervisor = subcommands.add_parser("supervise")
    supervisor.add_argument("record", type=Path)
    supervisor.add_argument("--ready-fd", type=int)
    # Split explicitly: argparse.REMAINDER otherwise swallows --ready-fd.
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    command = []
    if arguments[:1] == ["supervise"] and "--" in arguments:
        separator = arguments.index("--")
        command, arguments = arguments[separator + 1:], arguments[:separator]
    cleanup = subcommands.add_parser("cleanup")
    cleanup.add_argument("record", type=Path)
    cleanup.add_argument("timeout", type=float)
    options = parser.parse_args(arguments)
    if not sys.platform.startswith("linux") or sys.version_info < (3, 10):
        parser.error("daemon lifecycle requires Linux and Python 3.10 or newer")
    if options.operation == "check":
        linux.check()
        return 0
    if options.operation == "lease-fields":
        for key, value in lease_clock.fields(
            options.ttl, options.maximum, store._monotonic_clock(),
        ).items():
            print(f"{key}\t{base64.b64encode(value.encode('ascii')).decode('ascii')}")
        return 0
    if options.operation == "supervise":
        if not command or (
            options.ready_fd is not None and not 3 <= options.ready_fd <= 1024
        ):
            parser.error("supervise requires a command and a valid admission descriptor")
        return linux.supervise(options.record, command, ready_fd=options.ready_fd)
    if not math.isfinite(options.timeout) or options.timeout <= 0:
        parser.error("cleanup budget must be finite and positive")
    receipt = DaemonInstanceReceipt(options.record.stem, options.record.parent)
    if options.record != receipt.record_path:
        parser.error("cleanup requires a canonical instance record path")
    result = store.cleanup(receipt, stop_timeout=min(options.timeout, 300))
    print(f"{result.status}\t{result.reason}\t{result.instance_id}")
    if result.status == "held":
        print(f"daemon instance held: {result.instance_id} {result.reason}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
