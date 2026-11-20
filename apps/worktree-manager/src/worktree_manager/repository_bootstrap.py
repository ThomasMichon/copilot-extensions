"""Optional repository caller; acquisition and setup remain engine-owned."""

from __future__ import annotations

import argparse

from .engine_client import EngineError, run_engine_passthrough
from .setup_arguments import parse as parse


def run(args: argparse.Namespace) -> int:
    print()
    print("  repository acquisition/registration and setup (engine-owned):")
    print(f"    source: {args.repository}")
    if args.repository_target:
        print(f"    target: {args.repository_target}")
    if args.repository_commit:
        print(f"    commit: {args.repository_commit}")
    if not args.apply:
        print("    plan only; setup declaration remains uninspected until owner qualification.")
        return 0
    command = ["repos", "setup", args.repository, "--apply"]
    if args.repository_target:
        command.extend(["--target", args.repository_target])
    if args.repository_commit:
        command.extend(["--commit", args.repository_commit])
    if args.trust_repository_setup:
        command.append("--trust-repository-setup")
    if args.context is not None:
        command.extend(["--context", args.context])
    try:
        return run_engine_passthrough(None, command, terminal_aware=True)
    except EngineError as error:
        print(f"    ! repository stage unavailable: {error}")
        print("      core is retained; this stage requires the engine's shared repository setup seam.")
        return 1
