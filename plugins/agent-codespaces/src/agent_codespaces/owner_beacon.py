"""The Connection Owner's liveness beacon, kept fresh and single.

Split from :mod:`connection_owner` (module-size cap): the singleton renewal a
running Owner performs, and the thread that keeps its beacon fresh whatever
its reconcile loop is doing.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterable
from typing import Any

from .connection_owner import (
    _LIVE_STALE_FLOOR,
    _bridge_forwards_of,
    _live_snapshot,
    _owner_lock,
    _write_liveness,
)

log = logging.getLogger("agent-codespaces")


def renew_owner_singleton(
    interval: float,
    active: Iterable[str] | None = None,
    bridge_forwards: Iterable[str] | None = None,
) -> bool:
    """Refresh this daemon's beacon -- or report that another Owner holds the machine.

    Freshness is the only liveness signal where a pid can't be checked
    (Windows), so a beacon that lapsed during one slow cycle lets a second
    Owner start. Without this check both keep rewriting the beacon and both
    keep competing forwards into every CodeSpace, indefinitely. Each cycle,
    under the registry lock, an Owner that finds another's fresh beacon
    yields (False) instead of overwriting it."""
    with _owner_lock():
        live = _live_snapshot()
        if live is not None and live.pid != os.getpid():
            return False
        _write_liveness(interval, active=active, bridge_forwards=bridge_forwards)
        return True


class BeaconKeeper:
    """Refresh the Owner's beacon on its own timer, on a thread.

    A reconcile cycle can take minutes (an SSH probe per CodeSpace, relay
    starts that wait for their bind, synchronous ``gh`` calls that block the
    event loop). Refreshed only between cycles, the beacon would go stale, and
    the next tenant would spawn a second Owner while this one still runs. On a
    thread it stays fresh whatever the loop is doing. When it finds another
    Owner's fresh beacon it stops writing and asks the loop to stand down.
    """

    def __init__(self, owner: Any, interval: float, on_yield: Callable[[], None]) -> None:
        import threading

        self._owner = owner
        self._interval = interval
        self._period = max(1.0, min(interval, _LIVE_STALE_FLOOR / 3))
        self._on_yield = on_yield
        self._stop = threading.Event()
        self.yielded = False
        self._thread = threading.Thread(target=self._run, name="owner-beacon", daemon=True)

    def _snapshot(self) -> tuple[set[str], dict[str, int]]:
        for _ in range(3):  # the loop may be changing these dicts right now
            try:
                return set(self._owner.active_codespaces()), dict(_bridge_forwards_of(self._owner))
            except RuntimeError:
                continue
        return set(), {}

    def beat(self) -> bool:
        active, bridges = self._snapshot()
        if renew_owner_singleton(self._interval, active=active, bridge_forwards=bridges):
            return True
        self.yielded = True
        return False

    def _run(self) -> None:
        while not self._stop.wait(self._period):
            try:
                if not self.beat():
                    log.warning(
                        "Connection Owner: another Owner holds this machine now; "
                        "stopping this one and its forwards."
                    )
                    self._on_yield()
                    return
            except Exception as exc:  # a beacon write must never kill the thread
                log.debug("Connection Owner beacon refresh failed: %s", exc)

    def start(self) -> None:
        # Only the Owner that won the machine starts a keeper: its lifecycle
        # records never count a losing concurrent start as a restart.
        log.info("Connection Owner started (pid %s, interval %ss)", os.getpid(), self._interval)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("Connection Owner stopping (pid %s%s)", os.getpid(),
                 ", yielded to another Owner" if self.yielded else "")
