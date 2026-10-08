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

Platform scope: identity-bound termination of a duplicate daemon is
unsupported on macOS (``daemon_health._context()`` sets
``repair_supported=False`` there, a PRE-EXISTING, broader
``zdd.diagnostics``/``daemon_health`` limitation this module does not
introduce and does not attempt to lift). On Darwin, the duplicate-monitor
half of this backstop is consequently a no-op (the repair call reports a
blocked finding rather than terminating anything); the zero-candidate
ensure-a-monitor half of this module is unaffected, since
``_ensure_status_monitor()`` never depends on identity-bound termination.
Windows and Linux get the full mitigation described above.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

# Long enough to reliably outlast the predecessor's own self-retire debounce
# (2 confirmations 15s apart -- 30s minimum, copilot-extensions#5453) with
# margin for a slow drain/rollback, short enough that an ambiguous
# post-cutover state (duplicate or zero live monitors) self-heals within
# a couple of minutes without a human needing to notice and run
# `doctor --apply-daemon-health` by hand.
DEFAULT_DELAY_SECONDS = 120.0

# An upper bound on an otherwise-finite, otherwise-valid --delay-seconds:
# time.sleep() raises OverflowError for a huge-but-finite float (e.g.
# 1e308) on supported platforms, which the finite/non-negative check alone
# does not catch. This command exists to apply a repair shortly after a
# cutover/restart attempt, never to wait indefinitely -- an hour is already
# far beyond any legitimate use.
MAX_DELAY_SECONDS = 3600.0


def _slot_marker_valid(slot: Path, version: str) -> bool:
    """Whether ``slot`` carries a well-formed ``.install-complete.json``
    marker naming exactly ``version`` -- mirrors the canonical schema
    ``scripts/versioned_runtime.py``'s own ``validate_marker()`` enforces
    (required: ``version``/``completed_at``/``pid``; optional:
    ``payload_hash``; no other keys; no duplicate JSON fields) rather than
    accepting any JSON object with a matching ``version`` field, which
    could let a truncated or malformed marker make an incomplete slot
    trusted. Not import-shared with that module directly: ``scripts/`` is
    a build/install-time tree, not part of the installed runtime package,
    so it cannot be relied on to be importable from here at agent
    runtime. A stale or hand-edited ``current-version``/``last-known-good``
    pointer, or a slot directory mid-install with no completion marker
    yet, must not be trusted as a complete, startable runtime."""
    try:
        import json

        def _unique_object(pairs):
            out: dict = {}
            for key, value in pairs:
                if key in out:
                    raise ValueError(f"duplicate JSON field: {key}")
                out[key] = value
            return out

        marker = json.loads(
            (slot / ".install-complete.json").read_text("utf-8"),
            object_pairs_hook=_unique_object,
        )
    except Exception:
        return False
    if not isinstance(marker, dict):
        return False
    allowed = {"version", "completed_at", "pid", "payload_hash"}
    required = {"version", "completed_at", "pid"}
    if not required.issubset(marker) or not set(marker).issubset(allowed):
        return False
    if not isinstance(marker["version"], str) or marker["version"] != version:
        return False
    if not isinstance(marker["completed_at"], str):
        return False
    if (
        not isinstance(marker["pid"], int)
        or isinstance(marker["pid"], bool)
        or marker["pid"] < 0
    ):
        return False
    if "payload_hash" in marker and not isinstance(marker["payload_hash"], str):
        return False
    return True


def current_runtime_python() -> str:
    """The CURRENT runtime slot's interpreter path, falling back to this
    process's own ``sys.executable`` whenever that slot doesn't resolve to
    a validated, complete runtime. A long-delayed caller (e.g. this
    module's own ``status-monitor-reap-stale``, which can run up to its
    configured delay after being spawned) may itself be running from a
    slot that is no longer current by the time it spawns a monitor --
    spawning with a stale interpreter would start an already-superseded
    (or, worse, incomplete/broken) monitor that ``_ensure_status_monitor()``
    would otherwise report as successfully ensured despite an immediate
    exit.

    Uses ``config.venv_python()`` (the same current-version ->
    last-known-good -> newest-slot resolution order the canonical
    hooks/binstubs use) but additionally validates the resolved
    candidate's ``.install-complete.json`` completion marker
    (``_slot_marker_valid``) before trusting it -- ``venv_python()`` itself
    accepts any existing interpreter file unconditionally, which the
    canonical resolvers do not.

    Deliberately does NOT fall back to scanning sibling slots when the
    resolved candidate's own marker fails to validate: a slot picked that
    way can still be immediately self-retired by
    ``status_updater_cli._runtime_superseded()``, which compares a running
    monitor's own prefix against the RAW ``current-version`` pointer
    value, not against whichever slot this resolver happened to validate
    -- under a stale/corrupt pointer naming a newer, incomplete slot, a
    spawned OLDER validated sibling would see itself as superseded and
    exit immediately, right back to zero monitors. Falling back directly
    to this process's own already-running ``sys.executable`` instead
    sidesteps that mismatch entirely (this process is itself proof that
    interpreter can run), at the cost of not auto-recovering from a
    genuinely corrupt ``current-version`` pointer -- a rarer, deeper
    failure mode than the ordinary post-cutover ambiguity this module
    exists to heal, and better pursued (if ever) as its own fix to
    ``status_updater_cli._runtime_superseded()`` making the two decisions
    marker-aware and mutually consistent, not scoped to this interim
    mitigation.

    Also does NOT replicate the canonical resolvers' full
    semantic-version-ordering comparison for ``venv_python()``'s own
    newest-complete-slot fallback (a direct, lexicographic ``versions/``
    directory-name sort): unifying Python-side resolution with those
    resolvers is a separate, broader hardening effort for
    ``config.venv_python()`` itself (already used elsewhere in this
    codebase)."""
    try:
        from . import config as _cfg
        current = _cfg.venv_python()
        if current.exists():
            root = current.parents[1] if current.parent.name in ("Scripts", "bin") else current.parent
            if _slot_marker_valid(root, root.name):
                return str(current)
    except Exception:
        pass
    return sys.executable


