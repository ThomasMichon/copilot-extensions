"""Preview or explicitly roll back one recorded user-global Git transaction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import git_config_transaction as transaction


def plan_sha(value: str) -> str:
    if not transaction._SHA.fullmatch(value):
        raise argparse.ArgumentTypeError("expected a lowercase SHA-256 plan digest")
    return value


def print_plan(plan: dict) -> None:
    print(f"  Git configuration plan {plan['plan_sha256']}")
    if plan["diff"]:
        print(plan["diff"], end="" if plan["diff"].endswith("\n") else "\n")


def rollback(args: argparse.Namespace) -> int:
    home = Path.home()
    root = home / ".agent-machines" / "backups" / "git-config"
    try:
        receipt = args.receipt.absolute()
        if receipt.parent.parent.resolve() != root.resolve() or receipt.is_symlink():
            raise transaction.TransactionError(
                "receipt must belong to the current user's Git backups"
            )
        plan = transaction.rollback_preview(home / ".gitconfig", receipt)
        payload = {"plan": plan.summary(), "changed": False, "dry_run": not args.apply}
        if args.apply:
            backup = transaction.apply(
                plan, expected_plan_sha256=args.plan_sha or "", backup_root=root
            )
            payload.update(changed=plan.changed, backup_path=str(backup) if backup else None)
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print_plan(plan.summary())
            print(
                "Rollback applied."
                if args.apply
                else "Preview only; --apply --plan-sha is required."
            )
        return 0
    except (transaction.TransactionError, OSError, ValueError) as exc:
        changed = getattr(exc, "changed", False)
        receipt = getattr(exc, "receipt", None)
        if args.json:
            print(
                json.dumps(
                    {
                        "status": "error" if changed else "blocked",
                        "changed": changed,
                        "backup_path": str(receipt) if receipt is not None else None,
                    }
                )
            )
        if changed:
            print(f"Git configuration rollback changed the file but failed: {exc}", file=sys.stderr)
            if receipt is not None:
                print(f"Recovery receipt: {receipt}", file=sys.stderr)
        else:
            print(f"Git configuration rollback refused: {exc}", file=sys.stderr)
        return 2


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "git-config", help="Manage explicit Git configuration rollback."
    )
    commands = parser.add_subparsers(dest="git_config_command", required=True)
    restore = commands.add_parser(
        "restore", help="Preview or apply only the declared Git resource, never modules or surfaces."
    )
    restore.add_argument("--repo")
    restore.add_argument("--machine")
    restore.add_argument("--json", action="store_true")
    restore.add_argument("--apply", action="store_true")
    restore.add_argument("--git-config-plan-sha", type=plan_sha)
    restore.set_defaults(
        func=restore_declared, all_projects=False, only=None, maintenance_safe=False,
        verbose=False, resource_types=frozenset({"git-config"}),
    )
    undo = commands.add_parser(
        "rollback", help="Preview rollback of one recorded user-global transaction."
    )
    undo.add_argument("--receipt", required=True, type=Path)
    undo.add_argument("--apply", action="store_true")
    undo.add_argument("--plan-sha", type=plan_sha)
    undo.add_argument("--json", action="store_true")
    undo.set_defaults(func=rollback)


def restore_declared(args: argparse.Namespace) -> int:
    from .__main__ import _cmd_restore

    return _cmd_restore(args)
