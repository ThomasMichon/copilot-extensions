"""The daemon side: a coalescing, ref-counted, idle-exiting TCP server.

Generalizes ``agent-worktrees``' ``hook_ipc.HookIpcServer`` (a dynamic-port,
loopback-only, token-authed ``ThreadingTCPServer`` for one request kind) into
a reusable shape covering any number of request kinds, plus the explicit
ref-count/linger/liveness-reap lifecycle that plugin does not yet need for
its single hook-decision kind.
"""

from __future__ import annotations

import json
import secrets
import socketserver
import threading
import time
from collections.abc import Callable

#: ``compute(kind, payload) -> result``. Raise to surface an error to every
#: joiner of the same in-flight (kind, key) execution.
Compute = Callable[[str, dict], dict]

PROTOCOL_VERSION = 1
_READ_TIMEOUT_S = 5.0
#: How long `close()` waits for confirmed entry into `serve_forever()`
#: before giving up on calling `shutdown()` at all (see `close()`'s own
#: docstring). Generous relative to ordinary OS thread-scheduling latency,
#: bounded so `close()` itself can never hang indefinitely on a thread that
#: never gets scheduled.
_CLOSE_SERVE_WAIT_S = 2.0


class Unavailable(Exception):
    """A request could not complete before its caller-supplied deadline."""


class _InFlight:
    __slots__ = ("error", "event", "result")

    def __init__(self) -> None:
        self.event = threading.Event()
        self.result: dict | None = None
        self.error: BaseException | None = None


class _Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, address, handler_cls, *, owner: CoalescingServer):
        self.owner = owner
        super().__init__(address, handler_cls)

    def service_actions(self) -> None:
        # Called by `serve_forever()` itself on every accept-loop iteration
        # -- the earliest point that genuinely proves the loop has entered
        # and is actually polling, unlike merely setting a flag from inside
        # the thread's target callable *before* calling `serve_forever()`
        # (Copilot review finding: that still leaves a -- much narrower,
        # but real -- window where a concurrent `close()` could observe
        # "running" and call `shutdown()` before the loop truly started).
        # Repeated calls are harmless (`Event.set()` is idempotent).
        self.owner._serve_running.set()

    def process_request(self, request, client_address) -> None:
        # copilot-extensions#3798: counted the instant a connection is
        # accepted, strictly BEFORE `ThreadingMixIn.process_request` spawns
        # the per-request handler thread -- the narrow accept-to-dispatch
        # gap a consumer's own subscriber-count-based busy predicate cannot
        # see (a connection can be accepted, and this counter incremented,
        # before that new thread's first line ever runs). Decremented in
        # `process_request_thread` below, only once the handler has fully
        # returned -- a strict superset of "inside `_Handler.handle()`".
        self.owner._on_request_accepted()
        try:
            super().process_request(request, client_address)
        except BaseException:
            # Copilot review finding: `ThreadingMixIn.process_request` only
            # creates and starts the handler thread -- if that itself fails
            # (e.g. `Thread.start()` raising under OS thread exhaustion),
            # `process_request_thread` below never runs, so its own
            # decrement would never fire, permanently inflating the count
            # and blocking every future shutdown-drain wait. Decrement here
            # before re-raising so a failed delegation is never counted as
            # "still handling a request".
            self.owner._on_request_finished()
            raise

    def process_request_thread(self, request, client_address) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.owner._on_request_finished()


class _Handler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        owner: CoalescingServer = self.server.owner  # type: ignore[attr-defined]
        try:
            self.request.settimeout(_READ_TIMEOUT_S)
            raw = self.rfile.readline(2 * 1024 * 1024)
            envelope = json.loads(raw.decode("utf-8"))
            if (
                not isinstance(envelope, dict)
                or envelope.get("version") != PROTOCOL_VERSION
                or not secrets.compare_digest(
                    str(envelope.get("token") or ""), owner.token
                )
            ):
                return

            action = str(envelope.get("action") or "request")
            client_id = str(envelope.get("client_id") or "")

            if action == "subscribe":
                if client_id:
                    owner.subscribe(client_id)
                self._write({"version": PROTOCOL_VERSION, "ok": True})
                return
            if action == "release":
                if client_id:
                    owner.release(client_id)
                self._write({"version": PROTOCOL_VERSION, "ok": True})
                return

            kind = str(envelope.get("kind") or "")
            key = str(envelope.get("key") or "")
            payload = envelope.get("payload")
            deadline = float(envelope.get("deadline") or 0)
            if not isinstance(payload, dict):
                payload = {}
            if client_id:
                # A fire-and-forget requester (no explicit subscribe) still
                # counts as a live subscriber while its request is in flight.
                owner.touch(client_id)
            if deadline <= time.time():
                raise Unavailable
            result = owner.handle_request(kind, key, payload, deadline)
            if not isinstance(result, dict):
                result = {}
            self._write({"version": PROTOCOL_VERSION, "result": result})
        except Unavailable:
            try:
                self._write({"version": PROTOCOL_VERSION, "fallback": True})
            except OSError:
                return
        except Exception:
            return

    def _write(self, obj: dict) -> None:
        self.wfile.write(json.dumps(obj, separators=(",", ":")).encode("utf-8") + b"\n")


