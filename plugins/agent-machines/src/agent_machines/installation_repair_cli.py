"""CLI boundary for explicit installation repair and its confirmations."""

from __future__ import annotations

import argparse
import json
import sys


def command(args: argparse.Namespace) -> int:
    from .installation_repair import repair
    from .installation_repair_admission import RepairAdmissionError
    from .installation_repair_process import caller_owned_readiness

    try:
        enclosing = getattr(args, "caller_owned_readiness", False)
        if enclosing and (
            not args.required or args.apply or args.confirm_restart or args.expected_plan_digest
            or getattr(args, "confirm_cleanup", None)
        ):
            raise RepairAdmissionError("caller-owned readiness requires read-only required selection")
        with caller_owned_readiness(enclosing):
            result = repair(
                args.project_root, modules=args.module, apply=args.apply,
                required=args.required,
                refresh=getattr(args, "refresh", False),
                expected_plan_digest=args.expected_plan_digest,
                readiness_timeout=args.readiness_timeout, installer_timeout=args.installer_timeout,
                confirm_restart=args.confirm_restart,
                confirm_cleanup=getattr(args, "confirm_cleanup", None),
                readiness_only=enclosing,
            )
        result["callerOwnedReadiness"] = enclosing
    except (RepairAdmissionError, ValueError) as error:
        print(f"agent-machines: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    if result["interrupted"]:
        return 130
    return 0 if result["status"] in {
        "discovered", "observed", "ready", "restart-confirmed", "cleanup-confirmed",
    } else 2
