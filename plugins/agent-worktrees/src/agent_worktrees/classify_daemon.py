"""Resident classify/list accelerator scaffolding (plugin-process-hygiene #2323).

Thin wrappers around the vendored ``work_coalescing_singleton`` library, scoped
to agent-worktrees' own request shape: coalesce concurrent ``list --json
--classify`` batch-classification passes for the same project onto one
resident execution, instead of each independently recomputing it (today's
Phase 4c fallback, the per-project :class:`single_instance_lease.SingleInstance`
guard around ``_classify_records`` in ``__main__.py``, stays the correct
degrade path when no daemon is reachable -- this module never replaces it,
only gives a caller a faster path to try first).

**Deliberately NOT wired into any command yet.** This module owns only the
wire layer (start the daemon side, make one coalesced client request); the
actual git-classification work stays exactly ``_classify_records_live`` /
``_classify_from_cache`` in ``__main__.py``. Wiring -- publishing this
daemon's rendezvous alongside the resident status-monitor's existing
``HookIpcServer`` rendezvous, and trying it from ``_classify_records`` before
falling back to today's lease-based path -- is tracked, still-open follow-up
(see the ``plugin-process-hygiene`` effort journal for the concrete plan:
the daemon's ``compute`` callback loads a project's records itself via
``tracking.list_records``, so a request payload only needs to name the
project, never serialize whole records over the wire).
"""

from __future__ import annotations

from collections.abc import Callable

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

#: A cold batch-classification pass (~5 git calls per worktree) is seconds,
#: not the ~1s hot session-lifecycle-hook decision path.
BOOT_WAIT_S = 6.0
REQUEST_DEADLINE_S = 5.0
KIND = "classify"

#: How long the daemon lingers with zero subscribers before idle-exiting, and
#: how stale a subscriber's last-seen stamp may get before the liveness
#: reaper drops it. Generous relative to a single CLI invocation's lifetime
#: (a `list --json --classify` round trip), short relative to an operator
#: session.
LINGER_SECONDS = 10.0
SUBSCRIBER_TTL_SECONDS = 45.0


def start_server(
    compute: Callable[[str, dict], dict],
    *,
    on_idle: Callable[[], None] | None = None,
) -> CoalescingServer:
    """Start the classify-serving daemon side (not yet published anywhere)."""
    return CoalescingServer(
        compute,
        linger_seconds=LINGER_SECONDS,
        subscriber_ttl=SUBSCRIBER_TTL_SECONDS,
        on_idle=on_idle,
    )


def rendezvous_fields(server: CoalescingServer) -> dict:
    """Rendezvous fields namespaced so they never collide with the resident
    status-monitor's existing ``HookIpcServer`` fields in the same lock file
    (see ``hook_ipc.HookIpcServer.rendezvous``)."""
    rv = server.rendezvous()
    return {
        "classify_transport": rv["transport"],
        "classify_endpoint": rv["endpoint"],
        "classify_token": rv["token"],
        "classify_generation": rv["generation"],
    }


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    """Parse this module's rendezvous fields out of an already-read lock dict.

    Returns ``None`` for anything malformed or absent -- the caller's own
    correct fallback path, never an exception.
    """
    if not isinstance(data, dict):
        return None
    endpoint = data.get("classify_endpoint")
    token = data.get("classify_token")
    if not isinstance(endpoint, str) or not isinstance(token, str) or not token:
        return None
    host, _, port_s = endpoint.partition(":")
    if not host or not port_s:
        return None
    try:
        port = int(port_s)
    except ValueError:
        return None
    return host, port, token


def classify_via_daemon(
    lock_data: dict | None,
    *,
    key: str,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
) -> dict:
    """Try a live daemon (per ``lock_data``'s rendezvous fields) first, else
    run ``fallback`` immediately.

    Never boots a daemon itself -- a resident monitor's own lifecycle (launch/
    idle-exit) stays entirely its existing owner's responsibility; this
    function only decides whether one it can already see is worth asking.
    Never raises past this call: any daemon miss (absent, unreachable, past
    its deadline) is indistinguishable from "no daemon" to the caller.
    """
    endpoint = endpoint_from_rendezvous(lock_data)
    if endpoint is None:
        return fallback()
    host, port, token = endpoint
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=KIND,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
