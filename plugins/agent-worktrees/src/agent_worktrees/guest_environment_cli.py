"""Targeted guest diagnostics; installation-context owns identity and enrollment."""

from __future__ import annotations

import json
from argparse import Namespace
from typing import Any

from . import _installation_context as primitive
from . import output


def targeted_doctor(args: Namespace) -> int:
    """Run guest preflight without entering general doctor repair machinery."""
    apply = getattr(args, "fix", False)
    report: dict[str, Any] = {"version": 1, "scope": "guest-environment", "apply": apply}
    try:
        if any(getattr(args, name, None) for name in (
            "repair_creation", "seed_id", "repair_record", "bound_agent_end_field",
            "gc_sessions", "prune_pivots", "apply_daemon_health",
        )):
            raise ValueError(
                "--guest-distribution is a targeted diagnostic and cannot be "
                "combined with other doctor repair selectors."
            )
        status = primitive.inspect_guest_environment(distribution=args.guest_distribution)
        report["guest_environment"] = status
        if status["status"] != "ready":
            raise ValueError(
                "guest-default-" + status["status"] + ": the designated WSL "
                "distribution is not verified as the host default. No default "
                "or registration was changed."
            )
        if status["registrationState"] == "unregistered":
            if apply:
                report["registration"] = primitive.register_guest_environment(
                    args.guest_distribution,
                )
                report["guest_environment"] = primitive.inspect_guest_environment(
                    distribution=args.guest_distribution,
                )
                verified = report["guest_environment"]
                if verified["status"] != "ready" or verified["registrationState"] != "registered":
                    raise ValueError("guest-identity-unverified: enrollment did not verify as registered.")
            else:
                report["status"] = "registration-required"
                report["detail"] = (
                    "Run doctor --guest-distribution <name> --fix through the "
                    "matching named direct WSL entry to enable SSH identity."
                )
        report.setdefault("status", "ready")
    except (primitive.InstallationContextError, OSError, ValueError) as error:
        report["status"] = "blocked"
        report["detail"] = str(error)

    if getattr(args, "json", False):
        print(json.dumps(report, indent=2))
    elif report["status"] == "ready":
        output.ok("Guest identity/default preflight ready; host default unchanged.")
    else:
        output.err(str(report["detail"]))
    return 0 if report["status"] == "ready" else 1
