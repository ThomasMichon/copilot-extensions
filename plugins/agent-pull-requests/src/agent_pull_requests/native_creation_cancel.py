"""Exact unpublished-creation cancellation through the existing Worktrees owner."""
from __future__ import annotations

import json
import subprocess

from .native_command import run_command
from .watch_provider import command_path

CONTRACT = "agent-worktrees.pr-creation-cancellation.v1"


def add_parser(subparsers, handler):
    from .native_cli import validate_selector

    parser = subparsers.add_parser(
        "cancel-creation", allow_abbrev=False,
        help="cancel one explicitly approved unpublished creation in its owning worktree",
    )
    parser.add_argument("--worktree", required=True, help="exact full owning worktree ID")
    parser.add_argument("--creation", required=True, help="immutable creation ID")
    parser.add_argument("--expected-revision", required=True, type=int)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--confirm", action="store_true", help="requires actual operator approval")
    parser.add_argument("--worktrees-command", required=True, type=validate_selector)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(handler=handler)


def _failure(args, error, status, body=None):
    payload = dict(body) if isinstance(body, dict) else {}
    payload.setdefault("worktree_id", args.worktree)
    payload.setdefault("pr_id", args.creation)
    if not payload.get("error"):
        payload["error"] = error
    frontend = payload.get("frontend")
    payload["frontend"] = {
        **(frontend if isinstance(frontend, dict) else {}),
        "cancellation_status": status, "diagnostic": error,
        "selected_worktree_id": args.worktree, "selected_pr_id": args.creation,
        "selected_expected_revision": args.expected_revision, "selected_reason": args.reason,
    }
    return 1, payload


def _discovery(body, args):
    prs = body.get("prs") if isinstance(body, dict) else None
    if (
        not isinstance(body, dict) or body.get("error")
        or body.get("worktree_id") != args.worktree
        or body.get("creation_cancellation_contract") != CONTRACT
        or not isinstance(prs, list) or any(not isinstance(pr, dict) for pr in prs)
        or sum(pr.get("pr_id") == args.creation for pr in prs) != 1
    ):
        raise ValueError(
            "Selected Worktrees runtime did not declare the v1 cancellation contract "
            "and one exact creation in the same full worktree; retain/reconcile the original attempt"
        )


def _confirmed(body, args):
    return (
        isinstance(body, dict) and not body.get("error")
        and body.get("contract") == CONTRACT
        and body.get("worktree_id") == args.worktree and body.get("pr_id") == args.creation
        and body.get("success") is True and body.get("state") == "abandoned"
        and type(body.get("pr_revision")) is int
        and body["pr_revision"] > args.expected_revision
        and body.get("creation_cancelled") is True
        and type(body.get("already_cancelled")) is bool
        and body.get("provider_closed") is False and body.get("claim_released") is False
    )


def _stderr(response, fallback):
    value = response.stderr
    return (value.strip() or fallback) if isinstance(value, str) else fallback


def _decode(response):
    try:
        return json.loads(response.stdout)
    except (ValueError, TypeError):
        raise ValueError(_stderr(response, "Worktrees returned non-JSON cancellation output")) from None


def run(args, prefix_factory=None):
    """Admission precedes discovery; after dispatch an invalid result stays unknown."""
    dispatched, body = False, None
    try:
        if any(
            not value.strip() or value != value.strip() or value.startswith("-") or "\0" in value
            for value in (args.worktree, args.creation)
        ) or type(args.expected_revision) is not int or args.expected_revision < 0:
            raise ValueError("Cancellation requires exact worktree/creation IDs and a nonnegative integer revision")
        if not args.reason.strip() or "\0" in args.reason:
            raise ValueError("Cancellation requires a nonblank --reason")
        if not args.confirm:
            raise ValueError(
                "Cancellation requires actual operator approval and explicit --confirm. "
                "Retain or reconcile useful or uncertain work; a rebase, conflict or delay "
                "is not grounds to abandon it. No Worktrees cancellation warning was executed."
            )
        prefix = prefix_factory() if prefix_factory is not None else [command_path(args.worktrees_command)]
        response = run_command(
            [*prefix, "pr-status", args.worktree, "--all", "--no-live", "--json"], timeout=20.0,
        )
        body = _decode(response)
        if response.returncode:
            raise ValueError(_stderr(response, "Worktrees cancellation discovery failed"))
        _discovery(body, args)
        body = None
        dispatched = True
        response = run_command([
            *prefix, "pr-abandon", args.worktree, f"--creation={args.creation}",
            f"--expected-revision={args.expected_revision}", f"--reason={args.reason}",
            "--confirm", "--json",
        ], timeout=90.0)
        body = _decode(response)
        if response.returncode or not _confirmed(body, args):
            raise ValueError(_stderr(response, "Worktrees cancellation result is unconfirmed"))
        frontend = body.get("frontend")
        body["frontend"] = {
            **(frontend if isinstance(frontend, dict) else {}), "cancellation_status": "confirmed",
        }
        return 0, body
    except (ValueError, TypeError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        status = "unknown" if dispatched else "not-attempted"
        detail = (
            f"{exc}; cancellation outcome is unknown. Reconcile this original owner/attempt "
            "before intentional recovery with the original revision and reason; no retry or fallback was attempted"
            if dispatched else str(exc)
        )
        return _failure(args, detail, status, body)