class CoalescingServer:
    """Owns coalescing, subscriber ref-counting, linger, and liveness reaping.

    ``compute`` performs the actual work for one ``(kind, payload)`` request;
    the server ensures only one execution is in flight per ``(kind, key)`` at
    a time, joining late callers onto it rather than starting a second one.
    """

    def __init__(
        self,
        compute: Compute,
        *,
        linger_seconds: float = 5.0,
        subscriber_ttl: float = 30.0,
        reap_interval: float = 5.0,
        on_idle: Callable[[], None] | None = None,
    ):
        self.token = secrets.token_urlsafe(32)
        self.generation = secrets.token_hex(16)
        self._compute = compute
        self._linger_seconds = linger_seconds
        self._subscriber_ttl = subscriber_ttl
        self._reap_interval = reap_interval
        self._on_idle = on_idle

        self._lock = threading.Lock()
        self._subscribers: dict[str, float] = {}
        self._inflight: dict[tuple[str, str], _InFlight] = {}
        self._linger_timer: threading.Timer | None = None
        self._closed = False
        self._started = False
        # copilot-extensions#3798: incremented at `accept()` time (see
        # `_Server.process_request`), decremented only once the handler
        # thread has fully returned (`_Server.process_request_thread`) --
        # a strict superset of the subscriber-count-based busy window, so
        # a consumer's own shutdown-drain predicate can see a connection
        # that was accepted but has not yet reached `_Handler.handle()`.
        self._active_handlers = 0
        # Serializes the whole start()/close() state transition (Copilot
        # review finding): without this, close() could run concurrently
        # between a successful `Thread.start()` and its own following flag
        # assignment (e.g. `self._serve_thread.start()` succeeds, then
        # close() runs entirely -- sees `_serve_started` still False, skips
        # shutdown/join, closes the socket -- before start() resumes and
        # sets `_serve_started = True`, launches the reaper, and marks
        # `_started = True`), leaving a closed server with inconsistent
        # lifecycle flags despite this class's own docstrings describing
        # that race as handled. Holding this lock for each method's entire
        # body makes close() observe either the fully-pre-start or the
        # fully-post-start state, never a state in between.
        self._lifecycle_lock = threading.Lock()
        # Independently tracked per-thread start success (Copilot review
        # finding): the previous single `_started` flag flipped True
        # *before* either thread's own `.start()` call, so a `.start()`
        # failure (e.g. OS thread-creation exhaustion) left `_started=True`
        # even though `serve_forever()` was never entered -- `close()`'s
        # `self._server.shutdown()` then blocks forever waiting for a
        # `serve_forever()` loop that will never notice the shutdown
        # request, per `socketserver`'s own documented behavior. Each flag
        # is set only immediately after its own thread's `.start()` call
        # actually succeeds, so `close()` can precisely decide whether each
        # wait/join is safe rather than assuming both-or-neither.
        self._serve_started = False
        self._reap_started = False
        # `Thread.start()` returning successfully only proves the OS thread
        # object was created -- it does not prove the target callable has
        # actually begun executing yet, let alone that `serve_forever()`'s
        # own accept loop has truly started polling (Copilot review
        # finding). `close()` must not call `self._server.shutdown()` --
        # which blocks waiting for `serve_forever()`'s own exit -- until
        # that loop is *confirmed* running. Set only from
        # `_Server.service_actions()`, a hook `serve_forever()` itself
        # calls on every accept-loop iteration -- the earliest point that
        # genuinely proves the loop entered, not merely that the thread's
        # target callable was invoked.
        self._serve_running = threading.Event()
        self._reap_stop = threading.Event()

        self._server = _Server(("127.0.0.1", 0), _Handler, owner=self)
        self._serve_thread = threading.Thread(
            # A short poll_interval (vs. serve_forever's 0.5s default) so
            # service_actions() -- and therefore _serve_running -- fires
            # promptly after the loop starts, keeping close()'s own wait
            # for confirmed readiness fast on the ordinary path.
            target=self._server.serve_forever,
            args=(0.05,),
            name="work-coalescing-singleton",
            daemon=True,
        )
        self._reap_thread = threading.Thread(
            target=self._reap_loop,
            name="work-coalescing-singleton-reaper",
            daemon=True,
        )

    # -- lifecycle -------------------------------------------------------

    def start(self) -> None:
        """Start both background threads, or clean up fully on any failure.

        Exception-safe (Copilot review finding): if the reap thread's own
        ``.start()`` raises *after* the serve thread already launched
        successfully, this must not leave that now-accepting server/thread
        running with no owner -- a caller that (like several existing
        consumers, e.g. the resident status-monitor's classify/hook server
        startup) wraps ``start()`` in a bare ``try/except`` and discards the
        reference on failure would otherwise leak an accepting daemon
        indefinitely. ``start()`` therefore calls :meth:`close` itself
        before re-raising, so every caller gets an all-or-nothing outcome
        without needing to remember to clean up a partial failure.

        Serialized against :meth:`close` via ``_lifecycle_lock`` (see that
        attribute's own docstring) -- a concurrent ``close()`` can only ever
        observe this method's fully-pre-start or fully-post-start state,
        never a partial one.
        """
        try:
            with self._lifecycle_lock:
                if self._closed:
                    raise RuntimeError("cannot start an already-closed CoalescingServer")
                self._serve_thread.start()
                self._serve_started = True
                self._reap_thread.start()
                self._reap_started = True
                self._started = True
        except BaseException:
            # `with` has already released `_lifecycle_lock` by the time
            # this runs (context-manager exit happens before the enclosing
            # `except` body), so calling `close()` here -- which itself
            # acquires that same lock -- cannot deadlock.
            self.close()
            raise

    def rendezvous(self) -> dict:
        host, port = self._server.server_address
        return {
            "transport": "tcp",
            "endpoint": f"{host}:{port}",
            "token": self.token,
            "generation": self.generation,
        }

    def close(self) -> None:
        """Serialized against :meth:`start` via ``_lifecycle_lock`` (see
        that attribute's own docstring) -- this method's entire body runs
        atomically with respect to a concurrent ``start()``, so it can
        only ever observe the fully-pre-start or fully-post-start state.
        """
        with self._lifecycle_lock:
            self._closed = True
            self._reap_stop.set()
            with self._lock:
                self._cancel_linger_locked()
            # ``shutdown()`` blocks waiting for ``serve_forever()`` to notice --
            # forever, if that loop was never started (e.g. a unit test that
            # exercises ``handle_request``/refcounting directly, never calling
            # ``start()``, or a real ``start()`` whose serve-thread launch
            # itself failed -- see `_serve_started`'s own docstring above).
            # `_serve_started` alone only proves `Thread.start()` succeeded
            # (the OS thread object exists), not that `serve_forever()` has
            # actually begun executing (Copilot review finding) -- wait
            # (bounded) for `_serve_running` before ever calling `shutdown()`,
            # so a `close()` racing an in-flight `start()` never blocks on a
            # loop that has not truly started yet. If the loop still hasn't
            # confirmed running within that bound (a thread genuinely never
            # got scheduled -- pathological, but not this method's job to wait
            # out indefinitely), skip `shutdown()`: `server_close()` alone
            # still releases the bound socket, and the thread stays daemon-only
            # (never blocks process exit) if it does eventually run.
            # `_serve_running` is a one-way readiness event (Copilot review
            # finding): once `service_actions()` sets it, it stays set even
            # if the serve thread has since died on its own (a scenario a
            # test can force by directly clearing/replacing the target
            # callable, even though real `socketserver.serve_forever()`'s own
            # `try/finally` cannot exit without itself signalling shutdown
            # completion). Treat readiness as necessary but not sufficient --
            # also require the thread to still be alive right now, so a
            # `close()` that only ever observes a post-mortem serve thread
            # never calls `shutdown()` at all instead of trusting a stale
            # readiness signal.
            if (
                self._serve_started
                and self._serve_running.wait(timeout=_CLOSE_SERVE_WAIT_S)
                and self._serve_thread.is_alive()
            ):
                self._server.shutdown()
                # `shutdown()` only guarantees `serve_forever()`'s own while
                # loop noticed the request and its `finally` block ran --
                # join the thread here too (bounded) so `server_close()`
                # below never races an iteration still mid-flight inside
                # the selector (observed as a benign but noisy "operation
                # on something that is not a socket" `OSError` on a short
                # poll_interval).
                self._serve_thread.join(timeout=2)
            self._server.server_close()
            if self._serve_started:
                self._serve_thread.join(timeout=2)
            if self._reap_started:
                self._reap_thread.join(timeout=2)

    # -- subscriber ref-counting ------------------------------------------

    def subscribe(self, client_id: str) -> None:
        with self._lock:
            self._subscribers[client_id] = time.time()
            self._cancel_linger_locked()

    def touch(self, client_id: str) -> None:
        """Refresh a subscriber's liveness stamp (also covers a bare request)."""
        with self._lock:
            self._subscribers[client_id] = time.time()
            self._cancel_linger_locked()

    def release(self, client_id: str) -> None:
        with self._lock:
            self._subscribers.pop(client_id, None)
            self._maybe_start_linger_locked()

    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)

    def _on_request_accepted(self) -> None:
        with self._lock:
            self._active_handlers += 1

    def _on_request_finished(self) -> None:
        with self._lock:
            self._active_handlers -= 1

    def active_handler_count(self) -> int:
        """Connections accepted but not yet fully handled (copilot-
        extensions#3798) -- a strict superset of the accept-to-dispatch gap
        ``subscriber_count()``/an in-process compute counter alone cannot
        see. A consumer's own shutdown-drain busy predicate should OR this
        in alongside its existing checks."""
        with self._lock:
            return self._active_handlers

    def _cancel_linger_locked(self) -> None:
        if self._linger_timer is not None:
            self._linger_timer.cancel()
            self._linger_timer = None

    def _maybe_start_linger_locked(self) -> None:
        if self._subscribers or self._closed:
            return
        self._cancel_linger_locked()
        timer = threading.Timer(self._linger_seconds, self._on_linger_expired)
        timer.daemon = True
        self._linger_timer = timer
        timer.start()

    def _on_linger_expired(self) -> None:
        with self._lock:
            if self._subscribers or self._closed:
                return
            self._linger_timer = None
        if self._on_idle is not None:
            self._on_idle()

    def _reap_loop(self) -> None:
        while not self._reap_stop.wait(self._reap_interval):
            now = time.time()
            with self._lock:
                stale = [
                    cid
                    for cid, last in self._subscribers.items()
                    if now - last > self._subscriber_ttl
                ]
                for cid in stale:
                    del self._subscribers[cid]
                if stale:
                    self._maybe_start_linger_locked()

    # -- coalesced requests ------------------------------------------------

    def handle_request(self, kind: str, key: str, payload: dict, deadline: float) -> dict:
        """Run (or join) the coalesced execution for ``(kind, key)``.

        Raises ``Unavailable`` if ``deadline`` passes before a result (either
        this caller's own execution, or the in-flight one it joined) is
        ready.
        """
        map_key = (kind, key)
        with self._lock:
            inflight = self._inflight.get(map_key)
            is_owner = inflight is None
            if is_owner:
                inflight = _InFlight()
                self._inflight[map_key] = inflight

        if is_owner:
            try:
                inflight.result = self._compute(kind, payload)
            except BaseException as exc:
                inflight.error = exc
            finally:
                with self._lock:
                    self._inflight.pop(map_key, None)
                inflight.event.set()
            # Enforce the OWNER's own response deadline too (Copilot review
            # finding): without this, a slow `_compute` could finish after
            # this caller's own budget expired and still return a result
            # here -- unlike a joiner, which is already deadline-bound via
            # `event.wait(timeout=remaining)` below. The completed
            # `inflight.result`/`inflight.error` stay set on the shared
            # `_InFlight` regardless, so a joiner with a longer deadline
            # still gets the real result -- only this owner's own return
            # path is late.
            if deadline <= time.time():
                raise Unavailable
        else:
            remaining = deadline - time.time()
            if remaining <= 0 or not inflight.event.wait(timeout=remaining):
                raise Unavailable

        if inflight.error is not None:
            raise inflight.error
        assert inflight.result is not None  # noqa: S101 -- set whenever error is None
        return inflight.result
