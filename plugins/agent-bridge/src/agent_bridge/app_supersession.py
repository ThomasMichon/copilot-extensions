"""Native predecessor retirement after a successor publishes its generation."""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import FastAPI

log = logging.getLogger("agent-bridge")


async def retire_previous_daemon(
    app: FastAPI,
    previous,
    successor,
    *,
    make_client,
    drain_timeout: float,
    retirement_timeout: float,
    initial_recovery_task: asyncio.Task | None,
    recover_owned_cold: Callable[..., Awaitable[int]],
    run_in_daemon_thread: Callable[..., Awaitable[Any]],
    drain_poll: float,
    owns_generation: Callable[[Any, Any], bool],
    same_daemon: Callable[[Any, Any], bool],
) -> None:
    """Retire a predecessor with native exit evidence before owned cold recovery."""
    from zdd import process_exit

    reference = None
    if previous.pid and previous.process_start_time:
        try:
            reference = process_exit.open_process(
                previous.pid, previous.process_start_time, allow_exited=True,
            )
        except ProcessLookupError:
            try:
                exited = process_exit.observe_original_exit(previous.pid, previous.process_start_time)
            except (OSError, ValueError, NotImplementedError):
                log.warning("Predecessor original-exit evidence is unavailable; recovery fenced", exc_info=True)
                return
            if exited:
                await recover_owned_cold(
                    app, successor, initial_recovery_task=initial_recovery_task,
                    predecessor_exited=True,
                )
            return
        except (OSError, ValueError, NotImplementedError):
            log.warning("Predecessor native identity could not be pinned; retirement refused", exc_info=True)
            return
    try:
        if reference is not None and reference.wait(0):
            await recover_owned_cold(
                app, successor, initial_recovery_task=initial_recovery_task,
                predecessor_exited=True,
            )
            return
        await _retire_previous_daemon_control(
            app, previous, successor, make_client=make_client, drain_timeout=drain_timeout,
            retirement_timeout=retirement_timeout, previous_exit=reference,
            initial_recovery_task=initial_recovery_task,
            recover_owned_cold=recover_owned_cold,
            run_in_daemon_thread=run_in_daemon_thread, drain_poll=drain_poll,
            owns_generation=owns_generation, same_daemon=same_daemon,
        )
    finally:
        if reference is not None:
            reference.close()


async def _retire_previous_daemon_control(
    app: FastAPI, previous, successor, *, make_client, drain_timeout: float,
    retirement_timeout: float, previous_exit, initial_recovery_task: asyncio.Task | None,
    recover_owned_cold: Callable[..., Awaitable[int]],
    run_in_daemon_thread: Callable[..., Awaitable[Any]], drain_poll: float,
    owns_generation: Callable[[Any, Any], bool],
    same_daemon: Callable[[Any, Any], bool],
) -> None:
    """Drain and gracefully retire the predecessor atomically demoted at start."""
    from .client import BridgeClient, BridgeClientError, BridgeConnectionError
    from .config import config_dir
    from zdd import routing
    from zdd.routing import Endpoint

    def read_active():
        table = routing.read_table(config_dir())
        raw = table.get("active") if isinstance(table, dict) else None
        return Endpoint.from_dict(raw) if isinstance(raw, dict) else None

    active = await asyncio.to_thread(read_active)
    if not owns_generation(active, successor):
        log.info(
            "Skipping predecessor retirement because generation %d is no "
            "longer active", successor.generation,
        )
        return

    if make_client is None:
        make_client = lambda endpoint: BridgeClient(
            endpoint.base_url,
            app.state.auth_token,
            timeout=max(1, int(drain_timeout + 30)),
            connect_grace=0,
        )
    client = make_client(previous)
    drain = client.drain
    if previous_exit is None:
        # Legacy routing without birth evidence cannot authorize a cold
        # transition whose predecessor exit we would be unable to confirm.
        drain = getattr(client, "drain_gracefully", drain)

    async def release_drain(message: str) -> None:
        try:
            await run_in_daemon_thread(client.undrain)
        except (BridgeClientError, BridgeConnectionError, OSError):
            log.warning(message, exc_info=True)

    try:
        result = await run_in_daemon_thread(
            functools.partial(
                drain,
                timeout=drain_timeout,
                poll=drain_poll,
                force=False,
                source="startup-supersession",
                reason="superseded by a normal daemon start",
            )
        )
    except (BridgeClientError, BridgeConnectionError, OSError):
        log.warning(
            "Could not drain superseded predecessor at %s; generation "
            "self-retire remains the backstop",
            previous.base_url,
            exc_info=True,
        )
        await release_drain(
            "Failed to release predecessor drain after drain request failure"
        )
        return

    if not result.get("drained", False):
        log.warning(
            "Superseded predecessor at %s did not reach a safe drain boundary "
            "within %.0fs; leaving it alive",
            previous.base_url,
            drain_timeout,
        )
        await release_drain(
            "Failed to release predecessor drain after drain timeout"
        )
        return
    cold = result.get("mode") == "incompatible-contract-cold"
    if cold and previous_exit is None:
        log.error("Cold predecessor exit cannot be verified; shutdown/recovery refused")
        return
    manager = getattr(app.state, "session_manager", None)
    from . import cold_outages

    pending = manager is not None and cold_outages.available(manager.db) and bool(
        manager.db.execute_read("SELECT 1 FROM cold_outages LIMIT 1"),
    )
    if previous_exit is not None and (cold or pending):
        app.state.cold_predecessor = previous

    active = await asyncio.to_thread(read_active)
    if same_daemon(active, previous):
        log.warning(
            "Superseded predecessor became active again while draining; "
            "releasing its drain gate instead of shutting it down"
        )
        await release_drain("Failed to release restored predecessor drain")
        return
    if active is None or active.generation < successor.generation:
        log.warning(
            "Routing ownership became ambiguous while draining the predecessor; "
            "releasing its drain gate instead of shutting it down"
        )
        await release_drain("Failed to release predecessor drain")
        return

    shutdown_failed = False
    try:
        reply = await run_in_daemon_thread(client.shutdown)
        shutdown_failed = not isinstance(reply, dict) or reply.get("shutting_down") is not True
    except (BridgeClientError, BridgeConnectionError, OSError):
        log.warning(
            "Could not request graceful shutdown of superseded predecessor at %s",
            previous.base_url,
            exc_info=True,
        )
        shutdown_failed = True
    if previous_exit is not None and (cold or pending):
        if not await asyncio.to_thread(previous_exit.wait, retirement_timeout):
            log.error("Predecessor native exit is unconfirmed; owned cold recovery remains fenced")
            if shutdown_failed:
                await release_drain("Failed to release predecessor drain after unconfirmed shutdown")
            return
        await recover_owned_cold(
            app, successor, initial_recovery_task=initial_recovery_task,
            predecessor_exited=True,
        )
    elif shutdown_failed:
        await release_drain("Failed to release predecessor drain after shutdown request failure")
