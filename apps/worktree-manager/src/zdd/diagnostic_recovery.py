"""Native recovery custody for the generic diagnostics apply surface."""

from __future__ import annotations

import logging
import time
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

from . import breadcrumb, publication_history, routing
from .cutover_lock import CutoverBorrowError, CutoverLock, CutoverLockedError, lock_path

if TYPE_CHECKING:
    from .diagnostics import DiagnosticContext

log = logging.getLogger("zdd")


class IdentityTerminator(Protocol):
    def __call__(
        self, pid: int, expected_start_time: str | None, *,
        exit_timeout: float | None = None,
    ) -> dict: ...


def terminate_unpromoted(ctx: DiagnosticContext, pid: int, start_time: str | None) -> dict:
    """Generic diagnostics cannot discard a generation that may have served."""
    try:
        with routing.routing_lock(ctx.config_dir, deadline=time.monotonic() + 1.0):
            table = routing.read_table(ctx.config_dir, strict=True) or {}
            record = breadcrumb.read_breadcrumb(ctx.config_dir) or {}
            if publication_history.candidate_never_published(
                table, record, pid, start_time,
            ):
                result = ctx.terminate_pid_if_identity(pid, start_time, exit_timeout=5.0)
                if not isinstance(result, dict):
                    raise ValueError("native terminator returned invalid exit evidence")
                if all(result.get(key) is True for key in (
                    "killed", "identity_verified", "exit_confirmed",
                )):
                    return result
                reason = "native candidate exit unconfirmed; retirement refused"
                log.error(reason)
                return {
                    "killed": False, "blocked": True, "reason": reason,
                    "identity_verified": result.get("identity_verified") is True,
                    "exit_confirmed": result.get("exit_confirmed") is True,
                    "signal_accepted": result.get("killed") is True,
                }
            reason = "publication custody unverified; native owner retirement required"
    except (OSError, ValueError, TypeError) as exc:
        reason = f"native retirement authority unavailable ({type(exc).__name__}); retirement refused"
    log.error(reason)
    return {
        "killed": False, "identity_verified": False, "exit_confirmed": False,
        "blocked": True, "reason": reason,
    }


def mark_custody_refusals(findings: list[dict], table: dict, record: dict) -> None:
    for finding in findings:
        if finding.get("kind") not in {
            "abandoned_passive", "superseded_generation", "duplicate_resident",
        }:
            continue
        for target in finding.get("targets", []):
            if not publication_history.candidate_never_published(
                table, record, target.get("pid"), target.get("start_time"),
            ):
                finding["repairable"] = False
                finding["blocked_reason"] = (
                    "publication custody unverified; native owner retirement required"
                )
                break


def conditional_recovery_report(ctx: DiagnosticContext) -> dict | None:
    record = breadcrumb.read_breadcrumb(ctx.config_dir)
    reason = breadcrumb.recovery_refusal(ctx.config_dir, record)
    if reason is None:
        return None
    from .diagnostics import _candidate_map

    inventory_error = None
    try:
        candidates = _candidate_map(ctx.list_candidates())
    except Exception as exc:
        candidates = {}
        inventory_error = type(exc).__name__
        log.error("Conditional recovery inventory unavailable (%s)", inventory_error)
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
    release_consumer = not isinstance(consumer, CutoverLock)
    try:
        with ExitStack() as authority:
            if isinstance(consumer, CutoverLock):
                try:
                    authority.enter_context(consumer.borrow())
                except CutoverBorrowError as exc:
                    reason = f"native cutover borrow refused ({type(exc).__name__})"
                    log.error(reason)
                    report = _audit_daemon_health(ctx, now=now, cutover_state="busy")
                    return _blocked(ctx, report, "cutover_guard", reason)
                release_consumer = True
            if not (
                isinstance(consumer, CutoverLock)
                and consumer.path.resolve() == lock_path(ctx.config_dir).resolve()
            ):
                try:
                    authority.enter_context(CutoverLock(ctx.config_dir))
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
        if release_consumer and consumer is not None and hasattr(consumer, "release"):
            consumer.release()
