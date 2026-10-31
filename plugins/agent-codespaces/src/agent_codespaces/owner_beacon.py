"""The Connection Owner's liveness beacon, kept fresh and single.

Split from :mod:`connection_owner` (module-size cap): the singleton renewal a
running Owner performs, and the thread that keeps its beacon fresh whatever
its reconcile loop is doing.
"""

from __future__ import annotations

import asyncio
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

#: Run when this process's Owner has won the machine (before its "started"
#: record): process-local setup only the live Owner may do, such as opening
#: its rotating log.
_START_HOOKS: list[Callable[[], object]] = []


def on_owner_start(hook: Callable[[], object]) -> None:
    if hook not in _START_HOOKS:
        _START_HOOKS.append(hook)


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
        for hook in list(_START_HOOKS):
            try:
                hook()
            except Exception as exc:  # setup must never stop the Owner
                log.warning("Connection Owner start hook failed: %s", exc)
        log.info("Connection Owner started (pid %s, interval %ss)", os.getpid(), self._interval)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)
        log.info("Connection Owner stopping (pid %s%s)", os.getpid(),
                 ", yielded to another Owner" if self.yielded else "")

    async def wait_stopped(self) -> None:
        """Retain the beacon until its last native write has actually returned."""
        if self._thread.ident is not None:
            await owner_native_call(self._thread.join)


async def settle_owner_task(task: asyncio.Future[Any]) -> bool:
    """Finish admitted Owner work; report cancellation for its custodian to replay."""
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            if task.cancelled():
                break
            cancelled = True
        except Exception:
            break
    return cancelled


async def owner_native_call(function: Callable[..., Any], *args: Any) -> Any:
    """Settle value-only native work before propagating cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    cancelled = await settle_owner_task(task)
    try:
        result = task.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    if cancelled:
        raise asyncio.CancelledError
    return result


async def build_owner_channel(
    factory: Callable[..., Any], *args: Any, retain: Callable[[Any], None],
) -> Any:
    """Publish even a late constructor result to its Owner before replaying cancellation."""
    task = asyncio.create_task(asyncio.to_thread(factory, *args))
    cancelled = await settle_owner_task(task)
    try:
        channel = task.result()
    except Exception:
        if cancelled:
            raise asyncio.CancelledError from None
        raise
    retain(channel)
    if cancelled:
        raise asyncio.CancelledError
    return channel


async def stop_owner_channel(channel: Any) -> None:
    """Retain a channel's actual native cleanup, including after a bounded stop."""
    from ssh_manager import SupervisedRelayForward
    from ssh_manager.forward import LocalForward
    from ssh_manager.process import run_process_cleanup

    async def finish() -> None:
        transport = channel if isinstance(channel, SupervisedRelayForward) else getattr(
            channel, "_forward", None,
        )
        proc = getattr(transport, "_proc", None) if isinstance(
            transport, (SupervisedRelayForward, LocalForward),
        ) else None
        try:
            try:
                await channel.stop()
            except Exception:
                # A failed monitor can make stop raise before it reaches the
                # native process. Use that transport's existing teardown, never start.
                if proc is not None and proc.returncode is None:
                    if isinstance(transport, SupervisedRelayForward):
                        await transport._terminate(proc)
                    else:
                        await transport._kill(proc)
                raise
        finally:
            # Lower stop APIs may bound their process wait or clear their handle.
            # Keep the original process and its registered proxy cleanup in custody.
            try:
                if proc is not None:
                    await proc.wait()
                    await run_process_cleanup(proc)
            finally:
                tasks = tuple(getattr(channel, "_cleanup_tasks", ()))
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)

    task = asyncio.create_task(finish())
    cancelled = await settle_owner_task(task)
    task.result()
    if cancelled:
        raise asyncio.CancelledError
