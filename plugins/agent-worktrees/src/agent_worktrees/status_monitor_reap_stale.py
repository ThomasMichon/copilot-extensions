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


def _finite_non_negative_seconds(value: str) -> float:
    """``argparse`` ``type=`` validator for ``--delay-seconds``: rejects
    non-finite (``inf``/``nan``) and negative values at parse time, rather
    than reaching ``time.sleep()`` with one -- ``time.sleep(float("inf"))``
    raises ``OverflowError`` before this command's own try/except, breaking
    its "always exits 0" contract."""
    parsed = float(value)
    if parsed != parsed or parsed in (float("inf"), float("-inf")) or parsed < 0:
        raise argparse.ArgumentTypeError(
            f"must be a finite, non-negative number of seconds: {value!r}"
        )
    return parsed


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
        "--delay-seconds", type=_finite_non_negative_seconds, default=DEFAULT_DELAY_SECONDS,
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


def _ensure_monitor_if_zero_candidates_under_cutover_guard() -> str:
    """Re-check live candidate state and ensure a monitor exists, ALL while
    holding the cutover lock -- never from a stale report already released
    by the time this runs (review finding: a prior design checked
    ``apply_daemon_health()``'s returned report, but that call acquires and
    releases its OWN cutover guard internally before returning, leaving a
    real TOCTOU window in which a genuine concurrent cutover could start
    between the check and the spawn, racing a successor being promoted).

    Acquiring this SAME lock `activate_after_update()` itself acquires
    before running `CutoverOrchestrator.run()` makes the two mutually
    exclusive: while held here, no concurrent cutover attempt can be
    in-flight, so re-checking liveness and ensuring a monitor exist as one
    atomic unit is safe. A non-blocking acquire attempt (``timeout_s=0``)
    means a genuinely in-progress cutover is simply left alone -- it already
    owns ensuring a monitor exists once it finishes.

    Returns one of: ``"ensured"`` (spawned or confirmed live),
    ``"skipped-live"`` (a candidate already existed once re-checked),
    ``"skipped-cutover-busy"`` (another cutover holds the lock), or
    ``"error:<detail>"`` (best-effort, never raises).
    """
    from . import status_monitor_cutover as smc
    from . import status_monitor_runtime as smr
    from single_instance_lease import AlreadyRunningError

    try:
        lease = smc._acquire_cutover_lock(smc.routing_dir(), timeout_s=0.0)
    except AlreadyRunningError:
        return "skipped-cutover-busy"
    except Exception as exc:
        return f"error:{exc}"
    try:
        # NOT daemon_health.doctor_report()/apply_daemon_health() here: both
        # try to acquire this SAME cutover guard themselves (non-blocking,
        # per-call), which would self-block/no-op against the lease this
        # function is already holding. _candidates() is the lower-level,
        # lock-free liveness probe apply_daemon_health() itself audits with
        # once it holds the guard -- safe to call directly while already
        # holding it.
        from . import daemon_health
        candidates = daemon_health._candidates()
        if not candidates:
            smr._ensure_status_monitor()
            return "ensured"
        return "skipped-live"
    except Exception as exc:
        return f"error:{exc}"
    finally:
        lease.release()


def cmd_status_monitor_reap_stale(args: argparse.Namespace) -> int:
    """``status-monitor-reap-stale`` -- wait, then apply the identity-verified
    daemon-health repair, and ensure a monitor is actually running
    afterward. Spawned detached by
    :func:`schedule_delayed_daemon_health_reap`; not meant to be run
    interactively. Always exits 0 (advisory, best-effort).

    ``--delay-seconds`` is already validated finite/non-negative by argparse
    (:func:`_finite_non_negative_seconds`); the fallback here only guards a
    direct, non-argparse call (e.g. a test constructing ``Namespace`` by
    hand) against the same ``OverflowError``/hang a raw ``inf``/``nan``
    would otherwise cause in ``time.sleep()`` below.
    """
    delay = float(getattr(args, "delay_seconds", DEFAULT_DELAY_SECONDS))
    if delay != delay or delay in (float("inf"), float("-inf")) or delay < 0:
        delay = DEFAULT_DELAY_SECONDS
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

    # The repair above only audits/terminates EXISTING live candidates -- a
    # rollback that left ZERO live monitors (the other documented ambiguous
    # outcome, copilot-extensions#5453) produces zero findings and stays
    # down forever otherwise. Re-check and ensure under the cutover lock
    # (see the helper's own docstring for why this must be one atomic unit,
    # not a check against this already-stale report).
    outcome = _ensure_monitor_if_zero_candidates_under_cutover_guard()
    if outcome == "ensured":
        print("status-monitor-reap-stale: no live monitor after repair -- ensured")
    elif outcome.startswith("error:"):
        print(f"status-monitor-reap-stale: ensure-monitor attempt failed (non-fatal): {outcome[6:]}")
    return 0

