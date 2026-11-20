"""Setup intent parsing without application or engine dependencies."""
from __future__ import annotations

import argparse
from pathlib import Path
import re
from urllib.parse import urlsplit


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


def _context(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise argparse.ArgumentTypeError("context must be a declared lowercase identifier")
    return value


def parse(argv: list[str], *, register: bool = False) -> argparse.Namespace:
    command = "register" if register else "setup"
    parser = argparse.ArgumentParser(
        prog=f"worktree-manager {command}", allow_abbrev=False,
        epilog=None if register else "toolchain-config-only=1",
    )
    parser.add_argument("--apply", action="store_true")
    parser.set_defaults(toolchain_only=False)
    if not register:
        parser.add_argument(
            "--toolchain-only", action="store_true",
            help="validate/apply selected toolchain defaults without provisioning or repository setup",
        )
    parser.add_argument("repository" if register else "--repository", type=_repository)
    parser.add_argument("--repository-target")
    parser.add_argument("--repository-commit", type=_commit)
    parser.add_argument("--trust-repository-setup", action="store_true")
    parser.add_argument("--context", type=_context)
    args = parser.parse_args(argv)
    if args.toolchain_only and (
        args.repository is not None or args.repository_target is not None
        or args.repository_commit is not None or args.trust_repository_setup
        or args.context is not None
    ):
        parser.error("--toolchain-only cannot be combined with repository setup")
    if (
        args.repository_target is not None or args.repository_commit is not None
        or args.trust_repository_setup or args.context is not None
    ) and not args.repository:
        parser.error("repository target, commit and setup trust require --repository")
    if args.repository_target is not None and not Path(args.repository_target).expanduser().is_absolute():
        parser.error("--repository-target must be absolute")
    return args
