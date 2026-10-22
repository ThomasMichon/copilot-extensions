"""Thin native PR commands; Worktrees retains provider and publication policy."""

from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import math
import subprocess
import sys
import time

from .native_command import run_command
from .watch_provider import command_path, fetch_snapshot


def validate_selector(value: str) -> str:
    if not value.strip():
        raise argparse.ArgumentTypeError("worktrees command selector must not be empty")
    return value


class MergeMethodAction(argparse.Action):
    def __call__(self, parser, namespace, values, option_string=None):
        namespace.method = self.const
        namespace.method_explicit = True


def _mutation(args: argparse.Namespace, prefix: list[str]) -> tuple[int, dict]:
    if args.command == "create":
        if not args.head.strip():
            raise ValueError("native create requires a nonempty pushed head branch")
        if args.base is not None:
            raise ValueError(
                "native create uses the target's configured base; --base is not supported"
            )
        argv = [
            "pr-create", f"--repo={args.repo}", f"--from-branch={args.head}",
            f"--title={args.title}", f"--body={args.body}", "--json",
        ]
        if args.draft:
            argv.append("--draft")
    else:
        if args.method_explicit or args.delete_branch or args.auto:
            raise ValueError(
                "native merge uses the target's merge policy; "
                "--squash, --merge, --rebase, --auto and --delete-branch are not supported"
            )
        argv = ["pr-merge", args.repo, str(args.number), "--now", "--json"]
    try:
        result = run_command([*prefix, *argv], timeout=90.0)
    except (OSError, RuntimeError, subprocess.SubprocessError, UnicodeError):
        raise RuntimeError(
            "native PR command did not return a confirmed result; it may have applied. "
            "Reconcile with native status before retrying; no GitHub fallback was attempted"
        ) from None
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        detail = result.stderr.strip() or "native PR command returned non-JSON output"
        raise RuntimeError(f"{detail}; outcome unconfirmed, reconcile before retrying") from None
    if not isinstance(payload, dict):
        raise RuntimeError("native PR command returned an invalid result; reconcile before retrying")
    if result.returncode or payload.get("error"):
        if not payload.get("error"):
            payload["error"] = "native PR command refused or failed"
        return result.returncode or 1, payload
    number_key = "number" if args.command == "create" else "pr"
    if (
        payload.get("repo") != args.repo
        or type(payload.get(number_key)) is not int or payload[number_key] <= 0
        or args.command == "merge" and payload["pr"] != args.number
        or args.command == "create" and (
            payload.get("pr_opened") is not True
            or not isinstance(payload.get("url"), str) or not payload["url"]
        )
    ):
        raise RuntimeError("native PR command returned a mismatched result; reconcile before retrying")
    return 0, payload


def _read(args: argparse.Namespace, prefix: list[str]) -> tuple[int, dict]:
    waiting = args.command == "wait"
    if waiting and any(
        not math.isfinite(value) or value < 0 for value in (args.timeout, args.interval)
    ):
        raise ValueError("native wait timeout and interval must be finite and nonnegative")
    deadline = time.monotonic() + args.timeout if waiting else 0.0
    while True:
        snapshot = fetch_snapshot(
            args.repo, args.number, args.worktrees_command, prefix=prefix,
            runner=lambda argv: run_command(argv, timeout=20.0),
        )
        payload = dict(snapshot.native_snapshot)
        if not waiting:
            return 0, payload
        if snapshot.merged or snapshot.pr_state == "closed":
            return (0 if snapshot.merged else 1), {**payload, "timedOut": False}
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return 3, {**payload, "timedOut": True}
        time.sleep(min(args.interval, remaining))


def run(
    args: argparse.Namespace, *, prefix_factory: Callable[[], list[str]] | None = None,
) -> int:
    try:
        launch = prefix_factory() if prefix_factory is not None else [command_path(args.worktrees_command)]
        code, payload = (
            _mutation(args, launch) if args.command in ("create", "merge")
            else _read(args, launch)
        )
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        code, payload = 1, {"error": str(exc), "repo": args.repo}
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    elif payload.get("error"):
        print(f"agent-pull-requests: {payload['error']}", file=sys.stderr)
    else:
        # Retain native votes, gates, consent and completion evidence, not a
        # GitHub-shaped summary that could mistake a vote for approval.
        print(json.dumps(payload, indent=2, sort_keys=True))
    return code
