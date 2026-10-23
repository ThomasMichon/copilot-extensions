"""An exact-input, report-only branch of the existing doctor consumer."""

from __future__ import annotations

import json
from pathlib import Path

from .recovery_facts import MAX_BYTES, RecoveryError
from .recovery_inventory import empty_report, finding, inventory, parse_request, read_bounded

_INCOMPATIBLE = (
    "fix", "gc_sessions", "prune_pivots", "apply_daemon_health",
    "guest_distribution", "repair_machine_record", "from_machine", "to_machine",
    "repair_record", "bound_agent_end_field", "repair_creation", "seed_id",
)


def targeted_report(args) -> int:
    report = empty_report()
    try:
        if any(getattr(args, field, None) for field in _INCOMPATIBLE):
            raise RecoveryError("report_only_option_conflict")
        path = Path(args.recovery_report)
        if ".." in path.parts:
            raise RecoveryError("invalid_root")
        request = parse_request(read_bounded(path.absolute(), MAX_BYTES))
        report = inventory(request)
    except RecoveryError as exc:
        report["error"] = exc.reason
        report["outcome"] = finding(exc.reason)["status"]
    if getattr(args, "json", False):
        print(json.dumps(report, sort_keys=True))
    else:
        print(
            f"Recovery report: {report['outcome']}; migration=unknown; "
            f"effects=not-admitted; facts={report['facts_checked']}; "
            f"omitted={report['facts_omitted']}"
        )
        if report.get("error"):
            print(f"Reason: {report['error']}")
    # A readable report may be partial. Exit 0 is not readiness or admission.
    return 1 if report.get("error") else 0
