"""Async daemon-health backstop after a status-monitor cutover/restart
attempt (copilot-extensions#5453 interim mitigation).

Kept as its own small module (not folded into ``status_monitor_runtime``)
to stay well under this repo's 1000-line module-size cap
(``tools/check-module-size.py``) -- that module was already at its ceiling.

#5453 diagnosed why a hard, blocking exclusivity claim cannot be safely
added at the cutover/promotion transition: the predecessor's self-retire
loop needs two confirmations 15s apart (30s+) before it actually exits, so
any bounded wait short enough to avoid stalling an ordinary cutover is too
short to reliably outlast it. A cutover that times out or rolls back
ambiguously can therefore leave either a stray duplicate daemon or zero
live daemons, and nothing reconverges on exactly one owner without a human
running ``doctor --apply-daemon-health`` by hand.

This module closes that gap WITHOUT touching the hard-lease/cold-start
exclusivity logic (PR #5412) or the promotion/drain sequencing itself
(#5453's own tracked scope for the real fix): it schedules the EXISTING
identity-verified ``daemon_health.doctor_report(apply=True)`` repair to run
automatically, on a delay, after every cutover/restart attempt.
"""

from __future__ import annotations

import argparse
import sys
import time

# Long enough to reliably outlast the predecessor's own self-retire debounce
# (2 confirmations 15s apart -- 30s minimum, copilot-extensions#5453) with
# margin for a slow drain/rollback, short enough that an ambiguous
# post-cutover state (duplicate or zero live monitors) self-heals within
# a couple of minutes without a human needing to notice and run
# `doctor --apply-daemon-health` by hand.
DEFAULT_DELAY_SECONDS = 120.0


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "status-monitor-reap-stale",
        help="Wait, then apply the identity-verified resident daemon-health "
        "repair (the same one 'doctor --apply-daemon-health' runs by hand) "
        "-- an async, non-blocking backstop the installer fires after a "
        "cutover/restart attempt, so an ambiguous post-rollback state "
        "(copilot-extensions#5453) self-heals without needing a human to "
        "notice and run doctor manually.",
    )
    p.add_argument(
        "--delay-seconds", type=float, default=DEFAULT_DELAY_SECONDS,
        help="Seconds to wait before applying the repair (default: "
        f"{DEFAULT_DELAY_SECONDS:g}, long enough to outlast an "
        "ordinary self-retire window without blocking the installer).",
    )


def schedule_delayed_daemon_health_reap(
    delay_seconds: float = DEFAULT_DELAY_SECONDS,
) -> bool:
    """Fire-and-forget async backstop for an ambiguous post-cutover state:
    spawns a detached process that waits ``delay_seconds`` then applies the
    SAME identity-verified ``daemon_health.doctor_report(apply=True)``
    repair ``doctor --apply-daemon-health`` already runs safely by hand --
    never blocks the caller, never raises, and is safe to call
    unconditionally after any cutover/restart attempt (a clean single
    owner is simply a no-op finding for the repair to apply)."""
    try:
        from . import status_monitor_runtime as smr
        return smr._spawn_detached([
            sys.executable, "-m", "agent_worktrees", "status-monitor-reap-stale",
            "--delay-seconds", str(delay_seconds),
        ])
    except Exception:
        return False


def cmd_status_monitor_reap_stale(args: argparse.Namespace) -> int:
    """``status-monitor-reap-stale`` -- wait, then apply the identity-verified
    daemon-health repair. Spawned detached by
    :func:`schedule_delayed_daemon_health_reap`; not meant to be run
    interactively. Always exits 0 (advisory, best-effort)."""
    delay = max(0.0, float(getattr(args, "delay_seconds", DEFAULT_DELAY_SECONDS)))
    time.sleep(delay)
    try:
        from . import daemon_health
        report = daemon_health.doctor_report(apply=True)
    except Exception as exc:
        print(f"status-monitor-reap-stale: repair attempt failed (non-fatal): {exc}")
        return 0
    findings = report.get("findings") if isinstance(report, dict) else None
    count = len(findings) if isinstance(findings, list) else 0
    print(f"status-monitor-reap-stale: applied daemon-health repair ({count} finding(s))")
    return 0
