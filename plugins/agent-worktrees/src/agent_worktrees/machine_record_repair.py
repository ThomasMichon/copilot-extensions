"""Read-only compatibility notice for retired machine-label repair."""

from __future__ import annotations

import json
import re
from argparse import Namespace

from . import config as cfg, output


def targeted_doctor(args: Namespace) -> int:
    """Accept legacy selectors without reading, relabeling or migrating records."""
    record_id = getattr(args, "repair_machine_record", None)
    previous = getattr(args, "from_machine", None)
    apply = getattr(args, "fix", False)
    report = {
        "version": 1, "scope": "machine-record", "worktree_id": record_id,
        "mode": "fix" if apply else "report", "repairable": False,
        "repaired": False, "error": None, "resident_projection": "not-verified",
        "retired": True,
        "reason": "Canonical record naming is derived live; machine-label repair no longer writes state.",
    }
    try:
        if (
            not isinstance(record_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record_id)
            or ".." in record_id
            or not isinstance(previous, str) or not previous.strip()
        ):
            raise ValueError("repair requires an exact worktree ID and --from-machine")
        if any(getattr(args, field, None) for field in (
            "guest_distribution", "repair_creation", "seed_id", "repair_record",
            "bound_agent_end_field", "gc_sessions", "prune_pivots", "apply_daemon_health",
        )):
            raise ValueError("machine-record repair cannot be combined with other doctor passes")
        project = cfg.project_name()
        if not project:
            raise ValueError("machine-record repair requires an explicitly selected project")
        report["project"] = project
    except (ValueError, RuntimeError) as exc:
        report["error"] = str(exc)
    if getattr(args, "json", False):
        print(json.dumps(report))
    elif report["error"]:
        output.err(report["error"])
    else:
        output.ok(report["reason"])
    return 1 if report["error"] else 0
