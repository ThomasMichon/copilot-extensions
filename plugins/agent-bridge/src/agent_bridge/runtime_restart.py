"""Read-only restart-readiness check for the idle-gated reconcile restart (#533).

A routine version-bump redeploy must **never collapse a live dispatch**. Before
the installer swaps + restarts the daemon on a *reconcile-driven* update
(``install.ps1 update -DeferRestartIfBusy``), it asks whether the daemon is
*busy* -- actively dispatching a turn. This is a **read-only** query: unlike
``drain`` (which refuses new work / sets the draining flag), probing here does
not perturb a healthy daemon, so it is safe to run on every reconcile pass.

"Busy" is deliberately conservative -- a session actively ``RUNNING`` a turn, or
hosting active background sub-agents -- so we err toward *deferring* a restart
(the operator can always force one) rather than interrupting live work.
"""

from __future__ import annotations

from typing import Any

# Session statuses that mean "a restart would interrupt live work". Kept narrow
# (only an actively-dispatching turn); IDLE/connected sessions reattach across a
# daemon restart via the Session-Host reattach on lifespan startup.
BUSY_STATUSES = frozenset({"running"})

# Exit code the `service is-busy` CLI returns when the daemon is actively
# dispatching. Deliberately NOT 2: argparse exits 2 on an unknown subcommand, so
# an *older* installed CLI (one that predates `is-busy`) would otherwise look
# "busy" to the installer's idle-gate and defer the upgrade forever. 3 is
# unambiguous -- only a real busy daemon returns it.
BUSY_EXIT_CODE = 3


def busy_sessions(sessions: list[dict[str, Any]]) -> list[str]:
    """Ids of sessions actively dispatching (a restart would interrupt them)."""
    out: list[str] = []
    for s in sessions or []:
        status = str(s.get("status", "")).lower()
        if status in BUSY_STATUSES or s.get("has_active_background_tasks"):
            out.append(str(s.get("id") or s.get("session_id") or "?"))
    return out


def daemon_busy_sessions(client) -> list[str]:
    """Query the running daemon (read-only) for actively-dispatching sessions.

    Returns ``[]`` when the daemon is idle **or unreachable** -- an unreachable
    daemon is not serving a live dispatch, so a restart is safe (it just comes
    up on the new build). Any query error is swallowed to the same safe default.
    """
    try:
        sessions = client.list_sessions()
    except Exception:
        return []
    return busy_sessions(sessions)
