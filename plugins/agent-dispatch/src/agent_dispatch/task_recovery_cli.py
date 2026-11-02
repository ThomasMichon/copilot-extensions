"""Explicit, fenced recovery CLI commands and legacy reset compatibility."""

from __future__ import annotations

import argparse
import sys
from types import ModuleType

from .client_task_recovery import WAKE_OUTBOXES, strict_reset_request, validate_recovery_fields
from .loop_commands import _resolve_cli_module


def _core() -> ModuleType:
    return _resolve_cli_module()


def _recovery_flags(parser: argparse.ArgumentParser, *, request_id: bool = True) -> None:
    parser.add_argument(
        "--expected-revision", required=True, help="opaque revision from task-diagnostics"
    )
    if request_id:
        parser.add_argument(
            "--request-id", required=True, help="stable idempotency key for this action"
        )
    parser.add_argument(
        "--actor", required=True, help="explicit actor recorded in the recovery audit"
    )
    parser.add_argument("--reason", required=True, help="nonblank recovery reason")


def register_task_recovery_commands(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "task-diagnostics",
        allow_abbrev=False,
        help="read bounded task recovery evidence without repair",
    )
    parser.add_argument("task_id")
    parser.set_defaults(func=_core()._cmd_task_diagnostics)

    parser = sub.add_parser(
        "detach-agent",
        allow_abbrev=False,
        help="safely detach the exact agent assignment, not its target",
    )
    parser.add_argument("task_id")
    _recovery_flags(parser)
    parser.set_defaults(func=_core()._cmd_detach_agent)

    parser = sub.add_parser(
        "reconcile-wake",
        allow_abbrev=False,
        help="observe original wake send evidence without sending a prompt",
    )
    parser.add_argument("task_id")
    parser.add_argument("--outbox", choices=WAKE_OUTBOXES, required=True)
    parser.add_argument("--wake-id", required=True)
    _recovery_flags(parser, request_id=False)
    parser.set_defaults(func=_core()._cmd_reconcile_wake)


def _validated_fields(args: argparse.Namespace, *names: str) -> dict[str, str] | None:
    fields = {name: getattr(args, name, None) for name in names}
    try:
        return validate_recovery_fields(**fields)
    except ValueError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return None


def _cmd_task_diagnostics(args: argparse.Namespace) -> int:
    with _core()._client(args, ensure=False, read_only=True) as client:
        result = client.task_diagnostics(args.task_id)
    return _core()._emit(result)


def _cmd_detach_agent(args: argparse.Namespace) -> int:
    fields = _validated_fields(args, "expected_revision", "request_id", "actor", "reason")
    if fields is None:
        return 2
    with _core()._client(args, ensure=False, allow_ssh_failover=False) as client:
        result = client.recover_task(args.task_id, operation="detach_agent", **fields)
    return _core()._emit(result)


def _cmd_reset(args: argparse.Namespace) -> int:
    try:
        request = strict_reset_request(
            to=args.to,
            expected_revision=getattr(args, "expected_revision", None),
            request_id=getattr(args, "request_id", None),
            actor=getattr(args, "actor", None),
            reason=args.reason,
            expected_status=args.expected_status,
        )
    except ValueError as exc:
        print(f"agent-dispatch: {exc}", file=sys.stderr)
        return 2
    if request is None:
        with _core()._client(args) as client:
            result = client.reset(
                args.task_id,
                reason=args.reason,
                expected_status=args.expected_status,
            )
        return _core()._emit(result)
    operation, fields = request
    with _core()._client(args, ensure=False, allow_ssh_failover=False) as client:
        result = client.recover_task(args.task_id, operation=operation, **fields)
    return _core()._emit(result)


def _cmd_reconcile_wake(args: argparse.Namespace) -> int:
    fields = _validated_fields(args, "outbox", "wake_id", "expected_revision", "actor", "reason")
    if fields is None:
        return 2
    with _core()._client(args, ensure=False, allow_ssh_failover=False) as client:
        result = client.reconcile_wake(args.task_id, **fields)
    return _core()._emit(result)
