"""FastAPI coordinator -- the single-writer HTTP front for the task queue.

The coordinator is the *only* writer to the SQLite queue; every other
participant (agents, producers, the CLI) is an HTTP client. This keeps the
atomic-claim guarantees of :class:`~agent_dispatch.queue.TaskQueue` intact with
no cross-host locking. SSE event emission and agent-bridge integration land in a
later slice; this module is the task CRUD + claim/lease API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from . import __version__
from . import coordinator_loops as _coordinator_loops
from .config import DEFAULT_HANDOFF_FALLBACK_GRACE, DEFAULT_ORPHAN_GRACE
from .coordinator_auth import _make_auth
from .coordinator_directory import register_directory_routes
from .coordinator_loops import (
    DrainGate,
    LoopHealth,
    _GOVERNANCE_BACKOFF_SECONDS,
    _SELF_RETIRE_FORCE_EXIT_DEFAULT_S,
    _abandoned_passive_reap_settings,
    _attempt_handoff_fallback,
    _find_stale_handoff_tasks,
    _force_exit_after_should_exit,
    _gc_loop as _loops_gc_loop,
    _handoff_fallback_loop as _loops_handoff_fallback_loop,
    _orphan_reap_loop as _loops_orphan_reap_loop,
    _reconcile_handoff_fallback,
    _resolve_owner_session_id,
    _run_supervised_cycle,
    _worktree_status_relay_loop as _loops_worktree_status_relay_loop,
    _self_retire_force_exit_seconds,
    _self_retire_settings,
    _self_update_settings,
    _spawn_self_deploy,
)
from .coordinator_registries import register_registry_routes
from .coordinator_spawn import register_spawn_routes
from .coordinator_status import _slot_descriptor, register_status_routes
from .coordinator_tasks import register_task_routes
from .coordinator_worktree_status import register_worktree_status_routes
from .events import EventBus
from .loop_governance import LoopGovernance
from .queue import TaskQueue
from .satellites import FleetDirectory
from .worktree_status_relay import WorktreeStatusRelayStore

log = logging.getLogger("agent-dispatch.coordinator")

__all__ = [
    "DrainGate",
    "LoopHealth",
    "_GOVERNANCE_BACKOFF_SECONDS",
    "_SELF_RETIRE_FORCE_EXIT_DEFAULT_S",
    "_abandoned_passive_reap_settings",
    "_attempt_handoff_fallback",
    "_find_stale_handoff_tasks",
    "_force_exit_after_should_exit",
    "_gc_loop",
    "_handoff_fallback_loop",
    "_orphan_reap_loop",
    "_worktree_status_relay_loop",
    "_reconcile_handoff_fallback",
    "_resolve_owner_session_id",
    "_run_supervised_cycle",
    "_self_retire_force_exit_seconds",
    "_self_retire_settings",
    "_self_update_settings",
    "_slot_descriptor",
    "create_app",
]


async def _gc_loop(*args, **kwargs):
    return await _loops_gc_loop(
        *args,
        **kwargs,
        run_supervised_cycle=_run_supervised_cycle,
        governance_backoff=_governance_backoff,
    )


async def _orphan_reap_loop(*args, **kwargs):
    return await _loops_orphan_reap_loop(
        *args,
        **kwargs,
        run_supervised_cycle=_run_supervised_cycle,
        governance_backoff=_governance_backoff,
    )


async def _handoff_fallback_loop(*args, **kwargs):
    return await _loops_handoff_fallback_loop(
        *args,
        **kwargs,
        run_supervised_cycle=_run_supervised_cycle,
        governance_backoff=_governance_backoff,
    )


async def _worktree_status_relay_loop(*args, **kwargs):
    return await _loops_worktree_status_relay_loop(
        *args,
        **kwargs,
        run_supervised_cycle=_run_supervised_cycle,
        governance_backoff=_governance_backoff,
    )


async def _governance_backoff(*args, **kwargs):
    return await _coordinator_loops._governance_backoff(
        *args,
        **kwargs,
        backoff_seconds=_GOVERNANCE_BACKOFF_SECONDS,
    )


def create_app(
    queue: TaskQueue,
    *,
    token: str | None = None,
    control_token: str | None = None,
    sweep_interval: float = 0.0,
    orphan_grace: float = DEFAULT_ORPHAN_GRACE,
    handoff_fallback_enabled: bool = False,
    handoff_fallback_grace: float = DEFAULT_HANDOFF_FALLBACK_GRACE,
    enable_mcp: bool = True,
    wake_interval: float = 0.0,
    wake_deliver: Callable[[str, str, str, str | None, str], bool] | None = None,
    wake_is_active: Callable[[], bool] | None = None,
    wake_max_attempts: int = 8,
    wake_retry_base: float = 1.0,
) -> FastAPI:
    """Build the coordinator app over an existing :class:`TaskQueue`.

    When ``sweep_interval > 0`` the coordinator runs a background lease-recovery
    sweep every ``sweep_interval`` seconds so a crashed worker's held task
    automatically returns to ``queued`` without a manual ``recover`` call.

    ``handoff_fallback_enabled`` (opt-in; see ``AGENT_DISPATCH_HANDOFF_FALLBACK``)
    arms the handoff-fallback reconciliation loop on the same cadence -- off by
    default, as this is the coordinator autonomously spawning a real Copilot
    process on a time heuristic.

    When ``enable_mcp`` is set and the ``mcp`` extra is installed, a
    coordinator-hosted MCP endpoint is mounted at ``/mcp`` (identity via
    ``X-Agent-Machine``/``X-Agent-Worktree`` headers or explicit tool args).
    """
    if (
        token is not None
        and control_token is not None
        and secrets.compare_digest(token, control_token)
    ):
        raise ValueError(
            "AGENT_DISPATCH_CONTROL_TOKEN must differ from AGENT_DISPATCH_TOKEN"
        )
    bus = EventBus()
    directory = FleetDirectory()
    relay = WorktreeStatusRelayStore(
        Path(queue.db_path).parent / "worktree-status-relay.sqlite3"
    )

    coordinator_mcp = None
    mcp_app = None
    if enable_mcp:
        try:
            from .mcp_http import bearer_guard_middleware, build_coordinator_mcp

            coordinator_mcp = build_coordinator_mcp(
                queue, bus, control_token=control_token
            )
            # mcp 2.0: transport options moved off the constructor onto the app
            # factory. streamable_http_path="/" so mounting at "/mcp" yields the
            # endpoint at "/mcp" (not "/mcp/mcp").
            mcp_app = coordinator_mcp.streamable_http_app(
                stateless_http=True, streamable_http_path="/"
            )
            if token:
                mcp_app.add_middleware(
                    bearer_guard_middleware(token, control_token)
                )
        except ImportError:
            log.warning("mcp extra not installed; coordinator /mcp endpoint disabled")
            coordinator_mcp = None
            mcp_app = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        loop = asyncio.get_running_loop()
        bus.bind_loop(loop)
        from .wake import drain_wake_outbox

        governance = LoopGovernance()
        wake_signal: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        worktree_status_signal: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        if wake_interval > 0:
            def _signal_wake() -> None:
                if wake_signal.empty():
                    wake_signal.put_nowait(None)

            queue.set_wake_notifier(
                lambda: loop.call_soon_threadsafe(_signal_wake)
            )
        def _signal_worktree_status() -> None:
            if worktree_status_signal.empty():
                worktree_status_signal.put_nowait(None)

        queue.set_owned_transition_notifier(
            lambda: loop.call_soon_threadsafe(_signal_worktree_status)
        )
        wake_options = {
            "interval": wake_interval,
            "max_attempts": wake_max_attempts,
            "retry_base": wake_retry_base,
            "idle_interval": max(wake_interval, 5.0),
            "wake_signal": wake_signal,
        }
        if wake_deliver is not None:
            wake_options["deliver"] = wake_deliver
        if wake_is_active is not None:
            wake_options["is_active"] = wake_is_active
        wake_task = (
            asyncio.create_task(
                drain_wake_outbox(queue, bus, **wake_options)
            )
            if wake_interval > 0
            else None
        )
        sweeper_health = LoopHealth(name="liveness_gc", base_interval=sweep_interval or 0.0)
        orphan_health = LoopHealth(name="orphan_reap", base_interval=sweep_interval or 0.0)
        handoff_fallback_health = LoopHealth(
            name="handoff_fallback", base_interval=sweep_interval or 0.0
        )
        worktree_status_health = LoopHealth(
            name="worktree_status_relay", base_interval=sweep_interval or 0.0
        )
        _app.state.loop_health = {
            sweeper_health.name: sweeper_health,
            orphan_health.name: orphan_health,
            handoff_fallback_health.name: handoff_fallback_health,
            worktree_status_health.name: worktree_status_health,
        }
        sweeper = (
            asyncio.create_task(
                _gc_loop(
                    queue,
                    sweep_interval,
                    bus,
                    health=sweeper_health,
                    governance=governance,
                )
            )
            if sweep_interval and sweep_interval > 0
            else None
        )
        orphan_reaper = (
            asyncio.create_task(
                _orphan_reap_loop(
                    queue,
                    sweep_interval,
                    bus,
                    orphan_grace=orphan_grace,
                    health=orphan_health,
                    governance=governance,
                )
            )
            if sweep_interval and sweep_interval > 0
            else None
        )
        handoff_fallback_reconciler = (
            asyncio.create_task(
                _handoff_fallback_loop(
                    queue,
                    sweep_interval,
                    bus,
                    grace=handoff_fallback_grace,
                    health=handoff_fallback_health,
                    governance=governance,
                )
            )
            if handoff_fallback_enabled and sweep_interval and sweep_interval > 0
            else None
        )
        worktree_status_relay = (
            asyncio.create_task(
                _worktree_status_relay_loop(
                    queue,
                    sweep_interval,
                    bus,
                    relay=relay,
                    signal=worktree_status_signal,
                    health=worktree_status_health,
                    governance=governance,
                )
            )
            if sweep_interval and sweep_interval > 0
            else None
        )
        # Self-retire on supersession (owner-liveness tether). A coordinator that
        # has been *demoted* -- a newer generation flipped the routing table and
        # now serves clients -- drains to its safe cutover point and exits on its
        # own instead of lingering as a stranded ``serve --passive`` process (the
        # observed leak: a demoted generation persisting after its successor took
        # over). The "owner" tracked here is the single active routing generation,
        # with the periodic liveness GC as a backstop.
        #
        # DEFAULT-ON (opt-out): armed unless ``AGENT_DISPATCH_SELF_RETIRE`` is
        # explicitly falsy; when disabled, the loop is never created. The loop
        # **self-gates on active-ness**:
        # its startup phase waits until the routing table's ``active`` entry is our
        # own pid before it captures our generation and begins watching. This is what
        # makes it correct for a **cutover-promoted** coordinator -- one spawned
        # ``--passive`` (so ``self_retire_publish`` is False) and promoted by the
        # orchestrator flipping the routing table to it: such a coordinator is exactly
        # the ``serve --passive`` process this targets, so we must NOT gate on
        # ``self_retire_publish`` (that would leave the primary target inert). A
        # passive instance that is never promoted never sees its own pid as active and
        # arms nothing. Fail-safe on two independent axes -- it exits only once BOTH
        # (a) supersession by a *live, strictly-newer* generation and (b) the safe
        # cutover point (``DrainGate`` reports no in-flight claim) are K-confirmed.
        # So the genuinely-active coordinator (its own pid = active) can never
        # self-retire, and a claim mid-flight is never dropped.
        self_retire_task = None
        _sr_enabled, _sr_poll, _sr_confirmations = _self_retire_settings()
        # Slot-ownership observability (process-slot-ownership Phase 5): a small,
        # continuously-updated status dict `/health` renders under `"slot"` so an
        # operator (or `agent-dispatch health`) can see this loop's own view of
        # itself without grepping logs -- armed?, generation, confirms toward
        # self-retire. Purely observational; never read by the loop's own logic.
        _app.state.self_retire_status = {
            "enabled": _sr_enabled,
            "armed": False,
            "generation": None,
            "superseded": False,
            "confirms": 0,
        }
        if _sr_enabled:
            async def _self_retire_loop() -> None:
                import os as _os

                from zdd import routing
                from zdd.routing import Endpoint

                from .config import routing_dir
                from .self_retire import is_superseded

                my_pid = _os.getpid()
                # Observe our own publish landing first, capturing our generation.
                my_gen: int | None = None
                for _ in range(600):  # ~5 min ceiling to see our own publish
                    await asyncio.sleep(0.5)
                    if await _governance_backoff(
                        governance,
                        "iteration-boundary:self-retire-arm",
                        loop_name="self-retire",
                    ):
                        continue
                    data = await asyncio.to_thread(routing.read_table, routing_dir())
                    raw = data.get("active") if isinstance(data, dict) else None
                    ep = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
                    if ep is not None and ep.pid == my_pid:
                        my_gen = ep.generation
                        break
                if my_gen is None:
                    return
                _app.state.self_retire_status["armed"] = True
                _app.state.self_retire_status["generation"] = my_gen
                gate = getattr(_app.state, "drain_gate", None)
                confirms = 0
                while True:
                    await asyncio.sleep(_sr_poll)
                    if await _governance_backoff(
                        governance,
                        "iteration-boundary:self-retire",
                        loop_name="self-retire",
                    ):
                        confirms = 0
                        _app.state.self_retire_status["confirms"] = 0
                        continue
                    try:
                        superseded = await asyncio.to_thread(
                            is_superseded, routing_dir(), my_pid, my_gen
                        )
                        # Safe cutover point: no claim in flight (a claimed task is
                        # already durable in the queue). Absent a gate, treat as safe.
                        at_safe_point = superseded and (
                            gate is None or gate.claims == 0
                        )
                    except Exception:
                        confirms = 0
                        _app.state.self_retire_status["superseded"] = False
                        _app.state.self_retire_status["confirms"] = 0
                        log.debug("self-retire supersession check failed", exc_info=True)
                        continue
                    _app.state.self_retire_status["superseded"] = bool(superseded)
                    if not (superseded and at_safe_point):
                        confirms = 0
                        _app.state.self_retire_status["confirms"] = 0
                        continue
                    confirms += 1
                    _app.state.self_retire_status["confirms"] = confirms
                    if confirms >= _sr_confirmations:
                        if await _governance_backoff(
                            governance,
                            "pre-mutation:self-retire",
                            loop_name="self-retire",
                        ):
                            confirms = 0
                            _app.state.self_retire_status["confirms"] = 0
                            continue
                        log.info(
                            "superseded by a live newer generation at a safe cutover "
                            "point -- self-retiring (was gen %d, pid %d)",
                            my_gen, my_pid,
                        )
                        server = getattr(_app.state, "uvicorn_server", None)
                        if server is not None:
                            server.should_exit = True
                        asyncio.create_task(
                            _force_exit_after_should_exit(
                                deadline_s=_self_retire_force_exit_seconds(),
                                my_gen=my_gen,
                                my_pid=my_pid,
                            )
                        )
                        return

            self_retire_task = asyncio.create_task(_self_retire_loop())
            log.info(
                "self-retire-on-supersession armed (K=%d, poll=%.0fs)",
                _sr_confirmations, _sr_poll,
            )

        # Abandoned-passive reap (#5195): the periodic backstop for a
        # `spawn_passive` daemon that was never promoted because its cutover's
        # orchestrator process died before the flip. Only the genuinely active
        # coordinator runs this sweep (armed the same way as self-retire: wait
        # until our own pid is observed as `active`), and it only acts on a
        # breadcrumb aged past the grace window, so a cutover still genuinely
        # in flight is never disturbed.
        abandoned_passive_reap_task = None
        _apr_enabled, _apr_poll, _apr_grace = _abandoned_passive_reap_settings()
        _app.state.abandoned_passive_reap_status = {
            "enabled": _apr_enabled,
            "armed": False,
            "last_outcome": None,
        }
        if _apr_enabled:
            async def _abandoned_passive_reap_loop() -> None:
                import os as _os

                from zdd import routing
                from zdd.routing import Endpoint

                from .config import routing_dir

                my_pid = _os.getpid()
                for _ in range(600):  # ~5 min ceiling to see our own publish
                    await asyncio.sleep(0.5)
                    if await _governance_backoff(
                        governance,
                        "iteration-boundary:abandoned-passive-reap-arm",
                        loop_name="abandoned-passive-reap",
                    ):
                        continue
                    data = await asyncio.to_thread(routing.read_table, routing_dir())
                    raw = data.get("active") if isinstance(data, dict) else None
                    ep = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
                    if ep is not None and ep.pid == my_pid:
                        break
                else:
                    return
                _app.state.abandoned_passive_reap_status["armed"] = True
                while True:
                    await asyncio.sleep(_apr_poll)
                    if await _governance_backoff(
                        governance,
                        "iteration-boundary:abandoned-passive-reap",
                        loop_name="abandoned-passive-reap",
                    ):
                        continue
                    if await _governance_backoff(
                        governance,
                        "pre-mutation:abandoned-passive-reap",
                        loop_name="abandoned-passive-reap",
                    ):
                        continue
                    try:
                        from zdd.breadcrumb import read_breadcrumb

                        from .reap import reap_abandoned_passive_backstop

                        record = await asyncio.to_thread(read_breadcrumb, routing_dir())
                        outcome = await asyncio.to_thread(
                            reap_abandoned_passive_backstop,
                            routing_dir(),
                            record=record,
                            grace_seconds=_apr_grace,
                        )
                        _app.state.abandoned_passive_reap_status["last_outcome"] = outcome
                        if outcome.get("reaped"):
                            log.warning(
                                "abandoned-passive reap: retired pid=%s "
                                "(never promoted): %s",
                                outcome.get("pid"), outcome.get("reason"),
                            )
                    except Exception:
                        log.debug(
                            "abandoned-passive reap cycle failed", exc_info=True
                        )

            abandoned_passive_reap_task = asyncio.create_task(
                _abandoned_passive_reap_loop()
            )
            log.info(
                "abandoned-passive-reap armed (poll=%.0fs, grace=%.0fs)",
                _apr_poll, _apr_grace,
            )

        # Live self-update: periodically checks whether a newer, fully
        # installed version is now published (``current-version`` marker) and,
        # once confirmed stale at a safe cutover point, spawns a self-triggered
        # ``deploy`` from that version's own interpreter. Opt-in
        # (``AGENT_DISPATCH_SELF_UPDATE=1``) -- see ``_self_update_settings``.
        self_update_task = None
        _su_enabled, _su_poll, _su_confirmations, _su_cooldown = _self_update_settings()
        if _su_enabled:
            async def _self_update_loop() -> None:
                import os as _os

                from zdd import routing
                from zdd.routing import Endpoint

                from .config import routing_dir
                from .runtime_version import install_dir
                from .self_retire import is_superseded
                from .self_update import stale_target

                my_pid = _os.getpid()
                my_gen: int | None = None
                for _ in range(600):  # ~5 min ceiling to see our own publish
                    await asyncio.sleep(0.5)
                    if await _governance_backoff(
                        governance,
                        "iteration-boundary:self-update-arm",
                        loop_name="self-update",
                    ):
                        continue
                    data = await asyncio.to_thread(routing.read_table, routing_dir())
                    raw = data.get("active") if isinstance(data, dict) else None
                    ep = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
                    if ep is not None and ep.pid == my_pid:
                        my_gen = ep.generation
                        break
                if my_gen is None:
                    return
                gate = getattr(_app.state, "drain_gate", None)
                confirms = 0
                last_trigger: float | None = None
                while True:
                    await asyncio.sleep(_su_poll)
                    if await _governance_backoff(
                        governance,
                        "iteration-boundary:self-update",
                        loop_name="self-update",
                    ):
                        confirms = 0
                        continue
                    try:
                        if await asyncio.to_thread(
                            is_superseded, routing_dir(), my_pid, my_gen
                        ):
                            # A newer generation is already live -- either our
                            # own prior trigger landed, or someone else
                            # redeployed. Self-retire above owns our exit.
                            return
                        target = await asyncio.to_thread(
                            stale_target, install_dir(), __version__
                        )
                        # Not a safe point if a claim is in flight, OR if a
                        # cutover (ours or an externally-triggered one) is
                        # already draining -- `gate.claims` can already read 0
                        # early in that window, before the routing-table flip
                        # commits and `is_superseded` above starts saying
                        # True, so relying on `is_superseded` alone lets this
                        # loop race a second, redundant deploy on top of one
                        # already in flight.
                        at_safe_point = target is not None and (
                            gate is None or (gate.claims == 0 and not gate.draining)
                        )
                    except Exception:
                        confirms = 0
                        log.debug("self-update staleness check failed", exc_info=True)
                        continue
                    if not at_safe_point:
                        confirms = 0
                        continue
                    confirms += 1
                    if confirms < _su_confirmations:
                        continue
                    now = time.monotonic()
                    if last_trigger is not None and now - last_trigger < _su_cooldown:
                        continue
                    confirms = 0
                    try:
                        if await _governance_backoff(
                            governance,
                            "pre-mutation:self-update",
                            loop_name="self-update",
                        ):
                            continue
                        _spawn_self_deploy(target)
                        last_trigger = now
                        log.info(
                            "detected a newer installed version at %s -- "
                            "spawned a self-triggered deploy (pid %d, gen %d)",
                            target, my_pid, my_gen,
                        )
                    except Exception:
                        log.warning(
                            "failed to spawn self-triggered deploy", exc_info=True
                        )

            self_update_task = asyncio.create_task(_self_update_loop())
            log.info(
                "self-update armed (K=%d, poll=%.0fs, cooldown=%.0fs)",
                _su_confirmations, _su_poll, _su_cooldown,
            )
        async with contextlib.AsyncExitStack() as stack:
            if coordinator_mcp is not None:
                # mcp 2.0: a mounted sub-app's own lifespan doesn't run, so drive
                # the streamable-HTTP session manager from the host lifespan.
                await stack.enter_async_context(coordinator_mcp.session_manager.run())
            try:
                yield
            finally:
                queue.set_wake_notifier(None)
                queue.set_owned_transition_notifier(None)
                if wake_task is not None:
                    wake_task.cancel()
                    try:
                        await wake_task
                    except asyncio.CancelledError:
                        pass
                if self_retire_task is not None:
                    self_retire_task.cancel()
                    try:
                        await self_retire_task
                    except asyncio.CancelledError:
                        pass
                if abandoned_passive_reap_task is not None:
                    abandoned_passive_reap_task.cancel()
                    try:
                        await abandoned_passive_reap_task
                    except asyncio.CancelledError:
                        pass
                if self_update_task is not None:
                    self_update_task.cancel()
                    try:
                        await self_update_task
                    except asyncio.CancelledError:
                        pass
                if sweeper is not None:
                    sweeper.cancel()
                    try:
                        await sweeper
                    except asyncio.CancelledError:
                        pass
                if orphan_reaper is not None:
                    orphan_reaper.cancel()
                    try:
                        await orphan_reaper
                    except asyncio.CancelledError:
                        pass
                if handoff_fallback_reconciler is not None:
                    handoff_fallback_reconciler.cancel()
                    try:
                        await handoff_fallback_reconciler
                    except asyncio.CancelledError:
                        pass
                if worktree_status_relay is not None:
                    worktree_status_relay.cancel()
                    try:
                        await worktree_status_relay
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(
        title="agent-dispatch",
        version=__version__,
        dependencies=[Depends(_make_auth(token, control_token))],
        lifespan=lifespan,
    )
    app.state.bus = bus
    app.state.directory = directory
    # Back-compat alias for the pre-generalization attribute name.
    app.state.satellites = directory
    app.state.worktree_status_relay = relay
    # Graceful-cutover drain gate (docs/patterns/graceful-daemon-cutover.md).
    app.state.drain_gate = DrainGate()

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        result_errors = [
            error
            for error in errors
            if tuple(error.get("loc", ()))[:2] == ("body", "result")
        ]
        if any(error.get("type") == "result_too_large" for error in result_errors):
            status = 413
        elif result_errors or any(
            error.get("type") in {"json_invalid", "finite_number"}
            for error in errors
        ):
            status = 400
        else:
            status = 422
        safe_errors = [
            {
                key: error[key]
                for key in ("type", "loc", "msg")
                if key in error
            }
            for error in errors
        ]
        return JSONResponse(
            status_code=status,
            content=jsonable_encoder({"detail": safe_errors}),
        )


    register_status_routes(app, queue, bus)
    register_directory_routes(app, directory)
    register_task_routes(
        app,
        queue,
        bus,
        control_token=control_token,
        resolve_owner_session_id=lambda worker_id: _resolve_owner_session_id(worker_id),
    )
    register_spawn_routes(app, queue, bus)
    register_registry_routes(app, queue)
    register_worktree_status_routes(app, relay)

    if mcp_app is not None:
        # Mounted last so the coordinator's own routes take precedence.
        app.mount("/mcp", mcp_app)

    return app
