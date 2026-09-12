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
        self._reap_stop = threading.Event()

        self._server = _Server(("127.0.0.1", 0), _Handler, owner=self)
        self._serve_thread = threading.Thread(
            target=self._server.serve_forever,
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
        self._started = True
        self._serve_thread.start()
        self._reap_thread.start()

    def rendezvous(self) -> dict:
        host, port = self._server.server_address
        return {
            "transport": "tcp",
            "endpoint": f"{host}:{port}",
            "token": self.token,
            "generation": self.generation,
        }

    def close(self) -> None:
        self._closed = True
        self._reap_stop.set()
        with self._lock:
            self._cancel_linger_locked()
        # ``shutdown()`` blocks waiting for ``serve_forever()`` to notice --
        # forever, if that loop was never started (e.g. a unit test that
        # exercises ``handle_request``/refcounting directly, never calling
        # ``start()``). Only wait on threads that actually began running.
        if self._started:
            self._server.shutdown()
        self._server.server_close()
        if self._started:
            self._serve_thread.join(timeout=2)
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
        else:
            remaining = deadline - time.time()
            if remaining <= 0 or not inflight.event.wait(timeout=remaining):
                raise Unavailable

        if inflight.error is not None:
            raise inflight.error
        assert inflight.result is not None  # noqa: S101 -- set whenever error is None
        return inflight.result
