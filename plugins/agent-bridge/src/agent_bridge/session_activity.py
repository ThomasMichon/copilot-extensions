"""Activity accounting that preserves owned native and ACP executions."""
from __future__ import annotations
import logging
import time
log = logging.getLogger("agent-bridge")
_ACTIVE_STATUSES = {"created", "starting", "running", "idle"}

def _count_active_sessions(mgr, db=None) -> int:
    """Count work this daemon still owns or represents.

    Supersession self-retire uses this as its idleness gate. Count both
    manager-owned ACP sessions and still-live Session Hosts, plus fresh live-CLI
    registrations when a database is available. Stale live registrations are
    deliberately ignored; the live-session reaper reconciles those separately.
    """
    n = 0
    for s in mgr.list_sessions():
        st = getattr(s, "status", None)
        st = getattr(st, "value", st)
        if str(st).lower() in _ACTIVE_STATUSES:
            n += 1
    live_hosts = getattr(mgr, "_live_host_records", None)
    if callable(live_hosts):
        try:
            n += len(live_hosts())
        except Exception:
            log.debug("Self-retire host-record count failed", exc_info=True)
    if db is not None:
        list_fresh = getattr(db, "list_fresh_live_sessions", None)
        if callable(list_fresh):
            try:
                n += len(list_fresh(now=time.time()))
            except Exception:
                log.debug(
                    "Self-retire live-session registration count failed",
                    exc_info=True,
                )
    native_owner = getattr(mgr, "native_manager", None)
    if native_owner is not None and native_owner.serving() is True:
        try:
            n += len(native_owner.records())
        except Exception:
            n += 1  # uncertain native ownership must not trigger idle shutdown
    return n