def _finite_non_negative_seconds(value: str) -> float:
    """``argparse`` ``type=`` validator for ``--delay-seconds``: rejects
    non-finite (``inf``/``nan``), negative, and unreasonably large values at
    parse time, rather than reaching ``time.sleep()`` with one --
    ``time.sleep(float("inf"))`` (and some huge-but-finite floats, e.g.
    ``1e308``) raises ``OverflowError`` before this command's own
    try/except, breaking its "always exits 0" contract."""
    parsed = float(value)
    if (
        parsed != parsed
        or parsed in (float("inf"), float("-inf"))
        or parsed < 0
        or parsed > MAX_DELAY_SECONDS
    ):
        raise argparse.ArgumentTypeError(
            f"must be a finite number of seconds between 0 and {MAX_DELAY_SECONDS:g}: "
            f"{value!r}"
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
    holding the cutover lock -- never by checking
    ``apply_daemon_health()``'s returned report. That call acquires and
    releases its OWN cutover guard internally before returning, so a
    report-only check-then-act here would have a real TOCTOU window in
    which a genuine concurrent cutover could start between the check and
    the spawn, racing a successor being promoted.

    Acquiring this SAME lock `activate_after_update()` itself acquires
    before running `CutoverOrchestrator.run()` makes the two mutually
    exclusive: while held here, no concurrent cutover attempt can be
    in-flight, so re-checking liveness and ensuring a monitor exist as one
    atomic unit is safe. A non-blocking acquire attempt (``timeout_s=0``)
    means a genuinely in-progress cutover is simply left alone -- it already
    owns ensuring a monitor exists once it finishes.

    Returns one of: ``"ensured"`` (spawned or confirmed live),
    ``"skipped-live"`` (a candidate already existed once re-checked),
    ``"skipped-disabled"`` (``AGENT_WORKTREES_STATUS_MONITOR=0`` -- the
    resident monitor is opted out; never spawn one on its behalf),
    ``"skipped-cutover-busy"`` (another cutover holds the lock), or
    ``"error:<detail>"`` (best-effort, never raises -- including a
    non-fatal spawn failure reported by ``_ensure_status_monitor()``
    itself).
    """
    from . import status_monitor_cutover as smc
    from . import status_monitor_runtime as smr
    from single_instance_lease import AlreadyRunningError

    if not smr._status_monitor_enabled():
        # _ensure_status_monitor() assumes its own callers already checked
        # this (status_monitor_runtime.py / worktree_status_audit.py both
        # do) -- it does not re-check itself, so skip the call entirely
        # rather than spawn a monitor the operator explicitly opted out of.
        return "skipped-disabled"
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
            if smr._ensure_status_monitor():
                return "ensured"
            return "error:ensure-status-monitor reported spawn failure"
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

    ``--delay-seconds`` is already validated finite/bounded by argparse
    (:func:`_finite_non_negative_seconds`); the fallback here only guards a
    direct, non-argparse call (e.g. a test constructing ``Namespace`` by
    hand) against the same ``OverflowError``/hang a raw ``inf``/``nan``/huge
    value would otherwise cause in ``time.sleep()`` below. The ``sleep()``
    call itself is additionally wrapped, since a finite float that still
    exceeds the platform's own sleep range is possible in principle even
    after this clamping.
    """
    delay = float(getattr(args, "delay_seconds", DEFAULT_DELAY_SECONDS))
    if (
        delay != delay
        or delay in (float("inf"), float("-inf"))
        or delay < 0
        or delay > MAX_DELAY_SECONDS
    ):
        delay = DEFAULT_DELAY_SECONDS
    try:
        time.sleep(delay)
    except (OverflowError, ValueError):
        time.sleep(DEFAULT_DELAY_SECONDS)
    try:
        from . import daemon_health
        report = daemon_health.doctor_report(apply=True)
    except Exception as exc:
        # Non-fatal: a failure here only means the duplicate-termination
        # half of the repair didn't run -- it establishes nothing about
        # whether a monitor is actually live. Still fall through to the
        # zero-candidate recheck-and-ensure below, which probes liveness
        # independently and reports its own failures safely; returning
        # here would skip healing the exact zero-monitor state this
        # command exists for.
        print(f"status-monitor-reap-stale: repair attempt failed (non-fatal): {exc}")
    else:
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

