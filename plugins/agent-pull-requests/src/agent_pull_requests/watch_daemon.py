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

import json
import os
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path

from agent_procutil import no_window_kwargs
from work_coalescing_singleton import CoalescingServer

from .watch_contract import DEFAULT_UNTIL, PRSnapshot
from .watch_registry import FiredEvent, WatchKey, WatchRegistry

_HOME_ENV = "AGENT_PULL_REQUESTS_HOME"
_LOCK_FILENAME = "watch-daemon.lock"
_STATE_FILENAME = "watch-subscriptions.json"
_DEFAULT_POLL_INTERVAL_S = 30.0
#: How long a poller keeps running with zero subscribers before exiting --
#: generous relative to a register/unregister race, short relative to an
#: operator session.
_POLLER_IDLE_EXIT_S = 5.0
#: How long ``serve stop`` waits for a graceful shutdown before giving up
#: and reporting it as unresponsive (the caller decides what to do next --
#: this module never force-kills on the caller's behalf).
_SHUTDOWN_REQUEST_DEADLINE_S = 5.0


def state_dir() -> Path:
    override = os.environ.get(_HOME_ENV, "").strip()
    if override:
        return Path(override)
    return Path.home() / ".agent-pull-requests"


def lock_path() -> Path:
    return state_dir() / _LOCK_FILENAME


def subscriptions_path() -> Path:
    """The durable subscriber dump -- survives a ``serve stop``/``serve
    restart`` (or an ungraceful crash: it's updated on every register/
    unregister/fire, not only at clean shutdown) so a newly-started daemon
    reattaches every still-pending subscription exactly where it left off."""
    return state_dir() / _STATE_FILENAME


def _atomic_write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def read_subscriptions_state() -> list[dict]:
    try:
        raw = subscriptions_path().read_text(encoding="utf-8")
    except OSError:
        return []
    try:
        data = json.loads(raw)
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def write_subscriptions_state(entries: list[dict]) -> None:
    _atomic_write_json(subscriptions_path(), entries)


def read_lock_data() -> dict | None:
    try:
        raw = lock_path().read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(raw)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


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


def write_lock_data(data: dict) -> None:
    _atomic_write_json(lock_path(), data)


#: ``fetch(repo, number) -> PRSnapshot``.
Fetch = Callable[[str, int], PRSnapshot]
#: ``notify(event) -> None``. Best-effort -- an exception here is logged by
#: the caller, never allowed to kill a poller thread.
Notify = Callable[[FiredEvent], None]


def default_notify(event: FiredEvent) -> None:
    """Fire-and-forget: run the subscriber's ``notify`` spec as a windowless
    subprocess, feeding the fired event as JSON on stdin.

    ``notify`` is a plain dict the registering caller supplies, with no
    agent-dispatch-specific knowledge baked in here --
    ``{"argv": [...]}`` is run exactly as given; this plugin never
    special-cases a particular consumer. A missing/malformed ``argv`` is a
    silent no-op (logged by the caller's own exception handling), since a
    subscriber that registered a bad notify spec should not crash the
    daemon that's serving every other subscriber too.
    """
    notify_spec = event.subscriber.notify
    argv = notify_spec.get("argv") if isinstance(notify_spec, dict) else None
    if not argv or not isinstance(argv, list):
        return
    payload = {
        "repo": event.key.repo,
        "number": event.key.number,
        "subscriber_id": event.subscriber.subscriber_id,
        "transitions": list(event.transitions),
        "timed_out": event.timed_out,
    }
    if event.snapshot is not None:
        payload["pr_state"] = event.snapshot.pr_state
        payload["merged"] = event.snapshot.merged
        payload["review_decision"] = event.snapshot.review_decision
        payload["mergeable"] = event.snapshot.mergeable
        payload["checks_state"] = event.snapshot.checks_state
    subprocess.run(  # noqa: S603 -- caller-supplied notify argv, by design
        [str(a) for a in argv],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
        **no_window_kwargs(),
    )


