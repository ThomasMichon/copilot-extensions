"""Detect a wedged (alive-but-stuck) supervisor daemon from its own written
runtime-status snapshot.

Split out of ``loop_commands.py`` to keep that module under its line cap; this
is otherwise a natural extension of the same runtime-status projection it
already reads.
"""

from __future__ import annotations

import time

# A cycle running longer than this is flagged stalled. There is no single
# per-declaration poll_interval visible here (the daemon multiplexes many),
# so this is a coarse, deliberately generous floor -- a genuinely wedged
# cycle runs for many minutes to indefinitely, not tens of seconds.
SUPERVISOR_STALL_THRESHOLD_SECONDS = 180.0


def supervisor_stall_seconds(runtime_status: dict | None) -> float | None:
    """How long the *current* reconcile cycle has been running, if stuck --
    ``None`` when idle-but-healthy or under-informed (e.g. a freshly
    (re)started daemon with no finished cycle yet).

    ``cycle_started_at`` is written just before every reconcile call;
    ``updated_at`` only after one finishes. While healthy the two advance
    together every cycle, so their gap stays near zero. A cycle wedged inside
    ``SupervisorDaemon.reconcile_once`` (the process alive, but that single
    call never returning -- e.g. an unbounded subprocess/liveness probe)
    leaves ``cycle_started_at`` frozen while ``updated_at`` also stops
    advancing, so this returns the elapsed time since that cycle started
    once it exceeds the stall threshold -- distinct from merely noting the
    whole status file is old, which looks identical for a plain-dead
    process.
    """
    if not runtime_status:
        return None
    started = runtime_status.get("cycle_started_at")
    finished = runtime_status.get("updated_at")
    if not isinstance(started, (int, float)):
        return None
    if isinstance(finished, (int, float)) and finished >= started:
        return None  # the most recent cycle already completed
    elapsed = time.time() - started
    if elapsed < SUPERVISOR_STALL_THRESHOLD_SECONDS:
        return None
    return elapsed


def supervisor_stall_action(stall_seconds: float) -> str:
    return (
        f"supervisor alive but stuck mid-cycle {stall_seconds:.0f}s -- check "
        "its log for a hung call; restart via `install.ps1 update`, never a "
        "manual process kill"
    )
