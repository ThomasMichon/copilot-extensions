"""Use the prepared stdlib process owner before uv can query an interpreter."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


def main(arguments: list[str] | None = None) -> int:
    """Capture a bounded prerequisite command without acquiring dependencies."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deadline", required=True, type=float)
    parser.add_argument("--parent-pid", required=True, type=int)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    options = parser.parse_args(arguments)
    command = options.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command or sys.version_info < (3, 10):
        parser.error("prepared capture requires Python 3.10+ and a command")
    if os.getppid() != options.parent_pid:
        parser.error("prepared capture requires the original caller, not an interpreter redirector")
    capsule = Path(__file__).with_name("daemon_instance_runtime.pyz")
    if not capsule.is_file():
        parser.error("prepared process-helper capsule is unavailable")
    sys.path.insert(0, str(capsule))
    from agent_procutil.owned_budget import OwnedExecutionBudget
    from agent_procutil.owned_process import run_caller_owned

    result = run_caller_owned(
        command, timeout=None, input_text="", max_stdout_bytes=65536,
        max_stderr_bytes=65536,
        budget=OwnedExecutionBudget(options.deadline, cleanup_reserve=0.5),
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
