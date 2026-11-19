"""Self-watchdog: force-exit a daemon that is alive but no longer serving (#166).

The daemon holds an OS singleton lock (``singleton.SingleInstance``) for its
entire life, and the kernel frees that lock the instant the process dies. So the
durable fix for a **wedged** daemon -- one that is still alive and still holding
the lock (thereby blocking the next ``start`` via the #129 duplicate guard) but
is no longer accepting connections / answering ``/health`` -- is simply to make
the process *exit* when it detects it is no longer serving. A fresh start then
acquires the now-free lock cleanly, instead of refusing forever against a zombie.

Two wedge shapes are covered:

* **Startup wedged** -- the process acquired the singleton lock but never
  finished coming up (bind/serve hung), so it holds the lock without ever
  listening.
* **Serving wedged** -- the server came up, then stopped serving (event-loop
  stall, accept loop died, socket closed) while ``server.run()`` never returned,
  so the normal ``finally`` that releases the lock never runs.

The supervisor runs on a **plain OS thread**, deliberately independent of the
asyncio event loop: a wedged daemon's loop may itself be stalled, and an
asyncio-based check could never fire in exactly the case we must catch. A
*graceful* shutdown (``server.should_exit`` set by idle-shutdown, ``drain``, or
an admin stop) is never treated as a wedge -- that path unwinds ``server.run()``
and releases the lock on its own.

Disable with ``AGENT_BRIDGE_WATCHDOG=0`` (operational safety valve).

On Windows the terminal action first schedules a durable replacement daemon. If
that cannot be scheduled (for example, a WMI launch timing out on a loaded
host), exiting would leave no Bridge at all, so the process keeps serving and the
watchdog retries the handoff with a doubling wait, hard-exiting only after
repeated declines with no healthy probe in between.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.request
from collections.abc import Callable
from functools import partial

from process_telemetry.buffer import BufferedSink

log = logging.getLogger("agent-bridge")

# Generous defaults chosen to avoid false positives: a transient blocking call on
# the event loop (a heavy GC sweep, a slow DB write) must not trip the watchdog.
_DEFAULT_INTERVAL = 15.0
_DEFAULT_STARTUP_GRACE = 120.0
_DEFAULT_SERVING_GRACE = 60.0
_DEFAULT_SHUTDOWN_GRACE = 120.0
_WINDOWS_INTERVAL = 5.0
_WINDOWS_SERVING_GRACE = 15.0
# Ceiling for the doubling wait between replacement attempts after ``on_dead``
# declined to exit (no durable replacement could be scheduled), and how many
# consecutive declines without a healthy probe are tolerated before hard exit.
_MAX_RETRY_GRACE = 300.0
_MAX_DECLINED_HANDOFFS = 4
_EXIT_CODE = 70  # EX_SOFTWARE -- distinguishable in logs/postmortems
_diagnostic_writer: BufferedSink | None = None
_diagnostics_initialized = False
_diagnostic_owner_lock = threading.Lock()


def _write_event(record: dict[str, object]) -> None:
    from .install_paths import effective_config_dir

    with (effective_config_dir() / "agent-bridge-watchdog.log").open(
        "a", encoding="utf-8",
    ) as stream:
        stream.write(json.dumps(record) + "\n")


def _start_event_writer() -> BufferedSink | None:
    """Initialize once while arming, never replace a closed or blocked writer."""
    global _diagnostic_writer, _diagnostics_initialized
    with _diagnostic_owner_lock:
        if not _diagnostics_initialized:
            _diagnostics_initialized = True
            try:
                _diagnostic_writer = BufferedSink(
                    _write_event, capacity=32, batch_size=1, max_bytes=16384,
                )
                _diagnostic_writer.start()
            except Exception:
                if _diagnostic_writer is not None:
                    _diagnostic_writer.close()
        return _diagnostic_writer


def _record_event(event: str, detail: str) -> None:
    """Offer bounded primitives; persistence never gates a watchdog decision."""
    writer = _diagnostic_writer
    if writer is None or type(event) is not str or type(detail) is not str:
        return
    writer({
        "time_ns": time.time_ns(), "pid": os.getpid(),
        "event": event[:64], "detail": detail[:512],
    })


def _run_watchdog(watchdog: ServingWatchdog, writer: BufferedSink | None) -> None:
    try:
        watchdog.run()
    finally:
        if writer is not None:
            writer.close()


class ServingWatchdog:
    """Decide, from injected signals, whether a daemon has wedged and must exit.

    The decision loop is pure with respect to its injected callables (server
    state, a serving probe, a clock, a sleep, and the terminal action), so it can
    be unit-tested without a real server, real sockets, or a real process exit.
    """

    def __init__(
        self,
        *,
        is_started: Callable[[], bool],
        is_shutting_down: Callable[[], bool],
        is_stopped: Callable[[], bool],
        probe_serving: Callable[[], bool],
        on_dead: Callable[[str], bool | None],
        on_shutdown_stuck: Callable[[str], object] | None = None,
        interval: float = _DEFAULT_INTERVAL,
        startup_grace: float = _DEFAULT_STARTUP_GRACE,
        serving_grace: float = _DEFAULT_SERVING_GRACE,
        shutdown_grace: float = _DEFAULT_SHUTDOWN_GRACE,
        max_declined: int = _MAX_DECLINED_HANDOFFS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._is_started = is_started
        self._is_shutting_down = is_shutting_down
        self._is_stopped = is_stopped
        self._probe_serving = probe_serving
        self._on_dead = on_dead
        self._on_shutdown_stuck: Callable[[str], object] = on_shutdown_stuck or on_dead
        self._interval = interval
        self._startup_grace = startup_grace
        self._serving_grace = serving_grace
        self._shutdown_grace = shutdown_grace
        self._max_declined = max(1, max_declined)
        self._sleep = sleep
        self._monotonic = monotonic

    def run(self) -> None:
        """Supervise startup then serving; call ``on_dead`` when wedged.

        ``on_dead`` normally does not return. A ``False`` return means it could
        not hand off to a replacement and kept this process alive (it may be
        merely slow), so supervision resumes and retries after a doubling wait
        instead of leaving nothing serving. After ``max_declined`` consecutive
        declines with no healthy probe in between, the hard-exit action runs so
        a truly wedged process cannot hold the singleton lock forever.
        """
        declined = self._await_startup()
        if declined is None:
            return
        self._supervise_serving(declined)

    def _hand_off(self, reason: str, declined: int) -> bool:
        """Offer ``reason`` to ``on_dead``; return True to keep supervising."""
        if self._on_dead(reason) is not False:
            return False
        if declined + 1 >= self._max_declined:
            if self._is_shutting_down():
                self._await_shutdown()
                return False
            self._on_shutdown_stuck(f"{reason}; no replacement could be scheduled")
            return False
        return True

    def _next_grace(self, grace: float, floor: float) -> float:
        return min(grace * 2, max(_MAX_RETRY_GRACE, floor))

    def _await_startup(self) -> int | None:
        """Return the decline count once started; None (terminal) otherwise.

        Fires ``on_dead`` if the server neither starts nor requests shutdown
        within the startup grace -- the "acquired the lock but never served"
        wedge. A graceful shutdown before startup is a clean no-op. Declines
        carry into serving supervision: only a healthy probe resets them.
        """
        grace = self._startup_grace
        deadline = self._monotonic() + grace
        declined = 0
        while not self._is_started():
            if self._is_shutting_down():
                self._await_shutdown()
                return None
            now = self._monotonic()
            if now >= deadline:
                if not self._hand_off(
                    f"startup did not complete within {self._startup_grace:.0f}s "
                    "while holding the singleton lock",
                    declined,
                ):
                    return None
                declined += 1
                grace = self._next_grace(grace, self._startup_grace)
                deadline = self._monotonic() + grace
            self._sleep(self._interval)
        return declined

    def _supervise_serving(self, declined: int = 0) -> None:
        """After startup, exit if the serving probe fails past the grace window.

        A single failed probe is tolerated (transient loop stall); only a
        *sustained* failure -- ``serving_grace`` seconds with no successful probe
        and no graceful-shutdown request -- is treated as a wedge. ``declined``
        carries startup declines; only a healthy probe resets it.
        """
        failing_since: float | None = None
        window_start = 0.0
        grace = self._serving_grace
        while True:
            self._sleep(self._interval)
            if self._is_shutting_down():
                self._await_shutdown()
                return
            if self._probe_serving():
                failing_since = None
                grace = self._serving_grace
                declined = 0
                continue
            now = self._monotonic()
            if failing_since is None:
                failing_since = window_start = now
                continue
            if now - window_start >= grace:
                if self._is_shutting_down():
                    self._await_shutdown()
                    return
                if not self._hand_off(
                    f"stopped serving for {now - failing_since:.0f}s "
                    "without a graceful-shutdown request",
                    declined,
                ):
                    return
                declined += 1
                window_start = self._monotonic()
                grace = self._next_grace(grace, self._serving_grace)

    def _await_shutdown(self) -> None:
        """Force-exit only when a requested graceful shutdown never finishes."""
        deadline = self._monotonic() + self._shutdown_grace
        while not self._is_stopped():
            if self._monotonic() >= deadline:
                self._on_shutdown_stuck(
                    "graceful shutdown did not complete within "
                    f"{self._shutdown_grace:.0f}s"
                )
                return
            self._sleep(min(self._interval, self._shutdown_grace))


def _watchdog_enabled() -> bool:
    return os.environ.get("AGENT_BRIDGE_WATCHDOG", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _probe_host(bind: str) -> str:
    """Loopback-reachable host to probe for a given bind address."""
    if bind in ("", "0.0.0.0", "::", "[::]"):  # noqa: S104 -- probe target, not a bind
        return "127.0.0.1"
    return bind


def _make_health_probe(bind: str, port: int, *, timeout: float = 2.0) -> Callable[[], bool]:
    """A serving probe that GETs the unauthenticated ``/health`` endpoint."""
    url = f"http://{_probe_host(bind)}:{port}/health"

    def _probe() -> bool:
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 -- fixed loopback http
                return bool(resp.status == 200)
        except Exception as error:
            _record_event("probe-failed", type(error).__name__)
            return False

    return _probe


def _force_exit(reason: str) -> None:
    """Offer the terminal reason, then hard-exit without logging/flush waits."""
    _record_event("exit", reason)
    # os._exit (not sys.exit): the event loop / interpreter shutdown may itself be
    # wedged, and we must guarantee the process actually dies to release the lock.
    os._exit(_EXIT_CODE)


def arm_serving_watchdog(
    server: object,
    *,
    bind: str,
    port: int,
    is_stopped: Callable[[], bool] = lambda: False,
    interval: float = _DEFAULT_INTERVAL,
    startup_grace: float = _DEFAULT_STARTUP_GRACE,
    serving_grace: float = _DEFAULT_SERVING_GRACE,
    on_dead: Callable[[str], bool | None] | None = None,
) -> threading.Thread | None:
    """Start the serving watchdog on a daemon thread; return it (or None).

    ``server`` is the ``uvicorn.Server`` -- its ``started`` / ``should_exit``
    flags are the startup and graceful-shutdown signals. Returns None when the
    watchdog is disabled via ``AGENT_BRIDGE_WATCHDOG=0``.
    """
    if not _watchdog_enabled():
        log.info("Self-watchdog disabled via AGENT_BRIDGE_WATCHDOG")
        return None

    writer = _start_event_writer()
    watchdog = ServingWatchdog(
        is_started=lambda: bool(getattr(server, "started", False)),
        is_shutting_down=lambda: bool(getattr(server, "should_exit", False)),
        is_stopped=is_stopped,
        probe_serving=_make_health_probe(bind, port),
        on_dead=_force_exit if on_dead is None else on_dead,
        on_shutdown_stuck=_force_exit,
        interval=interval,
        startup_grace=startup_grace,
        serving_grace=serving_grace,
    )
    thread = threading.Thread(
        target=partial(_run_watchdog, watchdog, writer),
        name="agent-bridge-watchdog", daemon=True,
    )
    try:
        thread.start()
    except Exception:
        if writer is not None:
            writer.close()
        raise
    log.info(
        "Self-watchdog armed: force-exit if wedged (startup_grace=%.0fs, "
        "serving_grace=%.0fs, interval=%.0fs)",
        startup_grace,
        serving_grace,
        interval,
    )
    return thread
