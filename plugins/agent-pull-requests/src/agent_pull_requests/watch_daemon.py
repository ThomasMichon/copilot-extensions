"""The resident PR-watch daemon: one poll loop per ``(repo, number)``, any
number of subscribers multiplexed onto it, each notified independently when
its own requested transition fires.

Reuses ``work_coalescing_singleton.CoalescingServer`` for the control-plane
wire protocol (rendezvous, token auth, dedup/lifecycle) -- this module only
supplies the ``compute(kind, payload)`` callback and owns the actual
background polling + notification side, which is out of that library's
scope (it's a request/response coalescer, not an event-pushing scheduler).

Rendezvous is a JSON lock file under the plugin's durable state directory,
mirroring ``agent_worktrees.classify_daemon``'s own pattern so a future
shared "resident daemon" doc can describe both the same way.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable

from work_coalescing_singleton import CoalescingServer

from .watch_contract import ALL_TRANSITIONS, DEFAULT_UNTIL, PRSnapshot
from .watch_delivery import Deliveries
from .watch_notification import (
    ACKNOWLEDGED_NOTIFICATIONS, CALLBACK_TIMEOUT, default_notify, positive_seconds, validate_notify,
)
from .watch_registry import FiredEvent, WatchKey, WatchRegistry
from .watch_storage import (
    StateCommitUncertain,
    _atomic_write_json as _atomic_write_json, lock_path, read_lock_data, read_subscriptions_state,
    state_dir, subscriptions_path, write_lock_data, write_subscriptions_state,
)

_DEFAULT_POLL_INTERVAL_S = 30.0
#: How long a poller keeps running with zero subscribers before exiting --
#: generous relative to a register/unregister race, short relative to an
#: operator session.
_POLLER_IDLE_EXIT_S = 5.0
#: How long ``serve stop`` waits for a graceful shutdown before giving up
#: and reporting it as unresponsive (the caller decides what to do next --
#: this module never force-kills on the caller's behalf).
_SHUTDOWN_REQUEST_DEADLINE_S = CALLBACK_TIMEOUT + 5.0


def rendezvous_fields(server: CoalescingServer) -> dict:
    rv = server.rendezvous()
    return {
        "watch_transport": rv["transport"],
        "watch_endpoint": rv["endpoint"],
        "watch_token": rv["token"],
        "watch_generation": rv["generation"],
        "pid": os.getpid(),
        "started_at": time.time(),
    }


def endpoint_from_rendezvous(data: dict | None) -> tuple[str, int, str] | None:
    if not isinstance(data, dict):
        return None
    endpoint = data.get("watch_endpoint")
    token = data.get("watch_token")
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


#: ``fetch(repo, number) -> PRSnapshot``.
Fetch = Callable[[str, int], PRSnapshot]
Notify = Callable[[FiredEvent], int | None]


class WatchDaemon:
    """Owns the registry, one poller thread per active key, and dispatch.

    Every register/unregister/fire durably persists the subscriber set
    (``watch_registry.snapshot_state``/``write_subscriptions_state``) so a
    ``serve stop`` + ``serve restart`` -- the update/reattach path -- never
    silently drops a subscription that represents a *suspended* caller
    waiting on this daemon to wake it.
    ``persist=False`` is for tests that want a hermetic in-memory-only
    daemon with no real disk I/O.
    """

    def __init__(
        self,
        *,
        fetch: Fetch,
        notify: Notify = default_notify,
        poll_interval: float = _DEFAULT_POLL_INTERVAL_S,
        idle_exit: float = _POLLER_IDLE_EXIT_S,
        persist: bool = True,
    ) -> None:
        self._registry = WatchRegistry()
        self._fetch = fetch
        self._notify = notify
        self._poll_interval = poll_interval
        self._idle_exit = idle_exit
        self._persist_enabled = persist
        self._persistence_error: str | None = None
        self._persist_lock = threading.RLock()
        self._pollers: dict[WatchKey, threading.Thread] = {}
        self._pollers_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._deliveries = Deliveries(
            self._registry, self._notify, self._persist, self._persist_lock, self._shutdown_event,
        )
        if persist:
            self._reattach_from_disk()

    def _reattach_from_disk(self) -> None:
        """Reload every subscriber a prior generation persisted, and
        immediately resume polling each restored key -- no caller needs to
        re-``subscribe``; the reattach is transparent to them."""
        entries = read_subscriptions_state()
        if not entries:
            return
        self._registry.restore_state(entries)
        self._deliveries.resume()
        for key in self._registry.active_keys():
            self._ensure_poller(key)

    def _persist(self, entries: list[dict] | None = None) -> None:
        """Serialize every persist call against every other -- multiple
        poller threads (any of them, firing concurrently, plus register/
        unregister from a control-plane handler thread) can all call this
        for the same shared state file; on Windows an unserialized
        concurrent ``Path.replace()`` onto the same destination can raise
        ``PermissionError`` (file in use by the other writer's still-open
        temp handle), confirmed by a real flaky test failure this lock
        fixes."""
        if not self._persist_enabled:
            return
        with self._persist_lock:
            try:
                write_subscriptions_state(
                    self._registry.snapshot_state() if entries is None else entries,
                )
            except OSError:
                self._persistence_error = "state_write_failed"
                raise
            self._persistence_error = None

    # -- control-plane entrypoint -----------------------------------

    def compute(self, kind: str, payload: dict) -> dict:
        if kind == "register":
            return self._handle_register(payload)
        if kind == "unregister":
            key = WatchKey(repo=str(payload.get("repo", "")), number=int(payload.get("number", 0)))
            with self._persist_lock:
                identity = str(payload.get("subscriber_id", ""))
                sub = self._registry.subscriber(key, identity)
                expected = payload.get("registration_id")
                if "registration_id" in payload and (
                    not isinstance(expected, str) or not expected.strip()
                ):
                    return {"error": "invalid cancellation registration identity"}
                if sub is None or ("registration_id" in payload and expected != sub.registration_id):
                    return {"unregistered": False}
                entries = [
                    entry for entry in self._registry.snapshot_state()
                    if (entry["repo"], entry["number"], entry["subscriber_id"])
                    != (key.repo, key.number, identity)
                ]
                try:
                    self._persist(entries)
                except StateCommitUncertain:
                    self._registry.unregister(key, identity, registration_id=sub.registration_id)
                    return {"error": "cancellation durability is uncertain", "ambiguous_cancellation": True}
                ok = self._registry.unregister(
                    key, identity, registration_id=sub.registration_id,
                )
            result = {"unregistered": ok}
            if "registration_id" in payload:
                result.update(registration_id=sub.registration_id,
                              notification_protocol=ACKNOWLEDGED_NOTIFICATIONS)
            return result
        if kind == "status":
            return self.status()
        if kind == "health":
            return {
                "pid": os.getpid(),
                "subscriber_count": sum(len(v) for v in self._registry.status().values()),
                "active_keys": len(self._registry.active_keys()),
                "capabilities": [ACKNOWLEDGED_NOTIFICATIONS],
                "pending_delivery_count": len(self._registry.pending_events()),
                "persistence_error": self._persistence_error,
            }
        if kind == "shutdown":
            self._shutdown_event.set()
            return {"shutting_down": True}
        return {"error": f"unknown kind {kind!r}"}

    def _handle_register(self, payload: dict) -> dict:
        try:
            protocol = payload.get("notification_protocol")
            if "notification_protocol" in payload and protocol != ACKNOWLEDGED_NOTIFICATIONS:
                raise ValueError("unsupported notification protocol")
            acknowledged = protocol == ACKNOWLEDGED_NOTIFICATIONS
            if acknowledged and not self._persist_enabled:
                raise ValueError("acknowledged notifications require durable state")
            number = payload.get("number")
            repo, identity = payload.get("repo"), payload.get("subscriber_id")
            if (
                type(number) is not int or number <= 0
                or not isinstance(repo, str) or not repo.strip()
                or not isinstance(identity, str) or not identity.strip()
            ):
                raise ValueError("repo, positive number and subscriber_id are required")
            key = WatchKey(repo=repo, number=number)
            if acknowledged and payload.get("until") == []:
                raise ValueError("until must not be empty")
            until = payload.get("until") or DEFAULT_UNTIL
            if not isinstance(until, (list, tuple)) or any(t not in ALL_TRANSITIONS for t in until):
                raise ValueError("until must contain supported transitions")
            notify = validate_notify(payload.get("notify", {}), acknowledged)
            timeout = payload.get("timeout")
            if timeout is not None:
                timeout = positive_seconds(timeout)
        except (ValueError, TypeError, OverflowError):
            return {"error": "invalid watch registration or notification specification"}
        with self._persist_lock:
            previous = self._registry.subscriber(key, identity)
            sub = self._registry.register(
                key, identity, until=tuple(until), notify=notify,
                timeout=timeout, acknowledged=acknowledged,
            )
            try:
                self._persist()
            except StateCommitUncertain:
                self._ensure_poller(key)
                return {
                    "error": "registration durability is uncertain",
                    "ambiguous_registration": True, "registration_id": sub.registration_id,
                }
            except OSError:
                self._registry.restore_registration(key, sub, previous)
                raise
        self._ensure_poller(key)
        return (
            {"registered": True, "registration_id": sub.registration_id,
             "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS}
            if acknowledged else {"registered": True}
        )

    def wait_for_shutdown(self, poll_interval: float = 1.0) -> None:
        """Block the caller (``serve``'s own foreground loop) until a
        ``shutdown`` request arrives over the control plane, checking in
        short increments so a ``KeyboardInterrupt`` is still responsive."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=poll_interval)

    def close(self, timeout: float = _SHUTDOWN_REQUEST_DEADLINE_S) -> None:
        """Stop polling and join in-flight ticks without discarding subscriptions."""
        self._shutdown_event.set()
        deadline = time.monotonic() + timeout
        with self._pollers_lock:
            pollers = list(self._pollers.values())
        for thread in pollers:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        live = [thread.name for thread in pollers if thread.is_alive()]
        live.extend(self._deliveries.close(deadline))
        if live:
            raise TimeoutError(f"PR watch workers did not stop: {', '.join(live)}")

    # -- poller lifecycle ---------------------------------------------

    def _ensure_poller(self, key: WatchKey) -> None:
        with self._pollers_lock:
            if self._shutdown_event.is_set():
                return
            existing = self._pollers.get(key)
            if existing is not None and existing.is_alive():
                return
            thread = threading.Thread(
                target=self._poll_loop, args=(key,), name=f"pr-watch:{key}", daemon=True,
            )
            self._pollers[key] = thread
            thread.start()

    def _poll_loop(self, key: WatchKey) -> None:
        idle_since: float | None = None
        while not self._shutdown_event.is_set():
            if self._registry.subscriber_count(key) == 0:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= self._idle_exit:
                    break
                self._shutdown_event.wait(timeout=0.5)
                continue
            idle_since = None
            if not self._registry.watching_count(key):
                self._deliveries.resume()
                break
            try:
                snap = self._fetch(key.repo, key.number) if self._registry.watching_count(key) else None
            except Exception:
                snap = None
            if self._shutdown_event.is_set():
                break
            try:
                with self._persist_lock:
                    before = self._registry.revision()
                    fired = (
                        self._registry.apply_snapshot(key, snap) if snap is not None
                        else self._registry.sweep_timeouts((key,))
                    )
                    if self._registry.revision() != before or self._persistence_error is not None:
                        self._persist()
            except OSError:
                self._shutdown_event.wait(timeout=self._poll_interval)
                continue
            self._deliveries.resume()
            for event in fired:
                if event.subscriber.acknowledged:
                    continue
                self._deliveries.legacy(event)
            self._shutdown_event.wait(timeout=self._poll_interval)
        with self._pollers_lock:
            # Another register() may have raced in right as we decided to
            # exit -- only remove our own thread object, and only if it's
            # still the one on file (a fresh _ensure_poller already
            # replaced it otherwise).
            if self._pollers.get(key) is threading.current_thread():
                del self._pollers[key]
        if not self._shutdown_event.is_set() and self._registry.watching_count(key):
            self._ensure_poller(key)

    def status(self) -> dict:
        return {
            "subscribers": self._registry.status(),
            "pending_deliveries": self._deliveries.status(),
            "persistence_error": self._persistence_error,
        }


__all__ = [
    "Fetch",
    "Notify",
    "WatchDaemon",
    "default_notify",
    "endpoint_from_rendezvous",
    "lock_path",
    "read_lock_data",
    "read_subscriptions_state",
    "rendezvous_fields",
    "state_dir",
    "subscriptions_path",
    "write_lock_data",
    "write_subscriptions_state",
]
