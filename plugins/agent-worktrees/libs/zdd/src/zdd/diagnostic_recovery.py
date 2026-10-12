"""Native recovery custody for the generic diagnostics apply surface."""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING

from . import breadcrumb
from .cutover_lock import CutoverLock, CutoverLockedError, lock_path

if TYPE_CHECKING:
    from .diagnostics import DiagnosticContext

log = logging.getLogger("zdd")


def conditional_recovery_report(ctx: DiagnosticContext) -> dict | None:
    record = breadcrumb.read_breadcrumb(ctx.config_dir)
    unavailable = (
        record is None and breadcrumb.breadcrumb_path(ctx.config_dir).exists()
    ) or (
        record is not None and record.get("state") not in {
            "started", "flipped", "draining", "committed", "rolled_back", "failed",
        } and not breadcrumb.requires_conditional_recovery(record)
    )
    if not unavailable and not breadcrumb.requires_conditional_recovery(record):
        return None
    from .diagnostics import _candidate_map

    inventory_error = None
    try:
        candidates = _candidate_map(ctx.list_candidates())
    except Exception as exc:
        candidates = {}
        inventory_error = type(exc).__name__
        log.error("Conditional recovery inventory unavailable (%s)", inventory_error)
    reason = (
        "recovery state cannot be verified; owner-led recovery required" if unavailable
        else "conditional cutover requires owner-led recovery"
    )
    finding = {
        "kind": "conditional_recovery", "repairable": False,
        "reason": reason, "targets": [],
    }
    return {
        "service": ctx.service, "mode": "report", "validated_owner": None,
        "owner_validation_reason": finding["reason"], "cutover_in_progress": False,
        "cutover_guard_error": None,
        "candidate_count": len(candidates) if inventory_error is None else None,
        "candidate_inventory_error": inventory_error,
        "candidates": [candidate.to_dict() for _, candidate in sorted(candidates.items())],
        "findings": [finding], "counts": {"total": 1, "conditional_recovery": 1},
    }


def _blocked(ctx: DiagnosticContext, report: dict, kind: str, reason: str) -> dict:
    return {
        "service": ctx.service, "mode": "apply", "before": report, "after": report,
        "findings": report["findings"], "remaining_findings": report["findings"],
        "counts": report["counts"],
        "actions": [{"kind": kind, "blocked": True, "reason": reason}],
    }


def guarded_apply(ctx: DiagnosticContext, *, now: datetime | None = None) -> dict:
    from .diagnostics import _apply_daemon_health_locked, _audit_daemon_health, _try_acquire_cutover_guard

    consumer, error = _try_acquire_cutover_guard(ctx)
    if ctx.acquire_cutover_guard is not None and consumer is None:
        report = _audit_daemon_health(ctx, now=now, cutover_state="busy")
        return _blocked(ctx, report, "cutover_guard", error or "cutover in progress")
    native = None
    try:
        borrowed = (
            isinstance(consumer, CutoverLock)
            and consumer.path.resolve() == lock_path(ctx.config_dir).resolve()
            and consumer._fh is not None
        )
        if not borrowed:
            native = CutoverLock(ctx.config_dir)
            try:
                native.acquire(timeout=0)
            except (CutoverLockedError, OSError) as exc:
                reason = f"native cutover authority unavailable ({type(exc).__name__})"
                log.error(reason)
                report = _audit_daemon_health(ctx, now=now, cutover_state="busy")
                return _blocked(ctx, report, "cutover_guard", reason)
        report = conditional_recovery_report(ctx)
        if report is not None:
            log.error("Diagnostics refuses conditional state requiring owner-led recovery")
            return _blocked(ctx, report, "conditional_recovery", report["owner_validation_reason"])
        return _apply_daemon_health_locked(
            replace(ctx, acquire_cutover_guard=None), now=now,
        )
    finally:
        try:
            if native is not None:
                native.release()
        finally:
            if consumer is not None and hasattr(consumer, "release"):
                consumer.release()
