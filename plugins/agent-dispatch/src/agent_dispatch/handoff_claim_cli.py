"""Explicit targeted creator-claim retry; no task lifecycle mutation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def register_commands(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Register one owner-confirming operation with explicit legacy qualification."""
    parser = sub.add_parser(
        "handoff-claim", allow_abbrev=False,
        help="record or reconcile one exact handoff creator claim (never re-abandons)",
    )
    parser.add_argument("task_id")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--binding-file", help="binding JSON returned by successful native claim journaling")
    mode.add_argument("--owner-ref", help="explicit machine/project/worktree for an unbound legacy task")
    parser.set_defaults(func=_cmd_handoff_claim)


def _cmd_handoff_claim(args: argparse.Namespace) -> int:
    from . import __main__ as core

    binding = None
    if args.binding_file:
        binding = json.loads(Path(args.binding_file).read_text(encoding="utf-8"))
    with core._client(args, ensure=False, allow_ssh_failover=False) as client:
        peer = client.get(args.task_id)
        if peer.get("handoff_claim_contract") != "context-handoff-creator-claim/v1":
            raise ValueError("coordinator does not support exact creator-claim reconciliation")
        result = client.reconcile_handoff_claim(
            peer["id"], binding=binding, owner_ref=args.owner_ref,
        )
    return core._emit(result)
