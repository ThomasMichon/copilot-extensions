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
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from work_coalescing_singleton import CoalescingServer

from .watch_contract import ALL_TRANSITIONS, DEFAULT_UNTIL, Baseline, PRSnapshot
from .watch_delivery import Deliveries
from .watch_notification import (
    ACKNOWLEDGED_NOTIFICATIONS, CALLBACK_TIMEOUT, default_notify, positive_seconds, validate_notify,
)
from .watch_registry import FiredEvent, WatchKey, WatchRegistry
from .watch_storage import (
    StateCommitUncertain,
    VersionedSubscriptions,
    _atomic_write_json as _atomic_write_json, lock_path, read_lock_data, read_subscriptions_state,
    state_dir, subscriptions_path, write_lock_data,
    write_subscriptions_state as _write_subscriptions_state,
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


def validate_versioned_entries(entries: list[dict[str, Any]]) -> None:
    if isinstance(entries, VersionedSubscriptions):
        WatchRegistry().restore_state(entries, strict=True)


def read_watch_state(
    root: Path | None = None, *, require_plain: bool = False,
) -> list[dict[str, Any]]:
    entries = (
        read_subscriptions_state(root, require_plain=True) if require_plain
        else read_subscriptions_state() if root is None else read_subscriptions_state(root)
    )
    validate_versioned_entries(entries)
    return entries


def write_subscriptions_state(entries: list[dict[str, Any]], root: Path | None = None) -> None:
    _write_subscriptions_state(entries, root, validate=validate_versioned_entries)


def rendezvous_fields(server: CoalescingServer) -> dict[str, Any]:
    rv = server.rendezvous()
    return {
        "watch_transport": rv["transport"],
        "watch_endpoint": rv["endpoint"],
        "watch_token": rv["token"],
        "watch_generation": rv["generation"],
        "pid": os.getpid(),
        "started_at": time.time(),
    }


def endpoint_from_rendezvous(data: dict[str, Any] | None) -> tuple[str, int, str] | None:
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
NativeFetch = Callable[[str, int, str], PRSnapshot]
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
        active: bool = True,
        state_root: Path | None = None,
        native_fetch: NativeFetch | None = None,
        effect_guard: Callable[[], None] | None = None,
        on_storage_refusal: Callable[[], None] | None = None,
    ) -> None:
        self._registry = WatchRegistry()
        self._fetch = fetch
        self._native_fetch = native_fetch
        self._effect_guard = effect_guard
        self._storage_refusal = on_storage_refusal
        self._notify = notify
        self._poll_interval = poll_interval
        self._idle_exit = idle_exit
        self._persist_enabled = persist
        self._state_root = state_root
        self._persistence_error: str | None = None
        self._observation_errors: dict[str, str] = {}
        self._persist_lock = threading.RLock()
        self._persistence_guard: Callable[[], None] | None = None
        self._guarded_storage = False
        self._pollers: dict[WatchKey, threading.Thread] = {}
        self._native_pollers: set[threading.Thread] = set()
        self._pollers_lock = threading.Lock()
        self._shutdown_event = threading.Event()
        self._lifecycle_shutdown_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._admission = threading.Condition()
        self._accepting_mutations = active
        self._active_mutations = 0
        self._state_loaded = active
        self._quiescent = not active
        if not active:
            self._shutdown_event.set()
        self._deliveries = Deliveries(
            self._registry, self._notify, self._persist, self._persist_lock, self._shutdown_event,
            before_notify=self._before_notify,
        )
        if persist and active:
            try:
                self._reattach_from_disk()
            except BaseException:
                from .watch_retirement import WatchRetirement

                with WatchRetirement(close_owner=self.close):
                    raise

    def _reattach_from_disk(self) -> None:
        """Reload every subscriber a prior generation persisted, and
        immediately resume polling each restored key -- no caller needs to
        re-``subscribe``; the reattach is transparent to them."""
        entries = self._read_state()
        if not entries:
            return
        self._registry.restore_state(entries, strict=isinstance(entries, VersionedSubscriptions))
        self._deliveries.resume()
        for key in self._registry.active_keys():
            self._ensure_poller(key)

    def _read_state(self) -> list[dict[str, Any]]:
        if self._guarded_storage:
            return read_watch_state(self._state_root, require_plain=True)
        return read_watch_state(self._state_root)

    def set_persistence_guard(
        self, guard: Callable[[], None] | None, *, timeout: float,
    ) -> None:
        """Retain preparation's rejection boundary until workers or the owner settle."""
        if not self._persist_lock.acquire(timeout=positive_seconds(timeout)):
            raise TimeoutError("PR watch persistence boundary is busy")
        try:
            self._persistence_guard = guard
            if guard is not None:
                self._guarded_storage = True
        finally:
            self._persist_lock.release()

    def fence_deliveries(self) -> None:
        self._deliveries.fence_legacy()

    def has_legacy_pending(self) -> bool:
        return self._deliveries.has_legacy_pending()

    def stop_guarded_work(self) -> None:
        self._guarded_storage = True
        self.stop_admission()
        self.fence_deliveries()

    def _refuse_guarded_work(self) -> None:
        self.stop_guarded_work()
        if self._storage_refusal is not None:
            self._storage_refusal()

    @staticmethod
    def _check_guard(guard: Callable[[], None] | None) -> None:
        if guard is not None and guard() is not None:
            raise PermissionError("watch source guard validation did not complete")

    def _before_notify(self) -> None:
        if self._persistence_guard is None and self._effect_guard is None:
            return
        with self._persist_lock:
            try:
                self._check_guard(self._effect_guard)
                self._check_guard(self._persistence_guard)
            except Exception:
                self._refuse_guarded_work()
                raise

    def _persist(
        self, entries: list[dict[str, Any]] | None = None, *,
        before_replace: Callable[[], None] | None = None,
    ) -> None:
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
                guard = self._persistence_guard
                self._check_guard(self._effect_guard)
                self._check_guard(guard)
                snapshot = self._registry.snapshot_state() if entries is None else entries
                if before_replace is not None or self._guarded_storage:
                    def check() -> None:
                        self._check_guard(self._effect_guard)
                        self._check_guard(guard)
                        if before_replace is not None:
                            before_replace()

                    _write_subscriptions_state(
                        snapshot, self._state_root, validate=validate_versioned_entries,
                        before_replace=check, create_parent=False, require_plain=True,
                    )
                elif self._state_root is None:
                    write_subscriptions_state(snapshot)
                else:
                    write_subscriptions_state(snapshot, self._state_root)
            except Exception as error:
                if self._guarded_storage:
                    self._persistence_error = "state_write_failed"
                    self._refuse_guarded_work()
                elif isinstance(error, OSError):
                    self._persistence_error = "state_write_failed"
                raise
            self._persistence_error = None

    # -- control-plane entrypoint -----------------------------------

    def compute(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind not in {"register", "unregister"}:
            return self._compute_request(kind, payload)
        with self._admission:
            if not self._accepting_mutations:
                return {"error": "watch owner is not accepting mutations", "retryable": True}
            self._active_mutations += 1
        try:
            return self._compute_request(kind, payload)
        finally:
            with self._admission:
                self._active_mutations -= 1
                self._admission.notify_all()

    def _compute_request(self, kind: str, payload: dict[str, Any]) -> dict[str, Any]:
        if kind == "register":
            return self._handle_register(payload)
        if kind == "unregister":
            if (
                type(payload.get("number")) is not int or payload["number"] <= 0
                or any(
                    not isinstance(payload.get(field), str) or not payload[field].strip()
                    for field in ("repo", "subscriber_id")
                )
                or not isinstance(payload.get("worktrees_command", ""), str)
            ):
                return {"error": "invalid cancellation target"}
            key = WatchKey(
                repo=str(payload.get("repo", "")), number=int(payload.get("number", 0)),
                worktrees_command=payload.get("worktrees_command", ""),
            )
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
                    if (entry["repo"], entry["number"], entry["subscriber_id"],
                        entry.get("worktrees_command", ""))
                    != (key.repo, key.number, identity, key.worktrees_command)
                ]
                try:
                    self._persist(entries)
                except StateCommitUncertain:
                    self._registry.unregister(key, identity, registration_id=sub.registration_id)
                    return {"error": "cancellation durability is uncertain", "ambiguous_cancellation": True}
                ok = self._registry.unregister(
                    key, identity, registration_id=sub.registration_id,
                )
            result: dict[str, Any] = {"unregistered": ok}
            if key.worktrees_command:
                result["in_flight"] = any(
                    item["registration_id"] == sub.registration_id
                    for item in self._deliveries.running()
                )
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
                "capabilities": self._capabilities(),
                "pending_delivery_count": len(self._registry.pending_events()),
                "persistence_error": self._persistence_error,
                "delivery_error": self._deliveries.start_error,
            }
        if kind == "shutdown":
            with self._persist_lock:
                if "restart_engine_generation" in payload:
                    from .watch_storage import WATCH_ENGINE_GENERATION

                    requester = payload["restart_engine_generation"]
                    if type(requester) is not int or requester < WATCH_ENGINE_GENERATION:
                        return {"error": "watch restart requires a compatible executor generation"}
                    if self._persist_enabled:
                        try:
                            self._read_state()
                        except (OSError, ValueError):
                            return {"error": "watch restart state admission refused"}
                with self._admission:
                    self._accepting_mutations = False
                    self._lifecycle_shutdown_event.set()
                    self._shutdown_event.set()
            return {"shutting_down": True}
        return {"error": f"unknown kind {kind!r}"}

    def _handle_register(self, payload: dict[str, Any]) -> dict[str, Any]:
        from .watch_provider import command_path, target_id

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
            command = payload.get("worktrees_command", "")
            if not isinstance(command, str):
                raise ValueError("invalid native command")
            native_target = ""
            if command:
                command = command_path(command)
                if self._native_fetch is None:
                    raise ValueError("watch owner lacks native snapshot support")
                if not acknowledged:
                    raise ValueError("native observation requires acknowledged notifications")
            continuous = payload.get("continuous", False)
            if type(continuous) is not bool or continuous and not (acknowledged and command):
                raise ValueError("continuous notifications require native acknowledged snapshots")
            key = WatchKey(repo=repo, number=number, worktrees_command=command)
            if acknowledged and payload.get("until") == []:
                raise ValueError("until must not be empty")
            until = payload.get("until") or DEFAULT_UNTIL
            if not isinstance(until, (list, tuple)) or any(t not in ALL_TRANSITIONS for t in until):
                raise ValueError("until must contain supported transitions")
            if continuous and not {"merged", "closed"} <= set(until):
                raise ValueError("continuous notifications must observe both terminal states")
            notify = validate_notify(payload.get("notify", {}), acknowledged)
            timeout = payload.get("timeout")
            if timeout is not None:
                timeout = positive_seconds(timeout)
        except (ValueError, TypeError, OverflowError):
            return {"error": "invalid watch registration or notification specification"}
        if command:
            try:
                native_fetch = self._native_fetch
                if native_fetch is None:
                    return {"error": "native snapshot capability is unavailable"}
                snap = native_fetch(repo, number, command)
                if snap.provider != "azure-devops" or (
                    snap.observation is None and not (snap.merged or snap.closed_unmerged)
                ):
                    return {"error": "native ADO observation capability is unavailable"}
                native_target = target_id(snap)
            except Exception:
                return {"error": "native snapshot capability is unavailable"}
        with self._persist_lock:
            if command and not self._registry.target_matches(key, native_target):
                return {"error": "native target changed; cancel the prior binding explicitly"}
            previous = self._registry.subscriber(key, identity)
            if continuous and previous is not None:
                if (
                    not previous.continuous or previous.until != tuple(until)
                    or previous.notify != notify or timeout is not None
                ):
                    return {"error": "existing continuous subscription differs; cancel it explicitly"}
                before = (previous.pending, previous.queued, previous.terminal_observed)
                terminal_event = self._registry.admit_terminal(key, identity, snap)
                if terminal_event is not None:
                    try:
                        self._persist()
                    except StateCommitUncertain:
                        self._deliveries.resume()
                        return {
                            "error": "registration durability is uncertain",
                            "ambiguous_registration": True,
                            "registration_id": previous.registration_id,
                        }
                    except OSError:
                        self._registry.restore_admission(key, previous, *before)
                        raise
                    self._deliveries.resume()
                return {
                    "registered": True, "registration_id": previous.registration_id,
                    "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
                    "snapshot_protocol": "agent-worktrees.pr-snapshot/v1", "continuous": True,
                }
            sub = self._registry.register(
                key, identity, until=tuple(until), notify=notify,
                timeout=timeout, acknowledged=acknowledged,
                continuous=continuous,
                target_id=native_target,
                baseline=(
                    Baseline.from_snapshot(snap)
                    if command and not (snap.merged or snap.closed_unmerged) else None
                ),
            )
            terminal_event = self._registry.admit_terminal(key, identity, snap) if command else None
            try:
                self._persist()
            except StateCommitUncertain:
                if terminal_event is not None:
                    self._deliveries.resume()
                self._ensure_poller(key)
                return {
                    "error": "registration durability is uncertain",
                    "ambiguous_registration": True, "registration_id": sub.registration_id,
                }
            except OSError:
                self._registry.restore_registration(key, sub, previous)
                raise
        if terminal_event is not None:
            self._deliveries.resume()
        self._ensure_poller(key)
        return (
            {"registered": True, "registration_id": sub.registration_id,
             "notification_protocol": ACKNOWLEDGED_NOTIFICATIONS,
             **({"snapshot_protocol": "agent-worktrees.pr-snapshot/v1",
                 "continuous": continuous} if command else {})}
            if acknowledged else {"registered": True}
        )

    def _capabilities(self) -> list[str]:
        from .watch_provider import CONTINUOUS_NOTIFICATIONS, NATIVE_SNAPSHOTS

        return [
            ACKNOWLEDGED_NOTIFICATIONS,
            *([NATIVE_SNAPSHOTS, CONTINUOUS_NOTIFICATIONS] if self._native_fetch else []),
        ]

    def wait_for_shutdown(self, poll_interval: float = 1.0) -> None:
        """Block the caller (``serve``'s own foreground loop) until a
        ``shutdown`` request arrives over the control plane, checking in
        short increments so a ``KeyboardInterrupt`` is still responsive."""
        while not self._lifecycle_shutdown_event.is_set():
            self._lifecycle_shutdown_event.wait(timeout=poll_interval)

    @property
    def quiescent(self) -> bool:
        return self._quiescent

    @property
    def effects_enabled(self) -> bool:
        with self._admission:
            return self._accepting_mutations and not self._shutdown_event.is_set()

    @contextmanager
    def _lifecycle_operation(self, timeout: float) -> Iterator[float]:
        deadline = time.monotonic() + positive_seconds(timeout)
        if not self._lifecycle_lock.acquire(timeout=max(0.0, deadline - time.monotonic())):
            raise TimeoutError("PR watch lifecycle operation is busy")
        try:
            yield deadline
        finally:
            self._lifecycle_lock.release()

    def _close_mutation_admission(self, deadline: float) -> None:
        with self._admission:
            self._accepting_mutations = False
            self._shutdown_event.set()
            while self._active_mutations:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("PR watch admitted mutation handlers did not drain")
                self._admission.wait(remaining)

    def stop_admission(self) -> None:
        with self._admission:
            self._accepting_mutations = False
            self._shutdown_event.set()

    @staticmethod
    def _check_lifecycle_deadline(deadline: float) -> None:
        if time.monotonic() >= deadline:
            raise TimeoutError("PR watch lifecycle deadline expired")

    def quiesce(
        self, timeout: float = _SHUTDOWN_REQUEST_DEADLINE_S, *,
        before_persist: Callable[[], None] | None = None,
        persist: bool = True,
        preserve_queued: bool = False,
    ) -> None:
        """Retain ownership until admitted writes and accepted callbacks have stopped."""
        with self._lifecycle_operation(timeout) as deadline:
            if self._lifecycle_shutdown_event.is_set():
                raise RuntimeError("PR watch owner is shutting down")
            self._quiescent = False
            self._close_mutation_admission(deadline)
            if preserve_queued:
                self._stop_workers(deadline, bounded=True, preserve_queued=True)
            else:
                self._stop_workers(deadline, bounded=True)
            self._check_lifecycle_deadline(deadline)
            if self._lifecycle_shutdown_event.is_set():
                raise RuntimeError("PR watch shutdown interrupted quiescence")
            if self._state_loaded and persist:
                if before_persist is None:
                    self._persist()
                else:
                    self._persist(before_replace=before_persist)
            self._check_lifecycle_deadline(deadline)
            self._quiescent = True

    def reload_state(
        self, timeout: float = _SHUTDOWN_REQUEST_DEADLINE_S, *,
        read_state: Callable[[], list[dict[str, Any]]] | None = None,
        retain_legacy: bool = False,
    ) -> None:
        """Read the final state without effects, after the owner acquires its writer lease."""
        with self._lifecycle_operation(timeout) as deadline:
            if not self._quiescent or self._lifecycle_shutdown_event.is_set():
                raise RuntimeError("PR watch state reload requires completed quiescence")
            if not self._persist_enabled:
                raise RuntimeError("PR watch state adoption requires durable persistence")
            self._state_loaded = False
            registry = WatchRegistry()
            registry.restore_state(
                self._read_state() if read_state is None else read_state(), strict=True,
            )
            self._check_lifecycle_deadline(deadline)
            self._registry = registry
            self._deliveries = Deliveries(
                registry, self._notify, self._persist, self._persist_lock, self._shutdown_event,
                before_notify=self._before_notify,
                retained_legacy=self._deliveries if retain_legacy else None,
            )
            with self._pollers_lock:
                self._pollers.clear()
            self._check_lifecycle_deadline(deadline)
            self._state_loaded = True

    def settle_workers(self, *, timeout: float, preserve_queued: bool) -> None:
        """Stop accepted work without committing persistence or a shutdown intent."""
        with self._lifecycle_operation(timeout) as deadline:
            self._quiescent = False
            self._close_mutation_admission(deadline)
            self._stop_workers(deadline, bounded=True, preserve_queued=preserve_queued)
            self._check_lifecycle_deadline(deadline)
            self._quiescent = True

    def resume_effects(
        self, timeout: float = _SHUTDOWN_REQUEST_DEADLINE_S, *,
        before_admit: Callable[[], None] | None = None,
    ) -> None:
        """Resume the retained registry only after the owner's transaction authorizes effects."""
        with self._lifecycle_operation(timeout) as deadline:
            if not self._state_loaded:
                raise RuntimeError("PR watch state has not been adopted")
            if self._lifecycle_shutdown_event.is_set():
                raise RuntimeError("PR watch owner is shutting down")
            if self._persist_enabled:
                self._read_state()
                self._check_lifecycle_deadline(deadline)
            with self._admission:
                self._accepting_mutations = False
            if self._shutdown_event.is_set():
                scheduler = self._deliveries.scheduler
                self._deliveries.changed.set()
                if scheduler is not None and scheduler.ident is not None:
                    scheduler.join(max(0.0, deadline - time.monotonic()))
                    if scheduler.is_alive():
                        raise TimeoutError("PR watch delivery scheduler did not stop")
            with self._admission:
                self._check_lifecycle_deadline(deadline)
                if self._lifecycle_shutdown_event.is_set():
                    raise RuntimeError("PR watch owner is shutting down")
                self._quiescent = False
                self._shutdown_event.clear()
            self._deliveries.resume()
            for key in self._registry.active_keys():
                self._check_lifecycle_deadline(deadline)
                self._ensure_poller(key)
            # Commit admission and guard removal against the same boundary as worker refusal.
            with self._persist_lock, self._admission:
                self._check_lifecycle_deadline(deadline)
                if self._lifecycle_shutdown_event.is_set() or self._shutdown_event.is_set():
                    raise RuntimeError("PR watch shutdown interrupted activation")
                self._check_guard(self._effect_guard)
                self._check_guard(self._persistence_guard)
                self._check_lifecycle_deadline(deadline)
                if before_admit is not None:
                    before_admit()
                self._accepting_mutations = True
                self._deliveries.unfence_legacy()

    def close(self, timeout: float = _SHUTDOWN_REQUEST_DEADLINE_S) -> None:
        """Stop polling and join in-flight ticks without discarding subscriptions."""
        positive_seconds(timeout)
        with self._admission:
            self._accepting_mutations = False
            self._lifecycle_shutdown_event.set()
            self._shutdown_event.set()
        with self._lifecycle_operation(timeout) as deadline:
            self._close_mutation_admission(deadline)
            self._stop_workers(deadline, bounded=False)

    def _stop_workers(
        self, deadline: float, *, bounded: bool, preserve_queued: bool = False,
    ) -> None:
        with self._pollers_lock:
            pollers = list(self._native_pollers)
        for thread in pollers:
            if thread.ident is not None:
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
        native = threading.enumerate()
        live = [thread.name for thread in pollers if thread in native]
        if preserve_queued:
            live.extend(self._deliveries.close(deadline, bounded=bounded, preserve_queued=True))
        else:
            live.extend(self._deliveries.close(deadline, bounded=bounded))
        with self._pollers_lock:
            self._native_pollers.difference_update(thread for thread in pollers if thread not in native)
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
            self._native_pollers.intersection_update(threading.enumerate())
            self._native_pollers.add(thread)
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
            fetch_revision = self._registry.revision()
            try:
                if key.worktrees_command:
                    if self._native_fetch is None:
                        raise ValueError("native snapshot capability is unavailable")
                    snap = self._native_fetch(key.repo, key.number, key.worktrees_command)
                    from .watch_provider import target_id

                    if (
                        snap.provider != "azure-devops"
                        or snap.observation is None and not (snap.merged or snap.closed_unmerged)
                        or not self._registry.target_matches(key, target_id(snap))
                    ):
                        raise ValueError("native target no longer matches its registration")
                else:
                    snap = self._fetch(key.repo, key.number)
                self._observation_errors.pop(str(key), None)
            except Exception:
                snap = None
                if key.worktrees_command:
                    self._observation_errors[str(key)] = "native_snapshot_unavailable"
            if self._shutdown_event.is_set():
                break
            try:
                with self._persist_lock:
                    before = self._registry.revision()
                    previous = self._registry.snapshot_state()
                    fired = (
                        self._registry.apply_snapshot(
                            key, snap, fetch_revision=fetch_revision,
                        ) if snap is not None
                        else self._registry.sweep_timeouts((key,))
                    )
                    if self._registry.revision() != before or self._persistence_error is not None:
                        try:
                            self._persist()
                        except StateCommitUncertain:
                            for event in fired:
                                if not event.subscriber.acknowledged:
                                    self._deliveries.legacy(event)
                            raise
                        except OSError:
                            self._registry.restore_state([
                                entry for entry in previous
                                if self._registry.subscriber(
                                    WatchKey(
                                        entry["repo"], entry["number"],
                                        entry.get("worktrees_command", ""),
                                    ), entry["subscriber_id"],
                                ) is None
                            ])
                            raise
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

    def status(self) -> dict[str, Any]:
        return {
            "subscribers": self._registry.status(),
            "pending_deliveries": self._deliveries.status(),
            "legacy_callback_history": self._deliveries.legacy_status(),
            "persistence_error": self._persistence_error,
            "delivery_error": self._deliveries.start_error,
            "capabilities": self._capabilities(),
            "registrations": [
                {
                    "repo": entry["repo"], "number": entry["number"],
                    "subscriber_id": entry["subscriber_id"],
                    "registration_id": entry.get("registration_id"),
                    "worktrees_command": entry.get("worktrees_command", ""),
                    "continuous": entry.get("continuous", False),
                }
                for entry in self._registry.snapshot_state()
            ],
            "in_flight": self._deliveries.running(),
            "observation_errors": dict(self._observation_errors),
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
