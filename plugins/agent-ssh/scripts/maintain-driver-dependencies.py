"""Run explicit dependency maintenance in the selected completed SSH runtime."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import subprocess
import sys
import os


def main() -> int:
    payload = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location("_ssh_maintenance_runtime", payload / "scripts" / "snapshot_runtime.py")
    if spec is None or spec.loader is None:
        return 126
    adapter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(adapter)
    command, environment = adapter.runtime_command(payload, "agent-ssh", dict(os.environ))
    return subprocess.call(
        [*command, "driver-dependencies", "--apply", "--json", *sys.argv[1:]],
        env=environment, stdin=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW") if os.name == "nt" else 0,
    )


if __name__ == "__main__":
    raise SystemExit(main())
