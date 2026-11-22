"""CLI for manual checkout dehydration: ``dehydrate``, ``rehydrate`` and ``hydration``."""

from __future__ import annotations

import argparse
import json
import subprocess
from typing import Any

from . import config as cfg
from . import output


def add_parsers(sub: Any) -> None:
    for name, help_text in (
        ("dehydrate", "Store a clean, inactive managed checkout and remove its files (manual)"),
        ("rehydrate", "Restore a dehydrated checkout at its original path on its recorded branch"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("worktree_id", nargs="?")
        if name == "dehydrate":
            p.add_argument("--plan", action="store_true", help="Check admission only; change nothing")
        p.add_argument("--capabilities", action="store_true")
        p.add_argument("--json", action="store_true")
    p = sub.add_parser("hydration", help="Reconcile interrupted dehydration or recover tracking from the catalog")
    effects = p.add_mutually_exclusive_group(required=False)
    effects.add_argument("--reconcile", metavar="WORKTREE_ID", help="Settle one interrupted transition")
    effects.add_argument("--recover", action="store_true", help="Report records reconstructable from the catalog")
    effects.add_argument(
        "--release-recovery-hold", metavar="WORKTREE_ID",
        help="Accept a recovered record once its relationships are reconciled",
    )
    p.add_argument("--apply", action="store_true", help="With --recover, write reconstructable held records")
    p.add_argument("--capabilities", action="store_true")
    p.add_argument("--json", action="store_true")


def _emit(args: argparse.Namespace, payload: dict[str, Any]) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True))
        return
    if "entries" in payload:
        for item in payload["entries"]:
            print(f"{item.get('worktree_id')}: {item.get('verdict')} {item.get('reason', '')}".rstrip())
        return
    state = payload.get("state") or ("admissible" if payload.get("admissible") else "?")
    detail = payload.get("reconciled") or payload.get("reason") or ""
    print(f"{payload.get('worktree_id')}: {state} {detail}".rstrip())


def cmd_hydration(args: argparse.Namespace) -> int:
    from yaml import YAMLError

    from . import checkout_hydration as hydration
    from . import git_ops

    if args.capabilities:
        print(json.dumps(hydration.capabilities(), sort_keys=True))
        return 0
    try:
        config = cfg.load_config()
        if args.command == "dehydrate":
            if not args.worktree_id:
                raise hydration.HydrationRefused("invalid-id", "dehydrate requires an exact worktree ID")
            result = (hydration.plan if args.plan else hydration.dehydrate)(args.worktree_id, config)
        elif args.command == "rehydrate":
            if not args.worktree_id:
                raise hydration.HydrationRefused("invalid-id", "rehydrate requires an exact worktree ID")
            result = hydration.rehydrate(args.worktree_id, config)
        elif args.reconcile:
            result = hydration.reconcile(args.reconcile, config)
        elif args.recover:
            result = hydration.recover(config, apply=args.apply)
        elif args.release_recovery_hold:
            result = hydration.release_recovery_hold(args.release_recovery_hold, config)
        else:
            raise hydration.HydrationRefused("usage", "hydration needs --reconcile, --recover or --release-recovery-hold")
        _emit(args, result)
        held = result.get("state") == "hold" or any(e.get("verdict") == "hold" for e in result.get("entries", []))
        return 2 if held else 0
    except hydration.HydrationRefused as exc:
        payload = {"schema": hydration.SCHEMA, "complete": False, **exc.to_dict()}
    except (ValueError, OSError, RuntimeError, git_ops.GitError, YAMLError, subprocess.TimeoutExpired) as exc:
        payload = {"schema": hydration.SCHEMA, "complete": False, "code": "error", "error": str(exc)}
    if getattr(args, "json", False):
        print(json.dumps(payload, sort_keys=True, default=str))
    else:
        output.err(f"{args.command} refused ({payload['code']}): {payload['error']}")
    return 1
