"""Use the prepared stdlib process owner before uv can query an interpreter."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import sys
import time
from types import FrameType
from typing import NoReturn


def _input_deadline(_signal: int, _frame: FrameType | None) -> NoReturn:
    raise TimeoutError("prepared capture input deadline expired")


def _read_input(deadline: float) -> bytes:
    if os.name == "nt":
        raise ValueError("prepared stdin capture requires POSIX signal deadlines")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("prepared capture input deadline expired")
    alarm = getattr(signal, "SIGALRM")
    timer = getattr(signal, "setitimer")
    real = getattr(signal, "ITIMER_REAL")
    previous = signal.signal(alarm, _input_deadline)
    data = bytearray()
    try:
        timer(real, remaining)
        while len(data) <= 65536:
            block = os.read(sys.stdin.fileno(), min(65536, 65537 - len(data)))
            if not block:
                return bytes(data)
            data.extend(block)
        raise ValueError("prepared capture input exceeds 65536 bytes")
    finally:
        timer(real, 0)
        signal.signal(alarm, previous)


def main(arguments: list[str] | None = None) -> int:
    """Capture a bounded prerequisite command without acquiring dependencies."""
    started = time.monotonic()
    parser = argparse.ArgumentParser(description=__doc__)
    timing = parser.add_mutually_exclusive_group(required=True)
    timing.add_argument("--deadline", type=float)
    timing.add_argument("--timeout", type=float)
    parser.add_argument("--cleanup-reserve", type=float, default=0.5)
    parser.add_argument("--input-stdin", action="store_true")
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

    deadline = options.deadline if options.deadline is not None else started + options.timeout
    budget = OwnedExecutionBudget(deadline, cleanup_reserve=options.cleanup_reserve)
    payload = _read_input(budget.execution_deadline) if options.input_stdin else None
    result = run_caller_owned(
        command, timeout=None, input_text=None if options.input_stdin else "",
        input_bytes=payload, binary=options.input_stdin,
        max_stdout_bytes=65536, max_stderr_bytes=65536, budget=budget,
    )
    if options.input_stdin:
        sys.stdout.buffer.write(result.stdout)
        sys.stderr.buffer.write(result.stderr)
    else:
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
    return int(result.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
