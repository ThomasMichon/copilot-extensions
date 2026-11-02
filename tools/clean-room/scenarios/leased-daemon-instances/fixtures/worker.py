"""A finite-proof service body using the shipped direct-start admission API."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True)
    parser.add_argument("--ready", type=Path, required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--abrupt-exit", action="store_true")
    args = parser.parse_args()
    sys.path.insert(0, str(args.source / "libs" / "agent-procutil" / "src"))
    from agent_procutil.daemon_instances import admit_daemon_instance

    guard = admit_daemon_instance(
        args.service, runtime_root=args.runtime, source=Path(__file__)
    )
    artifact = args.runtime / "owned-runtime-state.txt"
    artifact.write_text("disposable runtime state\n", encoding="utf-8")
    guard.add_artifact(artifact)
    args.ready.write_text(
        json.dumps({"pid": os.getpid(), "instance_id": guard.receipt.instance_id}),
        encoding="utf-8",
    )
    if args.abrupt_exit:
        # Deliberately bypass teardown so installer reconciliation has real work.
        os._exit(0)
    try:
        while True:
            time.sleep(0.1)
    finally:
        guard.close()


if __name__ == "__main__":
    main()