class WatchDaemon:
    """Owns the registry, one poller thread per active key, and dispatch.

    Every register/unregister/fire durably persists the subscriber set
    (``watch_registry.snapshot_state``/``write_subscriptions_state``) so a
    ``serve stop`` + ``serve restart`` -- the update/reattach path -- never
    silently drops a subscription that represents a *suspended* caller
    (e.g. an agent-dispatch task) waiting on this daemon to wake it.
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
        self._persist_lock = threading.Lock()
        self._pollers: dict[WatchKey, threading.Thread] = {}
        self._pollers_lock = threading.Lock()
        self._shutdown_event = threading.Event()
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
        for key in self._registry.active_keys():
            self._ensure_poller(key)

    def _persist(self) -> None:
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
            write_subscriptions_state(self._registry.snapshot_state())

    # -- control-plane entrypoint -----------------------------------

    def compute(self, kind: str, payload: dict) -> dict:
        if kind == "register":
            return self._handle_register(payload)
        if kind == "unregister":
            key = WatchKey(repo=str(payload.get("repo", "")), number=int(payload.get("number", 0)))
            ok = self._registry.unregister(key, str(payload.get("subscriber_id", "")))
            self._persist()
            return {"unregistered": ok}
        if kind == "status":
            return {"subscribers": self._registry.status()}
        if kind == "health":
            return {
                "pid": os.getpid(),
                "subscriber_count": sum(len(v) for v in self._registry.status().values()),
                "active_keys": len(self._registry.active_keys()),
            }
        if kind == "shutdown":
            self._shutdown_event.set()
            return {"shutting_down": True}
        return {"error": f"unknown kind {kind!r}"}

    def _handle_register(self, payload: dict) -> dict:
        key = WatchKey(repo=str(payload.get("repo", "")), number=int(payload.get("number", 0)))
        until = tuple(payload.get("until") or DEFAULT_UNTIL)
        notify = payload.get("notify") if isinstance(payload.get("notify"), dict) else {}
        timeout = payload.get("timeout")
        self._registry.register(
            key,
            str(payload.get("subscriber_id", "")),
            until=until,
            notify=notify,
            timeout=float(timeout) if timeout else None,
        )
        self._ensure_poller(key)
        self._persist()
        return {"registered": True}

    def wait_for_shutdown(self, poll_interval: float = 1.0) -> None:
        """Block the caller (``serve``'s own foreground loop) until a
        ``shutdown`` request arrives over the control plane, checking in
        short increments so a ``KeyboardInterrupt`` is still responsive."""
        while not self._shutdown_event.is_set():
            self._shutdown_event.wait(timeout=poll_interval)

    # -- poller lifecycle ---------------------------------------------

    def _ensure_poller(self, key: WatchKey) -> None:
        with self._pollers_lock:
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
        while True:
            if self._registry.subscriber_count(key) == 0:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since >= self._idle_exit:
                    break
                time.sleep(0.5)
                continue
            idle_since = None
            try:
                snap = self._fetch(key.repo, key.number)
            except Exception:
                time.sleep(self._poll_interval)
                continue
            fired = self._registry.apply_snapshot(key, snap)
            if fired:
                self._persist()
            for event in fired:
                try:
                    self._notify(event)
                except Exception:  # noqa: S110 -- one bad subscriber's notify
                    # must never stop this poller from serving the rest.
                    pass
            time.sleep(self._poll_interval)
        with self._pollers_lock:
            # Another register() may have raced in right as we decided to
            # exit -- only remove our own thread object, and only if it's
            # still the one on file (a fresh _ensure_poller already
            # replaced it otherwise).
            if self._pollers.get(key) is threading.current_thread():
                del self._pollers[key]

    def status(self) -> dict:
        return {"subscribers": self._registry.status()}


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
