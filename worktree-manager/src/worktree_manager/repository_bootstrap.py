"""Optional repository caller; acquisition and setup remain engine-owned."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlsplit

from .engine_client import EngineError, run_engine_passthrough


def _repository(value: str) -> str:
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError:
        raise argparse.ArgumentTypeError("repository URL is malformed") from None
    if (
        parsed.scheme != "https" or not parsed.hostname
        or not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", parsed.hostname)
        or parsed.username is not None or parsed.password is not None
        or not parsed.path.strip("/") or "?" in value or "#" in value or "\\" in value
        or any(c.isspace() or ord(c) < 32 or 127 <= ord(c) <= 159 for c in value)
        or any(p in ("", ".", "..") for p in parsed.path.rstrip("/").split("/")[1:])
    ):
        raise argparse.ArgumentTypeError("repository requires credential-free HTTPS without query or fragment")
    return value


def _commit(value: str) -> str:
    if not re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", value):
        raise argparse.ArgumentTypeError("repository commit must be a full 40- or 64-hex commit ID")
    return value.lower()


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="worktree-manager setup", allow_abbrev=False)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--repository", type=_repository)
    parser.add_argument("--repository-target")
    parser.add_argument("--repository-commit", type=_commit)
    parser.add_argument("--trust-repository-setup", action="store_true")
    args = parser.parse_args(argv)
    if (
        args.repository_target is not None or args.repository_commit is not None
        or args.trust_repository_setup
    ) and not args.repository:
        parser.error("repository target, commit and setup trust require --repository")
    if args.repository_target is not None and not Path(args.repository_target).expanduser().is_absolute():
        parser.error("--repository-target must be absolute")
    return args


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
    try:
        return run_engine_passthrough(None, command, terminal_aware=True)
    except EngineError as error:
        print(f"    ! repository stage unavailable: {error}")
        print("      core is retained; this stage requires the engine's shared repository setup seam.")
        return 1
