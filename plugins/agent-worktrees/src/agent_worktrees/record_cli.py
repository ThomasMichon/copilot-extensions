"""CLI for exact, read-only durable worktree metadata."""

from __future__ import annotations

import argparse

from . import output
from .record_read import RecordReadError, read_exact_record, read_workspace_record


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "record",
        help="Read one exact durable worktree record in this project (JSON; no launch authority)",
    )
    selection = sp.add_mutually_exclusive_group(required=True)
    selection.add_argument("--worktree-id", help="Exact recorded worktree ID (no suffix lookup)")
    selection.add_argument(
        "--cwd", help="Absolute trusted initialization directory (no adoption or resident lookup)",
    )
    selection.add_argument("--describe-closeout-contract", action="store_true")
    selection.add_argument("--describe-readiness-contract", action="store_true")
    sp.add_argument("--closeout-task")
    sp.add_argument("--closeout-reservation")
    sp.add_argument("--closeout-native")
    sp.add_argument("--readiness-operation", choices=("observe-ready", "check-own-ready"))
    sp.add_argument("--readiness-task")
    sp.add_argument("--readiness-reservation")
    sp.add_argument("--readiness-native")
    sp.add_argument("--readiness-creation-nonce")
    sp.add_argument("--readiness-head-revision", type=int)
    sp.add_argument("--readiness-task-claim-ref")
    sp.add_argument("--json", action="store_true", help="Emit JSON (the default)")


def cmd_record(args: argparse.Namespace) -> int:
    """Return exact ground-owned metadata without discovery or mutation."""
    try:
        closeout = (
            getattr(args, "closeout_task", None), getattr(args, "closeout_reservation", None),
            getattr(args, "closeout_native", None),
        )
        described = getattr(args, "describe_closeout_contract", False)
        readiness = tuple(
            getattr(args, f"readiness_{name}", None)
            for name in (
                "operation", "task", "reservation", "native",
                "creation_nonce", "head_revision", "task_claim_ref",
            )
        )
        readiness_described = getattr(args, "describe_readiness_contract", False)
        if readiness_described or any(value is not None for value in readiness):
            from . import dispatch_worker_readiness

            if described or any(value is not None for value in closeout):
                raise ValueError("Readiness and finalized closeout requests cannot be mixed")
            if readiness_described:
                if any(value is not None for value in readiness):
                    raise ValueError("Readiness discovery cannot include an observation request")
                output._json_output(dispatch_worker_readiness.describe_contract())
                return 0
            if not getattr(args, "worktree_id", None) or any(
                value is None for value in readiness
            ):
                raise ValueError(
                    "Readiness requires an exact worktree, operation, task, reservation, "
                    "native, creation nonce, head revision and task claim"
                )
            operation, task, reservation, native, nonce, revision, ref = readiness
            observation = dispatch_worker_readiness.observe_ready(
                args.worktree_id, task, reservation, native,
                creation_nonce=nonce, head_revision=revision, task_claim_ref=ref,
                own_caller=operation == "check-own-ready",
            )
            output._json_output({"ok": True, **observation})
            return 0
        if described or any(value is not None for value in closeout):
            from . import dispatch_closeout_observation
            if described:
                if any(value is not None for value in closeout):
                    raise ValueError("Closeout discovery cannot include an observation request")
                output._json_output(dispatch_closeout_observation.describe_contract())
                return 0
            if not getattr(args, "worktree_id", None) or any(value is None for value in closeout):
                raise ValueError("Closeout observation requires worktree, task, reservation and native IDs")
            observation = dispatch_closeout_observation.observe(args.worktree_id, *closeout)
            output._json_output({"ok": True, **observation})
            return 0
        if getattr(args, "cwd", None) is not None:
            context = read_workspace_record(args.cwd)
            output._json_output({"ok": True, "context": context})
            return 0
        worktree = read_exact_record(args.worktree_id)
    except RecordReadError as exc:
        output._json_output({
            "ok": False, "worktree": None,
            "error": {"code": exc.code, "message": str(exc)},
        })
        return 1
    except (OSError, RuntimeError, ValueError) as exc:
        output._json_output({"ok": False, "error": str(exc)})
        return 1
    output._json_output({"ok": True, "worktree": worktree})
    return 0
