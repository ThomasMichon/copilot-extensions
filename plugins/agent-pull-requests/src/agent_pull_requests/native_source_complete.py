"""Capability-selected local source reconciliation without a second Git owner."""
from __future__ import annotations

import json
import re
import subprocess
import sys

from . import native_author
from .native_command import run_command
from .watch_provider import command_path

SCHEMA = "agent-worktrees.pr-source-complete"
RECOVERY = "Source completion is unconfirmed; inspect this source and retained recovery refs before retrying; no replay or fallback was attempted"


def add_parser(subparsers, repo_type, handler):
    from .native_cli import validate_selector

    parser = subparsers.add_parser(
        "complete", allow_abbrev=False,
        help="reconcile this managed local source through its existing Worktrees owner, not finalize",
    )
    parser.add_argument("--repo", required=True, type=repo_type)
    parser.add_argument("--expected-branch", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--worktrees-command", required=True, type=validate_selector)
    parser.add_argument("--dry-run", action="store_true", help="preview source reconciliation; may fetch upstream")
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=handler)


def _failure(args, message, *, dispatched=False):
    return {
        "schema": SCHEMA, "version": 1, "repo": args.repo, "source": None,
        "dry_run": args.dry_run, "ok": False,
        "status": "unknown" if dispatched else "not-attempted", "error": message,
        "frontend": {"expected_branch": args.expected_branch, "expected_head": args.expected_head},
    }


def _valid(payload, args):
    if not isinstance(payload, dict):
        return False
    status, source = payload.get("status"), payload.get("source")
    success = status in ("preview", "confirmed")
    if (
        payload.get("schema") != SCHEMA or type(payload.get("version")) is not int
        or payload["version"] != 1 or payload.get("repo") != args.repo
        or type(payload.get("dry_run")) is not bool or payload["dry_run"] != args.dry_run
        or type(payload.get("ok")) is not bool or payload["ok"] != success
        or status not in ("not-attempted", "preview", "confirmed", "unknown")
        or success and (status == "preview") != args.dry_run
        or success and payload.get("error")
        or not success and (
            not isinstance(payload.get("error"), str) or not payload["error"].strip()
        )
    ):
        return False
    if source is not None or status != "not-attempted":
        if (
            not isinstance(source, dict)
            or not isinstance(source.get("worktree_id"), str) or not source["worktree_id"].strip()
            or source.get("branch") != args.expected_branch
            or source.get("expected_head") != args.expected_head
        ):
            return False
    result = payload.get("result")
    if result is not None and (
        not isinstance(result, dict) or not isinstance(source, dict)
        or result.get("worktree_id") != source["worktree_id"]
        or "branch" in result and result["branch"] != args.expected_branch
    ):
        return False
    if success:
        if (
            not isinstance(result, dict) or result.get("success") is not True
            or result.get("worktree_id") != source["worktree_id"]
            or result.get("branch") != args.expected_branch
            or result.get("action") not in ("up-to-date", "fast-forwarded", "reset-past-squash", "rebased")
            or result.get("error")
        ):
            return False
    return True


def run(args, prefix_factory=None):
    dispatched, payload, diagnostic = False, None, ""
    try:
        if (
            not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", args.expected_head)
            or not args.expected_branch or args.expected_branch.startswith(("-", "refs/"))
            or any(c in args.expected_branch for c in "\0\r\n")
        ):
            raise ValueError("Completion requires the exact local branch and a full lowercase Git object ID")
        prefix = prefix_factory() if prefix_factory is not None else [command_path(args.worktrees_command)]
        native_author.capability(args, prefix, "local-source-complete")
        argv = [
            *prefix, "pr-complete", f"--repo={args.repo}",
            f"--expected-branch={args.expected_branch}", f"--expected-head={args.expected_head}", "--json",
        ]
        if args.dry_run:
            argv.append("--dry-run")
        dispatched = True
        response = run_command(argv, timeout=90.0)
        candidate = json.loads(response.stdout)
        if not _valid(candidate, args):
            raise ValueError(RECOVERY)
        payload = candidate
        code = response.returncode or (0 if payload["ok"] else 1)
        if response.returncode and payload["ok"]:
            diagnostic = RECOVERY
        elif not payload["ok"]:
            diagnostic = payload["error"]
            if payload["status"] == "unknown":
                diagnostic += "; " + RECOVERY
                backup = (payload.get("result") or {}).get("backup_ref")
                if backup:
                    diagnostic += f"; retained recovery ref: {backup}"
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, UnicodeError) as exc:
        message = str(exc) or "Selected source owner is unavailable"
        diagnostic = f"{message}; {RECOVERY}" if dispatched and message != RECOVERY else message
        payload, code = _failure(args, diagnostic, dispatched=dispatched), 1
    if args.json or payload["ok"]:
        print(json.dumps(payload, indent=2, sort_keys=True))
    if diagnostic:
        print(f"agent-pull-requests: {diagnostic}", file=sys.stderr)
    return code
