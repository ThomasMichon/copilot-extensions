"""Shared ``zdd`` daemon-health wiring for the resident status-monitor."""

from __future__ import annotations

from zdd import diagnostics

from . import locks, procs, self_retire, status_monitor_cutover, status_monitor_runtime


def _candidates() -> list[diagnostics.DaemonCandidate]:
    return [
        diagnostics.DaemonCandidate(
            pid=pid,
            start_time=locks.process_start_time(pid),
        )
        for pid in sorted(status_monitor_cutover._iter_status_monitor_pids())
    ]


def _context() -> diagnostics.DiagnosticContext:
    return diagnostics.DiagnosticContext(
        service="agent-worktrees status-monitor",
        config_dir=status_monitor_cutover.routing_dir(),
        read_lock=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
        lock_is_live=locks.lock_is_live,
        list_candidates=_candidates,
        is_superseded=lambda pid, generation: self_retire.is_superseded(
            status_monitor_cutover.routing_dir(), pid, generation
        ),
        terminate_pid_if_identity=procs.terminate_pid_if_identity,
        make_client=status_monitor_cutover._make_client,
        health_check=status_monitor_cutover._health_check,
        abandoned_passive_grace_seconds=0.0,
    )


def doctor_report(*, apply: bool) -> dict[str, object]:
    ctx = _context()
    if apply:
        return diagnostics.apply_daemon_health(ctx)
    return diagnostics.audit_daemon_health(ctx)
