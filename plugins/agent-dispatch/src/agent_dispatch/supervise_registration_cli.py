"""Parser registration for the supervise CLI family."""

from __future__ import annotations

import argparse

from .loop_commands import _resolve_cli_module
from .producers_cli import register_reservations_command
from .registrations import RegistrationKind


def register_supervise_commands(sub) -> None:
    p = sub.add_parser(
        "supervise",
        help="embody spawn supervisor: turn queued (label-gated) tasks into host embody autopilots, exactly once each, via atomic spawn reservations",
    )
    supervise_scope = p.add_mutually_exclusive_group()
    supervise_scope.add_argument("--repo", help="lane to supervise (default: the calling repo)")
    supervise_scope.add_argument("--all-repos", action="store_true", help="supervise every lane (no repo scope)")
    p.add_argument("--label", action="append", help="only spawn queued tasks carrying this label (repeatable; opt-in gate)")
    p.add_argument("--max-concurrent", "--max-active-processes", dest="max_concurrent", type=int, default=1, help="pool-local cap on live/launching worker processes (default: 1)")
    p.add_argument("--max-attempts", type=int, default=3, help="dead-letter a task after this many failed spawn attempts (default: 3; 0 = retry forever)")
    p.add_argument("--label-max-attempts", action="append", metavar="LABEL=N", help="per-label override of --max-attempts (repeatable), e.g. --label-max-attempts code-review=3 so raising one label's bound doesn't revive another label's stale tasks (N=0 = retry forever for that label)")
    p.add_argument("--no-heartbeat", action="store_true", help="don't hold the lease of confirmed-alive embodied workers")
    p.add_argument("--embody-backend", choices=["headless", "cli", "script"], default="headless")
    p.add_argument("--headless-label", action="append", metavar="LABEL")
    p.add_argument("--cli-label", action="append", metavar="LABEL")
    p.add_argument("--script-label", action="append", metavar="LABEL")
    p.add_argument("--disposable-cli-label", action="append", metavar="LABEL")
    p.add_argument(
        "--idle-nudge-exempt-label",
        action="append",
        metavar="LABEL",
        help="never send the generic idle-confirm nudge to a task carrying this label (repeatable) -- for a task type that owns its own resume path (an in-process evaluator, or an external one driven entirely through this CLI), an idle STARTED task with no new activity is its correct resting state, not an unfinished turn",
    )
    p.add_argument("--no-pair", action="store_true")
    p.add_argument("--headless-agent", default="task-worker", metavar="AGENT")
    p.add_argument("--charter", metavar="AGENT", help="optional .github/agents/<charter>.agent.md behavior overlay -- passed as Copilot's own --agent flag on the launched session, independent of the venue targeted by --headless-agent")
    p.add_argument("--verify-timeout", type=int, default=0)
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--no-reactive", action="store_true")
    p.add_argument("--reactive-interval", type=float, default=2.0)
    p.add_argument("--supervisor-id", help=argparse.SUPPRESS)
    p.add_argument("--once", action="store_true")
    p.add_argument("--pool")
    p.add_argument("--origin")
    p.add_argument("--headless", action="store_true")
    p.add_argument("--evaluator", metavar="SPEC")
    p.add_argument("--evaluator-ref")
    p.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    sup_sub = p.add_subparsers(dest="supervise_command")
    rp = sup_sub.add_parser("register", help="add a durable supervision registration and RETURN its handle (does not run the loop; the singleton supervisor runs it)")
    rp.add_argument("--kind", choices=sorted(RegistrationKind.DIRECT), default="supervised-lane")
    rp.add_argument("--id")
    rp.add_argument("--spec", metavar="JSON|@FILE")
    rp.add_argument("--machine")
    rp.add_argument("--env")
    rp.add_argument("--repo")
    rp.add_argument("--all-repos", action="store_true")
    rp.add_argument("--label", action="append")
    rp.add_argument("--max-concurrent", "--max-active-processes", dest="max_concurrent", type=int, default=1)
    rp.add_argument("--max-attempts", type=int, default=3)
    rp.add_argument("--label-max-attempts", action="append", metavar="LABEL=N")
    rp.add_argument("--embody-backend", choices=["headless", "cli", "script"], default="headless")
    rp.add_argument("--headless-label", action="append", metavar="LABEL")
    rp.add_argument("--cli-label", action="append", metavar="LABEL")
    rp.add_argument("--script-label", action="append", metavar="LABEL")
    rp.add_argument("--disposable-cli-label", action="append", metavar="LABEL")
    rp.add_argument("--idle-nudge-exempt-label", action="append", metavar="LABEL")
    rp.add_argument(
        "--steering-disallowed-label",
        action="append",
        metavar="LABEL",
        help="forbid a task carrying this label from posting a request_input steering card at all (repeatable) -- enforced coordinator-side; default is permissive, so name a label here only for a task type with no human to hand a card to (an evaluator-owned auto-reviewer, a batch log writer, an Adjudication Board worker)",
    )
    rp.add_argument("--headless-agent", metavar="AGENT")
    rp.add_argument("--charter", metavar="AGENT")
    rp.add_argument("--evaluator", metavar="SPEC")
    rp.add_argument("--evaluator-ref")
    rp.add_argument("--interval", type=float, default=30.0)
    rp.add_argument("--ensure", action="store_true")
    rp.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    rp = sup_sub.add_parser("status", help="query a registration by its handle")
    rp.add_argument("id")
    rp.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    rp = sup_sub.add_parser("list", help="list registrations")
    rp.add_argument("--kind", choices=sorted(RegistrationKind.ALL))
    rp.add_argument("--machine")
    rp.add_argument("--env")
    rp.add_argument("--active", action="store_true")
    rp.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    rp = sup_sub.add_parser("remove", help="remove a registration by its handle")
    rp.add_argument("id")
    rp.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    rp = sup_sub.add_parser("serve", help="run the singleton supervisor daemon (foreground)")
    rp.add_argument("--machine")
    rp.add_argument("--env")
    rp.add_argument("--interval", type=float, default=5.0)
    rp.add_argument("--once", action="store_true")
    rp.add_argument("--no-single-instance", action="store_true")
    rp.add_argument("--no-declared", action="store_true")
    rp.add_argument("--legacy-env", action="store_true")
    rp.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    rp = sup_sub.add_parser("daemon-status", help="show whether a supervisor daemon holds this (machine, env) scope and the registrations it would run")
    rp.add_argument("--machine")
    rp.add_argument("--env")
    rp.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    op = sup_sub.add_parser("override", help="operator kill-switch: locally disable/enable one supervised unit")
    op_sub = op.add_subparsers(dest="override_command")
    od = op_sub.add_parser("disable", help="disable a supervised unit now")
    od.add_argument("id")
    od.add_argument("--reason")
    od.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    oe = op_sub.add_parser("enable", help="clear a unit's override")
    oe.add_argument("id")
    oe.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    ol = op_sub.add_parser("list", help="list the current operator overrides")
    ol.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    op.set_defaults(func=_resolve_cli_module()._cmd_supervise)
    register_reservations_command(sub)
