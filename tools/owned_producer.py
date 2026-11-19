"""Opt-in disposable commands using the existing allocation and process owners."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import replace
import math
import os
from pathlib import Path
import sys
import time

from plugin_test_containment import ContainmentError, Limits, isolated_environment, run_contained
from plugin_test_owned_scratch import OwnedTestSandbox


class OwnedProducerSandbox(OwnedTestSandbox):
    diagnostic_schema = "copilot-extensions.owned-producer-diagnostic/v1"


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a finite positive number") from exc
    if not math.isfinite(seconds) or seconds <= 0:
        raise argparse.ArgumentTypeError("must be a finite positive number")
    return seconds


def _positive_integer(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a positive integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    """Parse the opt-in owner, bounds and literal command before any allocation."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        epilog="Consume disposable results during the command. External outputs keep their own owner.",
        allow_abbrev=False,
    )
    parser.add_argument("--owner", required=True, metavar="MACHINE/PROJECT/WORKTREE")
    parser.add_argument(
        "--kind", required=True,
        choices=("spike", "build", "archive-extraction", "report", "source-worker"),
        help="producer classification for bounded diagnostics",
    )
    defaults = Limits()
    parser.add_argument("--timeout", type=_positive_seconds, default=defaults.wall_seconds)
    parser.add_argument("--max-processes", type=_positive_integer, default=defaults.max_processes)
    parser.add_argument("--max-memory-mb", type=_positive_integer, default=defaults.max_memory_mb)
    parser.add_argument("--max-temp-mb", type=_positive_integer, default=defaults.max_temp_mb)
    parser.add_argument(
        "command", nargs=argparse.REMAINDER,
        help="-- ABSOLUTE_EXECUTABLE [ARG ...]; {scratch} expands to the private working directory",
    )
    args = parser.parse_args(list(argv))
    if len(args.command) < 2 or args.command[0] != "--":
        parser.error("a literal command after -- is required")
    args.command = args.command[1:]
    if not Path(args.command[0]).is_absolute() or "{scratch}" in args.command[0]:
        parser.error("the command executable must be an absolute path independent of scratch")
    if any("{scratch" in token.replace("{scratch}", "") for token in args.command):
        parser.error("the scratch argument marker is exactly {scratch}")
    return args


def execute(args: argparse.Namespace) -> int:
    """Join the contained command before the allocation owner settles any bytes."""
    repository = Path(__file__).resolve().parents[1]
    limits = Limits(
        wall_seconds=args.timeout, max_processes=args.max_processes,
        max_memory_mb=args.max_memory_mb, max_temp_mb=args.max_temp_mb,
    )
    limits.validate()
    scope = OwnedProducerSandbox(args.owner, repository, stage=f"producer:{args.kind}")
    with scope as raw_sandbox:
        deadline = time.monotonic() + limits.wall_seconds
        sandbox = Path(raw_sandbox)
        work = sandbox / "work"
        work.mkdir()
        environment = isolated_environment(os.environ, sandbox)
        environment["OWNED_PRODUCER_SCRATCH"] = str(work)
        command = [token.replace("{scratch}", str(work)) for token in args.command]
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            code = 124
        else:
            code = run_contained(
                command, cwd=work, env=environment, sandbox=sandbox,
                limits=replace(limits, wall_seconds=remaining),
            )
            if time.monotonic() > deadline:
                code = 124
        code = 128 - code if code < 0 else code
        scope.record_result(0, code)
        scope.record_outcome(code)
    return code


def main(argv: Sequence[str] | None = None) -> int:
    """Return the command result, or 125 when its owner cannot complete safely."""
    args = parse_arguments(sys.argv[1:] if argv is None else argv)
    try:
        return execute(args)
    except (ContainmentError, OSError, ValueError, RuntimeError) as error:
        print(f"run-owned-producer: FAILED: {error}", file=sys.stderr)
        return 125
