"""Steering/card CLI commands extracted from ``__main__.py``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def _cmd_card_set(args: argparse.Namespace) -> int:
    """Attach a card to a task the worker owns (the human-in-the-loop 'I need
    input' post). Parses the ``--request-input`` form spec, builds the card, and
    posts it; a card with a form marks the task awaiting-steer."""
    from . import steering

    worker_id = _core()._resolve_owner(args, verb="card set")
    if worker_id is None:
        return 2
    try:
        fields = steering.parse_request_input(getattr(args, "request_input", None))
    except steering.SteeringError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    body = args.body
    if body and body.startswith("@"):
        body = Path(body[1:]).expanduser().read_text(encoding="utf-8")
    card = steering.build_card(
        title=args.title,
        status=args.status,
        link=args.link,
        body=body,
        request_input=fields,
    )
    with _core()._client(args) as c:
        return _core()._emit(c.set_card(args.task_id, worker_id, card=card))

def _cmd_card_show(args: argparse.Namespace) -> int:
    """Show a task's current card (plus its steer inbox and any saved draft)."""
    with _core()._client(args) as c:
        task = c.get(args.task_id)
        steers = c.steer_log(args.task_id)
    return _core()._emit(
        {
            "task_id": args.task_id,
            "card": task.get("card"),
            "awaiting_steer": task.get("awaiting_steer"),
            "card_draft": task.get("card_draft"),
            "steers": steers,
        }
    )

def _cmd_card_draft_save(args: argparse.Namespace) -> int:
    """Persist an operator's not-yet-submitted draft answer (--field k=v ...).
    Never touches awaiting_steer/status -- the task stays blocked exactly as
    before, and the draft is durably visible from any surface/machine (unlike
    a local sidecar file)."""
    fields: dict[str, str] = {}
    for item in args.field or []:
        key, sep, value = item.partition("=")
        if not sep:
            print(
                f"agent-dispatch: --field must be key=value (got {item!r})",
                file=sys.stderr,
            )
            return 2
        fields[key.strip()] = value
    with _core()._client(args) as c:
        return _core()._emit(c.save_card_draft(args.task_id, fields=fields))

def _cmd_card_draft_clear(args: argparse.Namespace) -> int:
    """Clear a task's saved draft. Never touches awaiting_steer/status."""
    with _core()._client(args) as c:
        return _core()._emit(c.clear_card_draft(args.task_id))

def _cmd_steer(args: argparse.Namespace) -> int:
    """Submit an answer and have the coordinator resume the owning worktree."""
    fields: dict[str, str] = {}
    for item in args.field or []:
        key, sep, value = item.partition("=")
        if not sep:
            print(
                f"agent-dispatch: --field must be key=value (got {item!r})",
                file=sys.stderr,
            )
            return 2
        fields[key.strip()] = value
    sender = args.sender or _core()._owner_from_identity(args)
    with _core()._client(args) as c:
        result = c.steer(
            args.task_id,
            fields=fields,
            sender=sender,
            wake=args.wake,
            message=args.message,
            expected_status=args.expected_status,
        )
        woken = result.pop("steer_woken", None)
        wake_status = result.pop("steer_wake_status", None)
        if args.wake and wake_status is None:
            wake_status = "unsupported"
    return _core()._emit({"task": result, "woken": woken, "wake_status": wake_status})

def _cmd_steer_take(args: argparse.Namespace) -> int:
    """Consume pending steering for a task the worker owns."""
    worker_id = _core()._resolve_owner(args, verb="steer take")
    if worker_id is None:
        return 2
    with _core()._client(args) as c:
        return _core()._emit(
            c.steer_take(
                args.task_id,
                worker_id,
                all_pending=getattr(args, "all_pending", False),
            )
        )

def register_steering_commands(sub) -> None:
    cp = sub.add_parser(
        "card",
        help="attach/show a task's card -- the glanceable brief a worker posts when it needs operator input",
    )
    csub = cp.add_subparsers(dest="card_cmd", required=True)
    cs = csub.add_parser(
        "set",
        help="attach a card to a held task you own (a form via --request-input marks it awaiting-steer); identity auto-resolved from CWD",
    )
    cs.add_argument("task_id")
    cs.add_argument("worker_id", nargs="?", help="owner id (default: composed from machine/worktree)")
    cs.add_argument("--title", help="short card title")
    cs.add_argument("--status", help="one-line status overview")
    cs.add_argument("--link", help="a link to the rich artifact (draft / PR)")
    cs.add_argument("--body", help="the scrollable card body (markdown); '@path' reads a file")
    cs.add_argument("--request-input", dest="request_input", help="form spec the operator should fill")
    cs.add_argument("--machine", help="override the resolved machine")
    cs.add_argument("--worktree", help="override the resolved worktree id")
    cs.set_defaults(func=_core()._cmd_card_set)

    ch = csub.add_parser("show", help="show a task's current card + its steer inbox")
    ch.add_argument("task_id")
    ch.set_defaults(func=_core()._cmd_card_show)

    cd = csub.add_parser(
        "draft",
        help="save/clear a not-yet-submitted operator draft answer, without submitting a steer (the task stays blocked)",
    )
    cdsub = cd.add_subparsers(dest="card_draft_cmd", required=True)
    cds = cdsub.add_parser("save", help="persist a draft answer (--field k=v ...); never touches awaiting_steer/status")
    cds.add_argument("task_id")
    cds.add_argument("--field", action="append", metavar="KEY=VALUE", help="one draft answer field (repeatable)")
    cds.set_defaults(func=_core()._cmd_card_draft_save)
    cdc = cdsub.add_parser("clear", help="clear a task's saved draft; never touches awaiting_steer/status")
    cdc.add_argument("task_id")
    cdc.set_defaults(func=_core()._cmd_card_draft_clear)

    sp = sub.add_parser(
        "steer",
        help="submit an operator's answer to a task's card, or (steer take) consume the next answer as the worker",
    )
    ssub = sp.add_subparsers(dest="steer_cmd", required=True)
    ssm = ssub.add_parser("submit", help="submit an operator answer (--field k=v ...) and wake the worker")
    ssm.add_argument("task_id")
    ssm.add_argument("--field", action="append", metavar="KEY=VALUE", help="one answer field (repeatable)")
    ssm.add_argument("--sender", help="who is answering (default: resolved identity)")
    ssm.add_argument("--message", help="override the wake nudge text")
    ssm.add_argument("--expected-status", dest="expected_status", help="reject if the task's current status doesn't match")
    ssm.add_argument("--no-wake", dest="wake", action="store_false", help="do not send an agent-bridge wake nudge")
    ssm.set_defaults(func=_core()._cmd_steer, wake=True)

    stk = ssub.add_parser("take", help="consume the next pending steer for a task you own (the wake-side read); identity auto-resolved from CWD")
    stk.add_argument("task_id")
    stk.add_argument("worker_id", nargs="?", help="owner id (default: composed from machine/worktree)")
    stk.add_argument("--machine", help="override the resolved machine")
    stk.add_argument("--worktree", help="override the resolved worktree id")
    stk.add_argument("--all", dest="all_pending", action="store_true", help="atomically consume every pending steer (required after a wake)")
    stk.set_defaults(func=_core()._cmd_steer_take)
