"""Daemon-mediated write authority -- mutation-verb wire plumbing.

``agent-worktrees-authoritative-daemon`` effort, Phase 2. Mirrors
:mod:`classify_daemon`/:mod:`worktree_status_daemon`'s structure (the same
vendored ``work_coalescing_singleton`` transport, a fourth ``KIND`` alongside
``classify``/``worktree_status``/``mux_link`` on the same resident daemon --
never a second daemon process), but for **mutations** rather than reads.

**The single-implementation guarantee.** Every verb registered here (see
:func:`register_verb`) is called from exactly two places: the daemon's own
:func:`compute` callback (the steady-state path, when a daemon is reachable),
and :func:`run_direct` (the daemon-unreachable fallback). Both call the
*same* registered function -- there is deliberately no second, independently
maintained implementation of what a write means. This is
copilot-extensions#3761's resolved Open Question 1 (operator, verbatim):
"Internal import-based fallback to run the correct direct-write (reusing
same code, no fork), with log." :func:`run_direct` always logs the bypass
(verb name and reason, plus a best-effort worktree identifier when a
verb's own ``args`` happen to carry one -- never a required contract, since
this module's registry is deliberately verb-shape-agnostic) -- never
silent, never treated as an equally-preferred alternative to going through
the daemon (see the vision's ``no-writer-bypasses-the-daemon``).

**The fallback only ever runs when nothing could have been sent.**
:func:`run_direct` is reached automatically in exactly two cases: no daemon
endpoint could be discovered at all (even after a boot-wait), or an
endpoint was found but the TCP connect itself failed (a stale rendezvous
entry left by a since-exited daemon). In both, no request bytes were ever
sent, so nothing could have already run. A request that *was* actually sent
over an established connection and then failed (timeout, malformed
response) is a genuinely different, ambiguous case -- the daemon may
already be executing or have already committed the mutation -- and
:func:`write_with_boot`/:func:`dispatch` raise
:class:`AmbiguousWriteOutcome` there instead of silently retrying, per
2026-09-26 PR review findings (this distinction did not exist in this
module's first-landed version, and its first fix still conflated "endpoint
found" with "request sent" -- see :func:`_send_tracking_write_request`'s
own docstring for why a custom send/receive split was needed to separate
them correctly).

**Coalescing key is always unique per write.** Unlike ``classify``/
``worktree_status`` (idempotent reads, safe to coalesce concurrent identical
requests onto one answer), two writes must never join the same in-flight
execution merely because they arrived close together -- each is a distinct
mutation intent. :func:`write_with_boot` always mints a fresh key
(``uuid4().hex``) itself -- never accepts one from a caller -- so
``CoalescingServer``'s own ``(kind, key)`` dedup can never merge two
different write calls (see
``work_coalescing_singleton.server.CoalescingServer.handle_request``) no
matter what any current or future entry point passes.

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

import importlib
import json
import logging
import socket
import threading
import time
import uuid
from collections.abc import Callable

from work_coalescing_singleton import CoalescingServer
from work_coalescing_singleton import client as wcs_client

logger = logging.getLogger(__name__)


class AmbiguousWriteOutcome(Exception):
    """A write's daemon request failed *after* the request was actually sent
    over an established connection -- the mutation may already be
    committed, or still executing, server-side (``CoalescingServer`` does
    not cancel an already-accepted owner compute when a caller's own socket
    read times out). Unlike the "no daemon reachable at all" case (safe:
    nothing was ever sent), this state is never safe to silently retry via
    the same-code direct fallback -- doing so could double-apply a
    non-idempotent write (a counter increment, an appended session entry).
    The caller must decide: report the error, or design the specific verb
    to be safely idempotent/dedupable before ever allowing an automatic
    retry here.

    Deliberately **not** raised for a failure before the request bytes were
    ever sent (a refused/timed-out connect -- e.g. a stale rendezvous entry
    left by a since-exited daemon): that case is indistinguishable in
    effect from never having dialed a daemon at all, so it degrades to the
    same-code fallback exactly like a pre-dial miss (see
    :class:`_PreSendFailure` and :func:`write_with_boot`, 2026-09-26 PR
    review finding).
    """


class _PreSendFailure(Exception):
    """The TCP connect itself failed or timed out, before any request bytes
    were sent -- nothing could have run server-side. Caught internally by
    :func:`write_with_boot` and treated exactly like a pre-dial miss (safe
    to run the same-code fallback), never surfaced past this module."""


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

#: Per-verb schema version: reusing an existing verb NAME for an enriched
#: ``args`` shape (e.g. adding a field a pre-upgrade daemon process's
#: still-running old code would silently ignore) is invisible to the
#: ``tracking_write_verbs`` capability check below, which only ever looks
#: at the verb's NAME. A verb whose payload shape changed bumps its own
#: version here; :func:`endpoint_from_rendezvous` additionally rejects an
#: endpoint whose advertised version for that verb is too old, so the
#: request falls back to :func:`run_direct` -- always correct, since that
#: runs the CALLING process's own current code -- for the rest of that
#: rolling-upgrade window, exactly like an unknown verb name already does.
_VERB_VERSIONS: dict[str, int] = {}

#: How many :func:`compute` calls (verb executions) are currently running in
#: this process -- the daemon process, when this module's server is live.
#: Deliberately independent of ``CoalescingServer.subscriber_count()``: a
#: client releases its own subscriber lease as soon as *its own* request
#: call returns or times out, which can happen well before the server-side
#: compute this counter tracks actually finishes (2026-09-26 PR review
#: finding). See :func:`has_inflight_write`.
_inflight_writes = 0
_inflight_lock = threading.Lock()


def has_inflight_write() -> bool:
    """Whether a :func:`compute` call is currently executing in this process.

    The resident monitor's idle-shutdown predicate should check this
    alongside (not instead of) its ``CoalescingServer.subscriber_count()``
    check -- a lingering subscriber with no compute running is still a
    legitimate "someone is about to ask" signal, and a running compute with
    no subscriber left (the case this function exists for) must still block
    shutdown.
    """
    with _inflight_lock:
        return _inflight_writes > 0


#: The cross-process registration contract (2026-09-26 PR review finding --
#: `_VERBS` alone is a process-local dict; the resident daemon and any CLI
#: process calling :func:`dispatch`/:func:`run_direct` are *separate*
#: Python processes, so a `register_verb` call made in one never populates
#: the other's dict). Each entry here is a **fully-qualified module name**
#: (e.g. ``"agent_worktrees.tracking_session_registry"`` for a real
#: production verb module) that self-registers its own verb(s) via
#: `register_verb` calls in its own module-level code, purely as an import
#: side effect -- never a function reference or a registration RPC. Every
#: process that reaches this module's :func:`compute`/:func:`run_direct`
#: first calls :func:`_ensure_verb_modules_loaded`, which imports every
#: module listed here -- so the daemon process and a CLI process both
#: arrive at the identical registry independently, with no shared mutable
#: state or wire message required between them. Phase 3's first migrated
#: verb (`status_disposition_write`, see `tracking_disposition_write.py`) is
#: the first real entry; a verb-owning module is added here, not wired via
#: ad-hoc `register_verb` calls from arbitrary call sites.
_VERB_MODULES: tuple[str, ...] = (
    "agent_worktrees.tracking_disposition_write",
    "agent_worktrees.tracking_followup_write",
    "agent_worktrees.tracking_claim_write",
    "agent_worktrees.tracking_session_lifecycle_write",
    "agent_worktrees.tracking_session_registration_write",
    "agent_worktrees.tracking_session_deregistration_write",
)

_verb_modules_loaded = False
_verb_modules_lock = threading.Lock()


def _ensure_verb_modules_loaded() -> None:
    """Import every module named in :data:`_VERB_MODULES`, once per process.

    Idempotent and thread-safe. Called at the top of both :func:`compute`
    (the daemon-process path) and :func:`run_direct` (the direct-call
    fallback, which may run in the daemon process or a separate CLI
    process) -- see :data:`_VERB_MODULES`'s own docstring for why this is
    what actually closes the cross-process registration gap, rather than a
    convention a caller could forget to follow.
    """
    global _verb_modules_loaded
    if _verb_modules_loaded:
        return
    with _verb_modules_lock:
        if _verb_modules_loaded:
            return
        for name in _VERB_MODULES:
            importlib.import_module(name)
        _verb_modules_loaded = True


def register_verb(name: str, fn: Callable[[dict], dict], *, version: int = 1) -> None:
    """Register a mutation verb.

    ``fn`` receives the request's ``args`` dict and returns a JSON-safe
    result dict. Called from both the daemon's own :func:`compute` and this
    module's :func:`run_direct` fallback -- the one place either path reaches
    the actual mutation, per this module's single-implementation guarantee.

    Intended callers: a verb-owning module's own top level (so importing it
    -- via :func:`_ensure_verb_modules_loaded`, per :data:`_VERB_MODULES` --
    is what registers it, identically in every process), or a test that
    needs a throwaway verb for the duration of one test. A production verb
    registered from inside a CLI command's own function body would reproduce
    the cross-process gap :data:`_VERB_MODULES` exists to close -- register
    at import time, not call time.

    ``version`` defaults to 1 and only needs bumping when an existing verb's
    ``args``/result SHAPE changes in a way an older registered ``fn`` for the
    same name would silently mishandle (see :data:`_VERB_VERSIONS`) -- a
    purely additive, backward-compatible change (an older ``fn`` ignores an
    unrecognized arg key safely) does not require a bump.
    """
    _VERBS[name] = fn
    _VERB_VERSIONS[name] = version


def registered_verbs() -> frozenset[str]:
    """The currently registered verb names (tests / introspection).

    Does **not** call :func:`_ensure_verb_modules_loaded` itself -- this is a
    read of whatever is registered *right now* (useful for a test asserting
    the loader's own effect), not a trigger for loading.
    """
    return frozenset(_VERBS)


def compute(kind: str, payload: dict) -> dict:
    """``CoalescingServer``-shaped ``compute(kind, payload)`` callback.

    Rejects any request whose ``kind`` is not :data:`KIND` (2026-09-26 PR
    review finding, mirroring ``mux_link.py``'s own
    ``build_cached_compute``/``_compute`` guard) -- without this check, a
    request mislabeled with a different daemon's ``kind`` (e.g.
    ``"classify"``) would still be dispatched as a mutation here.

    ``payload`` must carry ``verb`` (a name registered via
    :func:`register_verb`, directly or via :data:`_VERB_MODULES`) and may
    carry ``args`` (a dict passed to that verb's function). Raises
    ``ValueError`` for a wrong ``kind`` or a malformed payload -- surfaced
    to the caller exactly like ``_classify_daemon_compute``'s own
    validation errors. An unregistered verb is deliberately NOT one of
    these: it returns ``{"unsupported_verb": True}`` instead of raising
    (copilot-extensions#3812) -- ``work_coalescing_singleton``'s own
    handler catches any exception ``compute()`` raises with a bare
    ``except Exception: return`` (closing the connection with no response
    bytes), which the client reads as an empty response and treats
    identically to "the request may have already run" -- indistinguishable
    from a request that genuinely started executing before failing. A
    version-skew rolling-upgrade window (a post-upgrade client dispatching
    a new verb to a still-running pre-upgrade daemon process, whose own
    ``_VERB_MODULES`` registration never heard of it) hits this exact path
    on every request until that daemon process itself restarts -- nothing
    ever ran server-side in that case, so it is always safe to fall back,
    but the empty-response signal alone can't tell the two apart. Returning
    a real, JSON-safe result instead lets :func:`write_with_boot` make that
    distinction explicitly and safely fall back rather than raising
    :class:`AmbiguousWriteOutcome` for a request that never executed.

    Counts itself in :data:`_inflight_writes` for the whole verb call (see
    :func:`has_inflight_write` -- 2026-09-26 PR review finding: a client
    that times out releases its subscriber lease immediately, even though
    this compute -- running here, in the daemon process, on its own handler
    thread -- keeps executing; the monitor's idle-shutdown predicate must
    not rely on subscriber count alone to decide a write is no longer in
    progress).
    """
    global _inflight_writes
    if kind != KIND:
        raise ValueError(f"tracking_write daemon does not serve kind={kind!r}")
    _ensure_verb_modules_loaded()
    verb = payload.get("verb")
    if not isinstance(verb, str) or not verb:
        raise ValueError("tracking_write request missing a verb name")
    fn = _VERBS.get(verb)
    if fn is None:
        # Never raise here (copilot-extensions#3812) -- see this function's
        # own docstring for why an unregistered verb must be a real,
        # JSON-safe result rather than an exception.
        return {"unsupported_verb": True}
    args = payload.get("args")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        raise ValueError("tracking_write request 'args' must be a dict")
    with _inflight_lock:
        _inflight_writes += 1
    try:
        return fn(args)
    finally:
        with _inflight_lock:
            _inflight_writes -= 1


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
    ``worktree_status_daemon``/``mux_link`` fields in the same lock file.

    Publishes this process's own currently-registered verb names
    (``tracking_write_verbs``, 2026-09-27 PR review finding,
    copilot-extensions#3812) so a client can refuse to dial an endpoint
    that structurally cannot serve the verb it needs -- a resident daemon
    process whose own ``_VERB_MODULES`` registration predates a verb (a
    rolling-upgrade version-skew window) is missing or lacks that verb in
    this list, never something a client should attempt to dial and then
    have to recover from an ``unsupported_verb``/``AmbiguousWriteOutcome``
    response for. Calls :func:`_ensure_verb_modules_loaded` itself so the
    published list is always this process's FULL verb set, regardless of
    whether any request has been served yet.
    """
    _ensure_verb_modules_loaded()
    rv = server.rendezvous()
    return {
        "tracking_write_transport": rv["transport"],
        "tracking_write_endpoint": rv["endpoint"],
        "tracking_write_token": rv["token"],
        "tracking_write_generation": rv["generation"],
        "tracking_write_verbs": sorted(_VERBS),
        # Only published when at least one verb actually needs it -- keeps
        # this payload, and every existing consumer's exact-field
        # expectations, unchanged for the overwhelming majority of verbs
        # that never bumped past version 1.
        **(
            {
                "tracking_write_verb_versions": {
                    name: version for name, version in _VERB_VERSIONS.items() if version > 1
                }
            }
            if any(version > 1 for version in _VERB_VERSIONS.values())
            else {}
        ),
    }


def endpoint_from_rendezvous(
    data: dict | None, *, verb: str | None = None, min_version: int = 1,
) -> tuple[str, int, str] | None:
    """Parse this module's rendezvous fields out of an already-read lock dict.

    Returns ``None`` for anything malformed or absent -- the caller's own
    correct fallback path, never an exception. Rejects a port outside
    ``0 < port < 65536`` (2026-09-26 PR review finding: an unvalidated port
    -- 0, negative, or above the valid range -- would otherwise be treated
    as a real endpoint and fail deep inside the socket client instead of
    safely degrading to the no-endpoint fallback here; mirrors
    ``mux_link.endpoint_from_rendezvous``'s own check).

    When ``verb`` is given, ALSO rejects an endpoint whose own published
    ``tracking_write_verbs`` list (see :func:`rendezvous_fields`) does not
    contain it -- including data published by a pre-this-change daemon
    process, which lacks the field entirely (2026-09-27 PR review finding,
    copilot-extensions#3812: returning ``{"unsupported_verb": True}`` from
    ``compute()`` only helps once BOTH sides already run this fix; it
    cannot retroactively help a still-running daemon process that predates
    it. This capability check closes that residual gap for good: an
    endpoint that cannot demonstrate it serves ``verb`` is never dialed at
    all, so the caller's normal boot-wait/fallback path runs instead,
    exactly as if no endpoint had been found).

    When ``min_version`` is above 1, ALSO rejects an endpoint whose
    published ``tracking_write_verb_versions`` entry for ``verb`` is lower
    (including absent, which means version 1) -- the same-name-but-
    enriched-payload rolling-upgrade case :data:`_VERB_VERSIONS` exists for:
    a verb a pre-upgrade daemon process already recognizes by name, but
    whose still-running old ``fn`` would silently ignore (or otherwise
    mishandle) a field a newer payload shape adds. Unlike the name check,
    this can never be confirmed from the daemon's response after the fact
    (the old ``fn`` succeeds normally, it just drops data) -- it must be
    caught here, before dialing.
    """
    if not isinstance(data, dict):
        return None
    if verb is not None:
        verbs = data.get("tracking_write_verbs")
        if not isinstance(verbs, list) or verb not in verbs:
            return None
        if min_version > 1:
            versions = data.get("tracking_write_verb_versions")
            current = versions.get(verb, 1) if isinstance(versions, dict) else 1
            if not isinstance(current, int) or current < min_version:
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
    if not (0 < port < 65536):
        return None
    return host, port, token


def run_direct(verb: str, args: dict, *, reason: str) -> dict:
    """The daemon-unreachable fallback: run ``verb``'s own registered
    function directly, in-process -- the identical implementation
    :func:`compute` would have called, never a forked second one.

    Always logs the bypass (verb name + reason, plus ``args["worktree_id"]``
    when a verb's own ``args`` happen to carry one under that key -- a
    best-effort inclusion, not a contract every verb must satisfy: this
    module's registry is deliberately verb-shape-agnostic, per the "Verb
    granularity note" above, so it cannot *require* a worktree identifier
    from an arbitrary verb's ``args``) before running it, so a later
    reconciliation pass has a durable trail of every mutation that happened
    outside the daemon's own mediation. Raises ``ValueError`` for an
    unregistered verb, mirroring :func:`compute`'s own validation.
    """
    _ensure_verb_modules_loaded()
    fn = _VERBS.get(verb)
    if fn is None:
        raise ValueError(f"tracking_write: unregistered verb {verb!r}")
    worktree_id = args.get("worktree_id") if isinstance(args, dict) else None
    logger.warning(
        "tracking_write: bypassing daemon for verb=%s worktree_id=%s reason=%s",
        verb,
        worktree_id if worktree_id is not None else "(unknown)",
        reason,
    )
    return fn(args)


def _send_tracking_write_request(
    host: str,
    port: int,
    token: str,
    *,
    key: str,
    payload: dict,
    request_deadline_s: float,
    client_id: str,
) -> dict:
    """Send one ``tracking_write`` request, distinguishing a **pre-send**
    failure (the TCP connect itself failed or timed out -- nothing could
    have run server-side, raises :class:`_PreSendFailure`) from a
    **post-send** failure (the connection was established and the request
    may have reached the daemon -- raises :class:`AmbiguousWriteOutcome`).

    Deliberately does not use ``wcs_client.request`` -- that function wraps
    the whole connect+send+receive sequence in a single
    ``except OSError: raise DaemonUnavailable``, which cannot make this
    distinction (2026-09-26 PR review finding). Speaks the identical wire
    protocol (``work_coalescing_singleton``'s versioned JSON-over-socket
    envelope) by construction, not by importing private helpers from it.
    """
    timeout = request_deadline_s + 1.0
    deadline = time.time() + request_deadline_s
    message: dict = {
        "action": "request",
        "kind": KIND,
        "key": key,
        "payload": payload,
        "deadline": deadline,
        "client_id": client_id,
    }
    envelope = dict(message, version=wcs_client.PROTOCOL_VERSION, token=token)
    body = json.dumps(envelope, separators=(",", ":")).encode("utf-8") + b"\n"

    try:
        sock = socket.create_connection((host, port), timeout=timeout)
    except OSError as exc:
        raise _PreSendFailure(str(exc)) from exc

    buf = b""
    try:
        with sock:
            sock.settimeout(timeout)
            sock.sendall(body)
            sock.shutdown(socket.SHUT_WR)
            while not buf.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buf += chunk
    except OSError as exc:
        raise AmbiguousWriteOutcome(
            "tracking_write request's connection was established but the "
            f"send/receive phase then failed -- outcome unknown: {exc}"
        ) from exc

    if not buf:
        raise AmbiguousWriteOutcome(
            "tracking_write request: empty response after a successful send"
        )
    try:
        response = json.loads(buf.decode("utf-8"))
    except ValueError as exc:
        raise AmbiguousWriteOutcome(
            f"tracking_write request: malformed response: {exc}"
        ) from exc
    if not isinstance(response, dict) or response.get("version") != wcs_client.PROTOCOL_VERSION:
        raise AmbiguousWriteOutcome("tracking_write request: malformed response")
    if response.get("fallback"):
        raise AmbiguousWriteOutcome(
            "tracking_write request: daemon reported fallback (its own "
            "deadline was exceeded server-side) -- the owner compute's "
            "outcome is still unknown"
        )
    result = response.get("result")
    if not isinstance(result, dict):
        raise AmbiguousWriteOutcome(
            "tracking_write request: malformed response (missing result)"
        )
    return result


def write_with_boot(
    *,
    read_lock_data: Callable[[], dict | None],
    ensure_monitor: Callable[[], bool] | None,
    payload: dict,
    fallback: Callable[[], dict],
    request_deadline_s: float = REQUEST_DEADLINE_S,
    boot_wait_s: float = BOOT_WAIT_S,
    poll_interval_s: float = 0.1,
    min_version: int = 1,
) -> dict:
    """Dial, boot-and-wait if no resident monitor currently publishes a
    tracking-write endpoint, then send one request.

    Structurally mirrors ``classify_daemon.classify_with_boot`` /
    ``worktree_status_daemon.status_with_boot``, but a write **cannot**
    share their "any miss runs fallback()" contract: ``CoalescingServer``
    does not cancel an already-accepted owner compute when a caller's own
    socket read times out, so a request that was actually *sent* and then
    failed is ambiguous -- the daemon may already be executing, or have
    already committed, the mutation. Blindly running the same-code
    fallback in that state risks double-applying a non-idempotent write.

    Two, and only two, cases are safe to run ``fallback()``: no endpoint
    was discoverable at all (even after the boot-wait), or the endpoint was
    found but the connection itself could never be established (a stale
    rendezvous entry left by a since-exited daemon) -- in both, nothing was
    ever sent anywhere. This now also includes an endpoint whose own
    published ``tracking_write_verbs`` capability list (see
    :func:`endpoint_from_rendezvous`) does not cover the requested verb
    (2026-09-27 PR review finding, copilot-extensions#3812) -- that
    endpoint is never even dialed, so it is exactly as safe as "no
    endpoint found." A further defense-in-depth case: a connection that DID
    succeed anyway (a benign race between the capability check and a
    connect, or an older client that skipped it) but whose response
    reports ``unsupported_verb`` -- ``compute()`` never actually executed
    anything in that case either. Anything else that fails *after* a
    connection was established raises :class:`AmbiguousWriteOutcome`
    instead of silently retrying (2026-09-26 PR review finding).

    Always mints its own fresh, unique coalescing key (``uuid4().hex``) --
    never accepts one from a caller -- so every entry point through this
    function structurally guarantees two writes can never coalesce onto one
    execution, regardless of what a caller passes.

    ``min_version`` is forwarded to :func:`endpoint_from_rendezvous` --
    see its own docstring for the same-name-but-enriched-payload skew case
    it closes.
    """
    started = time.time()
    verb = payload.get("verb") if isinstance(payload, dict) else None

    def _dial() -> tuple[str, int, str] | None:
        return endpoint_from_rendezvous(read_lock_data(), verb=verb, min_version=min_version)

    endpoint = _dial()
    if endpoint is None and ensure_monitor is not None:
        ensure_monitor()
        while endpoint is None and time.time() - started < boot_wait_s:
            time.sleep(poll_interval_s)
            endpoint = _dial()
    if endpoint is None:
        return fallback()

    host, port, token = endpoint
    key = uuid.uuid4().hex
    client_id = wcs_client.new_client_id()
    try:
        result = _send_tracking_write_request(
            host,
            port,
            token,
            key=key,
            payload=payload,
            request_deadline_s=request_deadline_s,
            client_id=client_id,
        )
        if isinstance(result, dict) and result.get("unsupported_verb"):
            return fallback()
        return result
    except _PreSendFailure:
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
    min_version: int = 1,
) -> dict:
    """The public entry point a migrated call site uses in place of calling
    its verb's function directly: try the resident daemon first (booting one
    on demand if none is reachable), falling back to :func:`run_direct`
    (same code, logged) only when nothing could ever have run server-side
    (see :func:`write_with_boot`'s own docstring for the exact three safe
    cases).

    Raises :class:`AmbiguousWriteOutcome` when a request *was* sent and then
    failed -- that state is never safe to auto-retry. This is a genuine
    exception a caller must handle deliberately, not a bug: it is the one
    case this module refuses to paper over with a same-code fallback.

    ``min_version`` names the minimum schema version this call site's
    ``args`` shape requires of a resident daemon's registered verb (see
    :data:`_VERB_VERSIONS`) -- pass the verb-owning module's own bumped
    version once it adds a field an older same-named verb would silently
    ignore. An endpoint below that version is treated exactly like one
    lacking the verb entirely: never dialed, same-code fallback used
    instead.
    """
    payload = {"verb": verb, "args": args}

    def _fallback() -> dict:
        return run_direct(verb, args, reason="no resident daemon reachable")

    return write_with_boot(
        read_lock_data=read_lock_data,
        ensure_monitor=ensure_monitor,
        payload=payload,
        fallback=_fallback,
        request_deadline_s=request_deadline_s,
        boot_wait_s=boot_wait_s,
        min_version=min_version,
    )
