"""Resident classify/list accelerator (plugin-process-hygiene #2323).

Thin wrappers around the vendored ``work_coalescing_singleton`` library, scoped
to agent-worktrees' own request shape: coalesce concurrent ``list --json
--classify`` batch-classification passes for the same project onto one
resident execution, instead of each independently recomputing it (the
per-project :class:`single_instance_lease.SingleInstance` guard around
``_classify_records`` in ``__main__.py`` stays the correct degrade path when
no daemon is reachable -- this module never replaces it, only gives a caller
a faster path to try first).

**Wired into `cmd_status_monitor`/`_classify_records`.** This module owns the
wire layer (start the daemon side, make one coalesced client request); the
actual git-classification work stays in ``_classify_daemon_compute`` /
``_classify_records_live`` / ``_classify_from_cache`` in ``__main__.py``. The
resident status-monitor starts a :class:`work_coalescing_singleton.CoalescingServer`
via :func:`start_server`, publishes its rendezvous alongside the existing
``HookIpcServer`` rendezvous in the same lock file, and ``_classify_records``
tries :func:`classify_with_boot` before falling back to the pre-#2323
lease-guarded path (see the ``plugin-process-hygiene`` effort journal for the
full design + validation writeup: the daemon's ``compute`` callback loads a
project's records itself via ``tracking.list_records``, so a request payload
only ever names the project + its list filters, never serializes whole
records over the wire).
"""


from __future__ import annotations

import time
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


def classify_with_boot(
    *,
    read_lock_data: Callable[[], dict | None],
    ensure_monitor: Callable[[], bool] | None,
    key: str,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
    boot_wait_s: float = BOOT_WAIT_S,
    poll_interval_s: float = 0.1,
) -> dict:
    """The full Phase 4d production sequence: dial, boot-and-wait if no
    resident monitor is currently publishing a classify endpoint, then send
    one coalesced request carrying a fresh per-call client id (so the
    daemon's own ref-counted idle-exit sees this caller as a live, if brief,
    subscriber -- see ``work_coalescing_singleton.server.CoalescingServer``'s
    ``touch``-on-request behavior) -- and releases that same client id in a
    ``finally`` right after, so a one-shot caller (this module has no
    long-lived session to keep a subscription open for) doesn't linger in
    the daemon's subscriber map until the 45s TTL reaper drops it. Release is
    best-effort: any failure there is backstopped by that same reaper, per
    ``work_coalescing_singleton.client.release``'s own contract.

    Deliberately reimplements (rather than calls)
    ``work_coalescing_singleton.client.call_with_fallback``'s dial/boot/poll
    algorithm, since that shared, byte-identical-across-plugins helper has no
    post-request release hook to attach to. ``read_lock_data`` is re-invoked
    on every boot-wait poll (never cached), so a monitor that starts mid-wait
    is picked up. ``ensure_monitor=None`` (or its own failure) degrades to a
    dial-only attempt -- still safe, just without the boot side. Never raises
    past this call: any miss at any stage (no daemon, boot-wait timeout,
    request timeout/error) runs ``fallback()``, identical in effect to
    :func:`classify_via_daemon`.
    """
    started = time.time()

    def _dial() -> tuple[str, int, str] | None:
        return endpoint_from_rendezvous(read_lock_data())

    endpoint = _dial()
    if endpoint is None and ensure_monitor is not None:
        ensure_monitor()
        while endpoint is None and time.time() - started < boot_wait_s:
            time.sleep(poll_interval_s)
            endpoint = _dial()
    if endpoint is None:
        return fallback()

    host, port, token = endpoint
    client_id = wcs_client.new_client_id()
    try:
        return wcs_client.request(
            host,
            port,
            token,
            kind=KIND,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
    except wcs_client.DaemonUnavailable:
        return fallback()
    finally:
        wcs_client.release(host, port, token, client_id, timeout=request_deadline_s)


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
