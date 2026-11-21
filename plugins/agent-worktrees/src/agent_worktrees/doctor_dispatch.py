"""Targeted doctor routing before ordinary project-wide maintenance."""

from __future__ import annotations

import argparse


def dispatch_targeted(args: argparse.Namespace) -> int | None:
    """Return a selected scope's status, or None for ordinary maintenance."""
    if getattr(args, "agenda", None) is not None or any(
        getattr(args, "agenda_" + field, None) is not None
        for field in ("source_home", "export_home", "lane", "store_id", "operation_id", "expected_revision")
    ):
        from .agenda_doctor import targeted_doctor
        return targeted_doctor(args)
    if getattr(args, "repair_orphaned_owner", None) is not None:
        from .orphaned_owner_repair import targeted_doctor
        return targeted_doctor(args)
    if getattr(args, "recovery_report", None) is not None:
        from .recovery_doctor import targeted_report
        return targeted_report(args)
    if (
        getattr(args, "repair_machine_record", None) is not None
        or getattr(args, "from_machine", None) is not None
        or getattr(args, "to_machine", None) is not None
    ):
        from .machine_record_repair import targeted_doctor
        return targeted_doctor(args)
    if getattr(args, "guest_distribution", None) is not None:
        from .guest_environment_cli import targeted_doctor
        return targeted_doctor(args)
    if getattr(args, "repair_creation", None) is not None or getattr(args, "seed_id", None):
        from .creation_tail_repair import targeted_doctor
        return targeted_doctor(args)
    if getattr(args, "repair_record", None) is not None or getattr(args, "bound_agent_end_field", None):
        from .tracking_yaml_repair import targeted_doctor
        return targeted_doctor(args)
    return None
