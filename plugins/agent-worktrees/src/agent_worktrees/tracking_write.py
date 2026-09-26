"""Daemon-mediated write authority -- mutation-verb wire plumbing.

``agent-worktrees-authoritative-daemon`` effort, Phase 2. Mirrors
:mod:`classify_daemon`/:mod:`worktree_status_daemon`'s structure (the same
vendored ``work_coalescing_singleton`` transport, a third ``KIND`` alongside
``classify``/``worktree_status`` on the same resident daemon -- never a
second daemon process), but for **mutations** rather than reads.

**The single-implementation guarantee.** Every verb registered here (see
:func:`register_verb`) is called from exactly two places: the daemon's own
:func:`compute` callback (the steady-state path, when a daemon is reachable),
and :func:`run_direct` (the daemon-unreachable fallback). Both call the
*same* registered function -- there is deliberately no second, independently
maintained implementation of what a write means. This is
copilot-extensions#3761's resolved Open Question 1 (operator, verbatim):
"Internal import-based fallback to run the correct direct-write (reusing
same code, no fork), with log." :func:`run_direct` always logs the bypass
(worktree/verb/reason) -- never silent, never treated as an equally-preferred
alternative to going through the daemon (see the vision's
``no-writer-bypasses-the-daemon``).

**Coalescing key is always unique per write.** Unlike ``classify``/
``worktree_status`` (idempotent reads, safe to coalesce concurrent identical
requests onto one answer), two writes must never join the same in-flight
execution merely because they arrived close together -- each is a distinct
mutation intent. :func:`dispatch` always mints a fresh key
(``f"{verb}:{uuid4().hex}"``), so ``CoalescingServer``'s own ``(kind, key)``
dedup can never merge two different write calls (see
``work_coalescing_singleton.server.CoalescingServer.handle_request``).

**Verb granularity note (agent-recommended, from real code inspection):** a
verb should map to a call site's whole guarded read-lock-modify-save
transaction (e.g. "apply this status assertion, honoring the terminal-state
guard"), not to a single low-level field-setter function
(``tracking.set_disposition`` etc.) in isolation -- real call sites already
batch several such setters under one ``tracking._RecordLock`` before one
``save_record``, and per-setter verbs would either multiply round trips per
real operation or break that atomicity. See the effort README's Phase 2
Journal for the full finding; this module's registry is verb-name-agnostic
and does not assume either granularity, but a migrated call site should
register at the transaction boundary, not the setter boundary.

**Not yet the full authority design.** This module proves the wire
plumbing and the fallback/logging guarantee end-to-end. It does not yet hold
a persistent in-memory record store the way the vision's long-term direction
describes (``worktree_status_cache.py``'s warm-restore pattern is the
intended model for that, tracked as a separate, not-yet-done Phase 2 item) --
today, a registered verb still does its own fresh lock/load/save against the
YAML record on every call, whether invoked from the daemon or directly. That
is still a real improvement: every write funnels through one process (the
daemon) when reachable, serializing what were previously N independent
CLI-invocation writers into one, with the file lock as continued defense in
depth -- it is a correctness step, not the final performance shape.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

logger = logging.getLogger(__name__)

KIND = "tracking_write"

#: A migrated verb is a real lock/load/mutate/save transaction -- comparable
#: cost to one of `worktree_status_compute`'s own facts, not a bare read.
#: Kept modest since a write's own caller (a CLI command) has no render/click
#: deadline to protect the way a status-bar consumer does, but should still
#: not hang indefinitely on a wedged daemon.
BOOT_WAIT_S = 4.0
REQUEST_DEADLINE_S = 8.0

#: Shorter linger than classify/worktree_status: a write is typically a
#: single CLI invocation's one-shot call, not a render loop with repeat
#: traffic, so there's little benefit to keeping the daemon warm for it alone
#: (it very likely already stays warm anyway, kept alive by other kinds'
#: traffic against the same resident monitor).
LINGER_SECONDS = 8.0
SUBSCRIBER_TTL_SECONDS = 30.0

_VERBS: dict[str, Callable[[dict], dict]] = {}


def register_verb(name: str, fn: Callable[[dict], dict]) -> None:
    """Register a mutation verb.

    ``fn`` receives the request's ``args`` dict and returns a JSON-safe
    result dict. Called from both the daemon's own :func:`compute` and this
    module's :func:`run_direct` fallback -- the one place either path reaches
    the actual mutation, per this module's single-implementation guarantee.
    """
    _VERBS[name] = fn


def registered_verbs() -> frozenset[str]:
    """The currently registered verb names (tests / introspection)."""
    return frozenset(_VERBS)


def compute(kind: str, payload: dict) -> dict:
    """``CoalescingServer``-shaped ``compute(kind, payload)`` callback.

    ``payload`` must carry ``verb`` (a name registered via
    :func:`register_verb`) and may carry ``args`` (a dict passed to that
    verb's function). Raises ``ValueError`` for an unregistered verb or a
    malformed payload -- surfaced to the caller exactly like
    ``_classify_daemon_compute``'s own validation errors.
    """
    verb = payload.get("verb")
    if not isinstance(verb, str) or not verb:
        raise ValueError("tracking_write request missing a verb name")
    fn = _VERBS.get(verb)
    if fn is None:
        raise ValueError(f"tracking_write: unregistered verb {verb!r}")
    args = payload.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("tracking_write request 'args' must be a dict")
    return fn(args)


def start_server(
    compute_fn: Callable[[str, dict], dict],
    *,
    on_idle: Callable[[], None] | None = None,
) -> CoalescingServer:
    """Start the tracking-write-serving daemon side (not yet published anywhere)."""
    return CoalescingServer(
        compute_fn,
        linger_seconds=LINGER_SECONDS,
        subscriber_ttl=SUBSCRIBER_TTL_SECONDS,
        on_idle=on_idle,
    )


def rendezvous_fields(server: CoalescingServer) -> dict:
    """Rendezvous fields namespaced so they never collide with the resident
    status-monitor's existing ``hook_ipc``/``classify_daemon``/
    ``worktree_status_daemon``/``mux_link`` fields in the same lock file."""
    rv = server.rendezvous()
    return {
        "tracking_write_transport": rv["transport"],
        "tracking_write_endpoint": rv["endpoint"],
        "tracking_write_token": rv["token"],
        "tracking_write_generation": rv["generation"],
    }


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    """Parse this module's rendezvous fields out of an already-read lock dict.

    Returns ``None`` for anything malformed or absent -- the caller's own
    correct fallback path, never an exception."""
    if not isinstance(data, dict):
        return None
    endpoint = data.get("tracking_write_endpoint")
    token = data.get("tracking_write_token")
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


def run_direct(verb: str, args: dict, *, reason: str) -> dict:
    """The daemon-unreachable fallback: run ``verb``'s own registered
    function directly, in-process -- the identical implementation
    :func:`compute` would have called, never a forked second one.

    Always logs the bypass (verb name + reason) before running it, so a
    later reconciliation pass has a durable trail of every mutation that
    happened outside the daemon's own mediation. Raises ``ValueError`` for an
    unregistered verb, mirroring :func:`compute`'s own validation.
    """
    fn = _VERBS.get(verb)
    if fn is None:
        raise ValueError(f"tracking_write: unregistered verb {verb!r}")
    logger.warning(
        "tracking_write: bypassing daemon for verb=%s reason=%s", verb, reason
    )
    return fn(args)


def write_with_boot(
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
    """Dial, boot-and-wait if no resident monitor currently publishes a
    tracking-write endpoint, then send one request -- mirrors
    ``classify_daemon.classify_with_boot`` / ``worktree_status_daemon.
    status_with_boot`` exactly (see either docstring for the full per-step
    rationale). Never raises past this call: any miss at any stage runs
    ``fallback()``.
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


def dispatch(
    verb: str,
    args: dict,
    *,
    read_lock_data: Callable[[], dict | None],
    ensure_monitor: Callable[[], bool] | None,
    request_deadline_s: float = REQUEST_DEADLINE_S,
    boot_wait_s: float = BOOT_WAIT_S,
) -> dict:
    """The public entry point a migrated call site uses in place of calling
    its verb's function directly: try the resident daemon first (booting one
    on demand if none is reachable), falling back to :func:`run_direct`
    (same code, logged) when no daemon can be reached at all.

    A fresh, unique coalescing key is minted for every call (see this
    module's docstring, "Coalescing key is always unique per write") so two
    concurrent writes -- even for the same verb and worktree -- are never
    merged into one execution.
    """
    key = f"{verb}:{uuid.uuid4().hex}"
    payload = {"verb": verb, "args": args}

    def _fallback() -> dict:
        return run_direct(verb, args, reason="no resident daemon reachable")

    return write_with_boot(
        read_lock_data=read_lock_data,
        ensure_monitor=ensure_monitor,
        key=key,
        payload=payload,
        fallback=_fallback,
        request_deadline_s=request_deadline_s,
        boot_wait_s=boot_wait_s,
    )
