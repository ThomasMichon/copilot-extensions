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
        help="hand a blocking wait to the layer (hibernate-the-wait): run '-- <cmd>' to completion, then resume the worktree-affinitied worker via agent-bridge",
    )
    rnp.add_argument("--resume", metavar="WORKTREE")
    rnp.add_argument("--task", metavar="ID")
    rnp.add_argument("--message")
    rnp.add_argument("--detach", action="store_true")
    rnp.add_argument("--machine")
    rnp.add_argument("--worktree")
    rnp.add_argument("command", nargs=argparse.REMAINDER, help="the blocking wait command, after '--' (e.g. -- agent-worktrees pr-watch 42)")
    rnp.set_defaults(func=_core()._cmd_run)

    evp = sub.add_parser(
        "evaluate",
        help="feed one task lifecycle event through a declarative evaluator and apply its decisions (emit a follow-up task, or nothing)",
    )
    evp.add_argument("--spec", required=True, metavar="FILE", help="evaluator spec (JSON)")
    evp.add_argument("--event-file", metavar="FILE", help="lifecycle event JSON (default: read from stdin)")
    evp.add_argument("--repo", help="lane for any emitted follow-up task (a local name or remote URL)")
    evp.add_argument("--dry-run", action="store_true", help="print the decisions without creating any follow-up task")
    evp.set_defaults(func=_core()._cmd_evaluate)
