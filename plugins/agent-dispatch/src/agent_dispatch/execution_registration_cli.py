"""Parser registration for resolve/run/evaluate/charter CLI families."""

from __future__ import annotations

import argparse

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def register_execution_commands(sub) -> None:
    cp = sub.add_parser(
        "charter",
        help="the shared 'how to behave as an agent-dispatch worker' policy prose, pulled on demand instead of always inlined in a seed",
    )
    csub = cp.add_subparsers(dest="charter_command", required=True)
    csp = csub.add_parser("show", help="print a named charter's full text")
    csp.add_argument("name", help="charter name, e.g. 'autopilot'")
    csp.set_defaults(func=_core()._cmd_charter_show)

    rvp = sub.add_parser(
        "resolve",
        help="drive THIS worktree to a clean, resolved final state after a loop (landed -> verify clean; abandoned -> unwind to base)",
    )
    rvp.add_argument("--outcome", required=True, choices=["landed", "abandoned"])
    rvp.add_argument("--base", metavar="BRANCH")
    rvp.add_argument("--source", metavar="REF")
    rvp.add_argument("--reason")
    rvp.add_argument("--execute", action="store_true", help="perform the plan (destructive steps discard working-tree state); without it, the plan is printed and nothing runs")
    rvp.set_defaults(func=_core()._cmd_resolve)

    rnp = sub.add_parser(
        "run",
        help="hand a blocking wait to the layer (hibernate-the-wait): run '-- <cmd>' until it resolves or a repeated-timeout backstop gives up, then resume the worktree-affinitied worker via agent-bridge",
    )
    rnp.add_argument("--resume", metavar="WORKTREE")
    rnp.add_argument("--task", metavar="ID")
    rnp.add_argument("--message")
    rnp.add_argument("--detach", action="store_true")
    rnp.add_argument("--waiter-child", action="store_true", help=argparse.SUPPRESS)
    rnp.add_argument("--waiter-generation", type=int, help=argparse.SUPPRESS)
    rnp.add_argument("--machine")
    rnp.add_argument("--worktree")
    rnp.add_argument(
        "--pr-watch-repo",
        metavar="OWNER/NAME",
        help=(
            "delegate this detached wait to the shared agent-pull-requests "
            "watch daemon instead of spawning a per-task OS waiter process "
            "-- the repo slug to watch (requires --detach, --task, and "
            "--pr-watch-number; the '-- <cmd>' tail becomes optional/"
            "informational only, since nothing is actually executed)"
        ),
    )
    rnp.add_argument(
        "--pr-watch-number",
        type=int,
        metavar="N",
        help="PR number to watch (paired with --pr-watch-repo)",
    )
    rnp.add_argument(
        "--pr-watch-until",
        metavar="CSV",
        help=(
            "comma-list of agent-pull-requests transitions to wake on "
            "(merged, closed, review_changed, mergeable_changed, "
            "checks_changed); default: its own 'merged,closed'"
        ),
    )
    rnp.add_argument(
        "--pr-watch-timeout",
        type=float,
        metavar="SECONDS",
        help=(
            "bound on how long the delegated subscription stays active "
            "before firing a timed-out event on its own (default: 3 days) "
            "-- never unbounded, so an abandoned PR's subscription always "
            "eventually clears"
        ),
    )
    rnp.add_argument("--finish-delegated-waiter", action="store_true", help=argparse.SUPPRESS)
    rnp.add_argument("--pr-watch-start-token", help=argparse.SUPPRESS)
    rnp.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="the blocking wait command, after '--' (e.g. -- agent-worktrees pr-watch 42)",
    )
    rnp.set_defaults(func=_core()._cmd_run)

    evp = sub.add_parser(
        "evaluate",
        help="feed one task lifecycle event through a declarative evaluator and apply its decisions (emit a follow-up task, or nothing)",
    )
    evp.add_argument("--spec", required=True, metavar="FILE", help="evaluator spec (JSON)")
    evp.add_argument(
        "--evaluator-ref",
        help="opaque selector for a script-based evaluator within the loaded spec",
    )
    evp.add_argument("--event-file", metavar="FILE", help="lifecycle event JSON (default: read from stdin)")
    evp.add_argument("--repo", help="lane for any emitted follow-up task (a local name or remote URL)")
    evp.add_argument("--dry-run", action="store_true", help="print the decisions without creating any follow-up task")
    evp.set_defaults(func=_core()._cmd_evaluate)

    vsp = sub.add_parser(
        "verify-submitted",
        help="explicitly re-run whole-goal verification for specific submitted task ids",
    )
    vsp.add_argument(
        "--evaluator-ref",
        help="atomically opt the named submitted task(s) into verification using this evaluator before re-checking",
    )
    vsp.add_argument("task_id", nargs="+")
    vsp.set_defaults(func=_core()._cmd_verify_submitted)
