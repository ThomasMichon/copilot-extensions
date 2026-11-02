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
_EXIT_CODE = 70  # EX_SOFTWARE -- distinguishable in logs/postmortems
_diagnostic_writer: BufferedSink | None = None
_diagnostics_initialized = False
_diagnostic_owner_lock = threading.Lock()


def _write_event(record: dict) -> None:
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
        on_dead: Callable[[str], None],
        on_shutdown_stuck: Callable[[str], None] | None = None,
        interval: float = _DEFAULT_INTERVAL,
        startup_grace: float = _DEFAULT_STARTUP_GRACE,
        serving_grace: float = _DEFAULT_SERVING_GRACE,
        shutdown_grace: float = _DEFAULT_SHUTDOWN_GRACE,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._is_started = is_started
        self._is_shutting_down = is_shutting_down
        self._is_stopped = is_stopped
        self._probe_serving = probe_serving
        self._on_dead = on_dead
        self._on_shutdown_stuck = on_shutdown_stuck or on_dead
        self._interval = interval
        self._startup_grace = startup_grace
        self._serving_grace = serving_grace
        self._shutdown_grace = shutdown_grace
        self._sleep = sleep
        self._monotonic = monotonic

    def run(self) -> None:
        """Supervise startup then serving; call ``on_dead`` once if wedged."""
        if not self._await_startup():
            return
        self._supervise_serving()

    def _await_startup(self) -> bool:
        """Return True once the server has started; False (terminal) otherwise.

        Fires ``on_dead`` if the server neither starts nor requests shutdown
        within the startup grace -- the "acquired the lock but never served"
        wedge. A graceful shutdown before startup is a clean no-op.
        """
        deadline = self._monotonic() + self._startup_grace
        while not self._is_started():
            if self._is_shutting_down():
                self._await_shutdown()
                return False
            if self._monotonic() >= deadline:
                self._on_dead(
                    f"startup did not complete within {self._startup_grace:.0f}s "
                    "while holding the singleton lock"
                )
                return False
            self._sleep(self._interval)
        return True

    def _supervise_serving(self) -> None:
        """After startup, exit if the serving probe fails past the grace window.

        A single failed probe is tolerated (transient loop stall); only a
        *sustained* failure -- ``serving_grace`` seconds with no successful probe
        and no graceful-shutdown request -- is treated as a wedge.
        """
        failing_since: float | None = None
        while True:
            self._sleep(self._interval)
            if self._is_shutting_down():
                self._await_shutdown()
                return
            if self._probe_serving():
                failing_since = None
                continue
            now = self._monotonic()
            if failing_since is None:
                failing_since = now
                continue
            if now - failing_since >= self._serving_grace:
                if self._is_shutting_down():
                    self._await_shutdown()
                    return
                self._on_dead(
                    f"stopped serving for {now - failing_since:.0f}s "
                    "without a graceful-shutdown request"
                )
                return

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
                return resp.status == 200
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
    on_dead: Callable[[str], None] | None = None,
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
