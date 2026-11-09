"""On-demand unified diffs through the existing target-qualified native owner."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys

from .native_command import run_command
from .watch_provider import NATIVE_SNAPSHOT_TIMEOUT, command_path

MAX_REPORT_BYTES = 1_048_576


def add_parser(subparsers, *, repo_type, handler):
    """Register a native-only read without a claimant or target checkout."""
    from .native_cli import validate_selector
    from .native_threads import _positive_number

    def repository(value):
        if value.startswith("-"):
            raise argparse.ArgumentTypeError("repo must not begin with an option prefix")
        return repo_type(value)

    parser = subparsers.add_parser(
        "diff", help="read a native unified diff on demand", allow_abbrev=False,
    )
    parser.add_argument("--repo", required=True, type=repository)
    parser.add_argument("--number", required=True, type=_positive_number)
    parser.add_argument("--worktrees-command", required=True, type=validate_selector)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=handler)


def _report(text, repo, number, *, max_bytes=MAX_REPORT_BYTES):
    if (
        not isinstance(text, str) or len(text) > max_bytes
        or len(text.encode("utf-8")) > max_bytes
    ):
        raise ValueError("native diff report is unavailable or exceeds its size budget")
    try:
        body = json.loads(text)
        json.dumps(body, allow_nan=False)
    except (ValueError, TypeError, RecursionError):
        raise ValueError("native diff report is not valid JSON") from None
    if (
        not isinstance(body, dict)
        or body.get("schema") != "agent-worktrees.pr-diff-read"
        or type(body.get("version")) is not int or body["version"] != 1
        or body.get("repo") != repo
        or type(body.get("pr")) is not int or body["pr"] != number
        or not isinstance(body.get("provider"), str) or not body["provider"].strip()
        or type(body.get("supported")) is not bool
        or "complete" not in body
        or body["complete"] is not None and type(body["complete"]) is not bool
        or not isinstance(body.get("error"), str)
        or not isinstance(body.get("diff"), str)
    ):
        raise ValueError("native diff report is incompatible or differs from the requested target")
    return body


def run(args, prefix_factory=None) -> int:
    """Keep acquisition outcome separate from complete/current review evidence."""
    try:
        launch = prefix_factory() if prefix_factory is not None else [command_path(args.worktrees_command)]
        if not launch or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in launch):
            raise ValueError("invalid attributed native peer prefix")
        command_path(launch[0])
        result = run_command(
            [*launch, "pr-watch", "diff", args.repo, str(args.number), "--json"],
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
            ) or body["error"] or "native diff read failed or is unsupported"
            print(f"agent-pull-requests: {detail}", file=sys.stderr)
        print(json.dumps(body, indent=2, sort_keys=True))
        return code
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError, UnicodeError) as exc:
        diagnostic = f"Native diff evidence unavailable: {exc}; no retry or forge fallback was attempted"
        if args.json:
            print(json.dumps({"repo": args.repo, "number": args.number, "error": diagnostic}))
        else:
            print(f"agent-pull-requests: {diagnostic}", file=sys.stderr)
        return 1
