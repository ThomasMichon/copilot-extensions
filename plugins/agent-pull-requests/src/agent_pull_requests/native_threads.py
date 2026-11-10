"""On-demand native discussion reads through the existing Worktrees owner."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys

from .native_command import run_command
from .watch_provider import NATIVE_SNAPSHOT_TIMEOUT, command_path


def _positive_number(value: str) -> int:
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("--number must be positive")
    return number


def add_parser(subparsers, *, repo_type, handler):
    """Register the native-only explicit target read."""
    from .native_cli import validate_selector

    def repository(value):
        if value.startswith("-"):
            raise argparse.ArgumentTypeError("repo must not begin with an option prefix")
        return repo_type(value)

    parser = subparsers.add_parser(
        "threads", help="read native discussion bodies on demand", allow_abbrev=False,
    )
    parser.add_argument("--repo", required=True, type=repository)
    parser.add_argument("--number", required=True, type=_positive_number)
    parser.add_argument("--worktrees-command", required=True, type=validate_selector)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=handler)


def _report(text, repo, number, *, max_bytes=1_048_576):
    if not isinstance(text, str) or len(text) > max_bytes or len(text.encode("utf-8")) > max_bytes:
        raise ValueError("native thread report is unavailable or exceeds its size budget")
    try:
        body = json.loads(text)
        json.dumps(body, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("native thread report is not valid JSON") from None
    if (
        not isinstance(body, dict)
        or body.get("schema") != "agent-worktrees.pr-threads"
        or type(body.get("version")) is not int or body["version"] != 1
        or body.get("repo") != repo
        or type(body.get("pr")) is not int or body["pr"] != number
        or not isinstance(body.get("provider"), str) or not body["provider"].strip()
        or type(body.get("supported")) is not bool
        or "complete" not in body
        or body["complete"] is not None and type(body["complete"]) is not bool
        or not isinstance(body.get("error"), str)
        or not isinstance(body.get("threads"), list)
        or any(not isinstance(thread, dict) for thread in body["threads"])
    ):
        raise ValueError("native thread report is incompatible or differs from the requested target")
    return body


def run(args, prefix_factory=None) -> int:
    """Preserve valid owner evidence, independently reporting invocation failure."""
    try:
        launch = prefix_factory() if prefix_factory is not None else [command_path(args.worktrees_command)]
        if not launch or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in launch):
            raise ValueError("invalid attributed native peer prefix")
        command_path(launch[0])
        result = run_command(
            [*launch, "pr-watch", "threads", args.repo, str(args.number), "--json"],
            timeout=NATIVE_SNAPSHOT_TIMEOUT,
        )
        try:
            body = _report(result.stdout, args.repo, args.number)
        except ValueError as exc:
            detail = result.stderr.strip() if isinstance(result.stderr, str) else ""
            raise ValueError(f"{exc}; {detail}" if detail else str(exc)) from None
        code = 1 if result.returncode or body["error"] or not body["supported"] else 0
        if code:
            detail = (
                result.stderr.strip() if isinstance(result.stderr, str) else ""
            ) or body["error"] or "native thread read failed or is unsupported"
            print(f"agent-pull-requests: {detail}", file=sys.stderr)
        print(json.dumps(body, indent=2, sort_keys=True))
        return code
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        diagnostic = f"Native thread evidence unavailable: {exc}; no retry or forge fallback was attempted"
        if args.json:
            print(json.dumps({"repo": args.repo, "number": args.number, "error": diagnostic}))
        else:
            print(f"agent-pull-requests: {diagnostic}", file=sys.stderr)
        return 1
