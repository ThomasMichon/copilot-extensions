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
from contextlib import asynccontextmanager, contextmanager
from dataclasses import asdict
from threading import Condition
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
)
from pydantic_core import PydanticCustomError

from . import __version__, telemetry
from .config import DEFAULT_ORPHAN_GRACE
from .events import EventBus, sse_format
from .queue import (
    CompletionOutcome,
    ProducerFenceError,
    ProducerScopeValidationError,
    ResultTooLargeError,
    ResultValidationError,
    SpawnReservation,
    StructuredResult,
    Task,
    TaskError,
    TaskQueue,
    encode_result,
    worker_id_for,
)
from .satellites import (
    ROLE_SATELLITE,
    FleetDirectory,
    UnknownInstance,
)

log = logging.getLogger("agent-dispatch.coordinator")


def _strict_structured_result(value: Any) -> Any:
    if value is None:
        return value
    try:
        encode_result(value)
    except ResultTooLargeError as exc:
        raise PydanticCustomError("result_too_large", str(exc)) from exc
    except ResultValidationError as exc:
        raise ValueError(str(exc)) from exc
    return value


HttpStructuredResult = Annotated[
    StructuredResult, BeforeValidator(_strict_structured_result)
]


# Generation self-retire tuning. Default-ON (opt-out): validated on real cutovers
# (it arms for cutover-promoted coordinators and self-retires a demoted generation
# without dropping an in-flight claim), so it is now the default. Set
# ``AGENT_DISPATCH_SELF_RETIRE=0`` (or false/no/off) to disable it. Cadence and the
# K-confirmation count are env-tunable.
_SELF_RETIRE_DEFAULT_POLL_S = 30.0
_SELF_RETIRE_DEFAULT_CONFIRMATIONS = 3


def _self_retire_settings() -> tuple[bool, float, int]:
    """``(enabled, poll_seconds, confirmations)`` for generation self-retire.

    ``enabled`` is True (default-ON / opt-out) unless ``AGENT_DISPATCH_SELF_RETIRE``
    is explicitly falsy (``0``/``false``/``no``/``off``) -- when disabled, the loop
    is never created, so no self-retire code runs at all. The poll cadence
    (``AGENT_DISPATCH_SELF_RETIRE_POLL_S``) and confirmation count
    (``AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS``) are overridable.
    """
    import os

    enabled = os.environ.get("AGENT_DISPATCH_SELF_RETIRE", "").strip().lower() not in (
        "0", "false", "no", "off",
    )
    try:
        poll = float(
            os.environ.get("AGENT_DISPATCH_SELF_RETIRE_POLL_S", "")
            or _SELF_RETIRE_DEFAULT_POLL_S
        )
        poll = max(1.0, poll)
    except ValueError:
        poll = _SELF_RETIRE_DEFAULT_POLL_S
    try:
        k = int(
            os.environ.get("AGENT_DISPATCH_SELF_RETIRE_CONFIRMATIONS", "")
            or _SELF_RETIRE_DEFAULT_CONFIRMATIONS
        )
        k = max(1, k)
    except ValueError:
        k = _SELF_RETIRE_DEFAULT_CONFIRMATIONS
    return enabled, poll, k


class DrainGate:
    """Process-wide drain state for the graceful daemon cutover.

    The **safe cutover point** for the coordinator is *between task claims*:
    draining means stop handing out new claims and let any in-flight ``/claim``
    settle. A claimed-but-unstarted task is already durable in the SQLite queue
    (``queued``/held with a lease the liveness GC recovers), so once no claim is
    mid-flight the old coordinator can be retired without losing non-resumable
    work. See docs/patterns/graceful-daemon-cutover.md.
    """

    def __init__(self) -> None:
        self._condition = Condition()
        self._draining = False
        self._claims = 0

    @property
    def draining(self) -> bool:
        with self._condition:
            return self._draining

    @property
    def claims(self) -> int:
        with self._condition:
            return self._claims

    def set_draining(self, value: bool) -> None:
        with self._condition:
            self._draining = value
            self._condition.notify_all()

    @contextmanager
    def track_claim(self):
        """Count an in-flight claim so drain can wait for the safe point."""
        with self._condition:
            self._claims += 1
        try:
            yield
        finally:
            with self._condition:
                self._claims = max(0, self._claims - 1)
                self._condition.notify_all()

    def wait_for_claims(self, *, timeout: float, poll: float) -> bool:
        """Block until no claim is in flight (True) or ``timeout`` elapses (False)."""
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while self._claims > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(timeout=min(max(poll, 0.05), remaining))
            return True


class DrainRequest(BaseModel):
    """Request body for the zdd drain endpoint."""

    timeout: float = 300.0
    poll: float = 1.0
    force: bool = False


def _resolve_owner_session_id(worker_id: str | None) -> str | None:
    """Best-effort: resolve a worker's (``machine/worktree``) current live-session
    id, captured on ``start`` as the task's owner identity for liveness GC.

    Shells the same agent-bridge live-session resolver `tracking` uses. Any
    failure (no bridge, unreachable, no session yet) returns ``None`` -- the task
    is then simply not GC-attributable until a later capture, never wrongly
    requeued (an owner without a captured identity reads ``unknown``).
    """
    if not worker_id or "/" not in worker_id:
        return None
    from . import tracking

    machine, _sep, worktree = worker_id.partition("/")
    if not worktree:
        return None
    is_remote = tracking.remote_dispatch.is_peer_machine(machine)
    session = tracking.resolve_live_session(
        worktree, machine=machine if is_remote else None
    )
    if not session:
        return None
    return session.get("session_id") or session.get("id")


def _reap_orphans(queue: TaskQueue, grace: float) -> int:
    """Reap unowned proposed/queued tasks pinned to a no-longer-live worktree.

    Resolves this machine + its live worktrees (both shell ``agent-worktrees``),
    then delegates the fenced abandon to
    :meth:`TaskQueue.reap_orphaned_targets`. Degrade-safe: an unresolved machine
    or worktree probe reaps nothing. Runs off the event loop (subprocess-shelling)
    via a worker thread. Returns the reaped count.
    """
    from . import tracking
    from .identity import resolve_machine

    machine = resolve_machine()
    if not machine:
        return 0
    live = tracking.live_worktrees()
    if live is None:
        return 0
    counts = queue.reap_orphaned_targets(live, machine=machine, grace_secs=grace)
    return counts.get("reaped", 0)


async def _gc_loop(
    queue: TaskQueue,
    interval: float,
    bus: EventBus,
) -> None:
    """Periodically garbage-collect tasks by **liveness**.

    A ``claimed``/``started`` task is requeued only when its owner worktree is
    *confirmed gone* (not on elapsed time), so long-running live work is never
    disturbed and a bridge blip leaves a task alone. Orphaned-pin reaping runs
    in its own loop so a slow worktree probe cannot delay this correctness pass.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            counts = await asyncio.to_thread(queue.reconcile_liveness)
        except Exception:  # pragma: no cover -- never let the loop die on a blip
            log.exception("liveness GC pass failed")
            counts = {}
        requeued = counts.get("requeued", 0)
        if requeued:
            log.info(
                "liveness GC requeued %d task(s) with a gone owner (checked %d)",
                requeued,
                counts.get("checked", 0),
            )
            bus.publish({"type": "task.reconciled", "requeued": requeued, **counts})


async def _orphan_reap_loop(
    queue: TaskQueue,
    interval: float,
    bus: EventBus,
    *,
    orphan_grace: float,
) -> None:
    """Reap orphaned target pins without blocking held-task liveness GC."""
    while True:
        await asyncio.sleep(interval)
        try:
            reaped = await asyncio.to_thread(
                _reap_orphans, queue, orphan_grace
            )
        except Exception:  # pragma: no cover -- never let the loop die on a blip
            log.exception("orphan reap pass failed")
            reaped = 0
        if reaped:
            log.info(
                "liveness GC reaped %d orphaned task(s) (target worktree gone)",
                reaped,
            )
            bus.publish({"type": "task.reaped", "reaped": reaped})


class ProducerScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    repo: str | None = None
    prompt: str = ""
    proposed: bool = False
    requires: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    affinity: dict[str, str] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    payload_ref: str | None = None
    payload_inline: str | None = None
    target_machine: str | None = None
    target_worktree: str | None = None
    target_repo: str | None = None
    exclusive_key: str | None = None
    supersede_exclusive_key: bool = False
    source: str | None = None
    origin_ref: str | None = None
    evaluator_ref: str | None = None
    dedup_key: str | None = None
    producer_scope: ProducerScopeBody | None = None
    producer_id: str | None = None
    producer_generation: StrictInt | None = None
    producer_capability: str | None = None
    producer_request_id: str | None = None
    goal: str | None = None
    done_criteria: str | None = None
    not_before: FiniteFloat = 0.0
    claim_as: str | None = None


class ProducerScopeHandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str
    producer_id: str
    expected_generation: StrictInt
    required_label: str | None = None


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str | None = None
    repo: str | None = None
    all_repos: bool = False
    machine: str | None = None
    worktree: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    task_id: str | None = None
    lease_seconds: int | None = None
    evaluation: bool = False


class WorkerBody(BaseModel):
    worker_id: str
    #: Optional: the worktree's current live-session id, captured on ``start`` as
    #: the task's owner identity (for liveness GC). When omitted the coordinator
    #: resolves it best-effort from the owner worktree.
    owner_session_id: str | None = None


class ActivityBody(BaseModel):
    activity: str | None = None
    reservation_key: str


class OwnerSessionBody(BaseModel):
    worker_id: str
    owner_session_id: str
    expected_generation: int | None = None


class YieldBody(BaseModel):
    worker_id: str
    note: str | None = None
    exclude: str | None = None
    release_spawn: bool = True


class SuspendBody(BaseModel):
    worker_id: str
    reason: str


class ResumeBody(BaseModel):
    worker_id: str
    wake: bool = True
    message: str | None = None
    adopt_session: bool = False
    reuse_session: bool = False
    expected_owner_session_id: str | None = None
    expected_generation: int | None = None


class ReleaseBody(BaseModel):
    worker_id: str
    reason: str | None = None


class CompleteBody(BaseModel):
    worker_id: str
    result_ref: str | None = None
    result: HttpStructuredResult = None  # type: ignore[assignment]
    expected_status: str | None = None
    expected_owner_session_id: str | None = None
    expected_generation: int | None = None


class ProgressBody(BaseModel):
    worker_id: str
    phase: str = ""
    summary: str
    blocker: str | None = None
    pr: str | None = None


class CardBody(BaseModel):
    """A card a worker posts to describe what it needs from the operator. The
    ``card`` object is built client-side (see ``steering.build_card``) and stored
    opaquely, keeping the coordinator a general steering substrate."""

    worker_id: str
    card: dict


class SteerBody(BaseModel):
    """An operator's answer to a task's card. Not worker-owned -- the operator
    (or a surface acting for them) submits it."""

    fields: dict = Field(default_factory=dict)
    sender: str | None = None
    wake: bool = True
    message: str | None = None


class SteerTakeBody(BaseModel):
    """A worker consuming the next pending steer for a task it owns."""

    worker_id: str
    all_pending: bool = False


class AbandonBody(BaseModel):
    worker_id: str | None = None
    permitted: bool = False
    reason: str | None = None


class ReserveSpawnBody(BaseModel):
    task_id: str
    reserved_by: str | None = None


class RecordSpawnBody(BaseModel):
    session_handle: str | None = None
    worktree: str | None = None


class RecordSpawnWorktreeBody(BaseModel):
    worktree: str
    ownership: str = "unknown"
    creating_host: str | None = None
    driver: str | None = None


class ReservationDetailBody(BaseModel):
    detail: str | None = None
    conclusion_state: str | None = None
    conclusion_detail: str | None = None


class RequestSpawnReleaseBody(BaseModel):
    detail: str | None = None
    disposition: str = "failed"


class RearmSpawnBody(BaseModel):
    permitted: bool = False
    reason: str | None = None
    min_failures: int = 3


class ScheduleLeaseBody(BaseModel):
    holder: str
    holder_session: str | None = None
    ttl: float | None = None


class ReleaseLeaseBody(BaseModel):
    holder: str
    force: bool = False


class AcquireResourceReservationBody(BaseModel):
    key: str
    owner: str
    ttl: float
    token: str | None = None


class BindResourceReservationBody(BaseModel):
    key: str
    owner: str
    token: str
    task_id: str


class ReleaseResourceReservationBody(BaseModel):
    key: str
    owner: str
    token: str


class RegistrationBody(BaseModel):
    kind: str
    spec: dict
    id: str | None = None
    machine: str | None = None
    env: str = "default"


class RegistrationStatusBody(BaseModel):
    status: str


class SatelliteRegisterBody(BaseModel):
    machine: str
    worktrees: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    status: dict = Field(default_factory=dict)


class SatelliteHeartbeatBody(BaseModel):
    status: dict | None = None
    worktrees: list[str] | None = None
    gate_state: str | None = None


class DirectoryRegisterBody(BaseModel):
    instance: str
    role: str = "peer"
    epoch: int = 0
    machine: str | None = None
    worktrees: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    status: dict = Field(default_factory=dict)


class DirectoryHeartbeatBody(BaseModel):
    status: dict | None = None
    worktrees: list[str] | None = None
    gate_state: str | None = None
    role: str | None = None
    epoch: int | None = None


def _task_dict(task: Task) -> dict:
    return asdict(task)


def _task_with_spawn_dict(queue: TaskQueue, task: Task) -> dict:
    result = asdict(task)
    latest = queue.latest_reservation(task.id)
    if latest is not None:
        result["spawn_reservation"] = asdict(latest)
    return result


def _bulk_task_dict(task: Task) -> dict:
    result = asdict(task)
    result.pop("result")
    return result


def _event_task_dict(task: dict) -> dict:
    result = dict(task)
    result["has_result"] = result.pop("result", None) is not None or bool(
        result.get("has_result")
    )
    return result


def _reservation_dict(res: SpawnReservation) -> dict:
    return asdict(res)


def _make_auth(token: str | None, control_token: str | None):
    bearer = HTTPBearer(auto_error=False)
    accepted = tuple(value for value in (token, control_token) if value)

    def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:  # noqa: B008
        if token is None:
            return
        if creds is None or not any(
            secrets.compare_digest(creds.credentials, value) for value in accepted
        ):
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return check


def _make_control_auth(
    control_token: str | None,
    on_reject: Callable[[str, dict[str, object]], None],
):
    bearer = HTTPBearer(auto_error=False)

    def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:  # noqa: B008
        if control_token is None:
            detail = {
                "code": "producer_control_unavailable",
                "operation": "transition",
                "reason": "control_authority_not_configured",
                "message": "managed producer transitions require a configured control token",
                "retryable": False,
            }
            on_reject(
                "producer_scope.transition_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(
                status_code=503,
                detail=detail,
            )
        if creds is None or not secrets.compare_digest(
            creds.credentials, control_token
        ):
            detail = {
                "code": "producer_control_forbidden",
                "operation": "transition",
                "reason": "invalid_control_authority",
                "message": "invalid or missing producer control bearer",
                "retryable": False,
            }
            on_reject(
                "producer_scope.transition_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(
                status_code=403,
                detail=detail,
            )

    return check


def create_app(
    queue: TaskQueue,
    *,
    token: str | None = None,
    control_token: str | None = None,
    sweep_interval: float = 0.0,
    orphan_grace: float = DEFAULT_ORPHAN_GRACE,
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

        wake_signal: asyncio.Queue[None] = asyncio.Queue(maxsize=1)
        if wake_interval > 0:
            def _signal_wake() -> None:
                if wake_signal.empty():
                    wake_signal.put_nowait(None)

            queue.set_wake_notifier(
                lambda: loop.call_soon_threadsafe(_signal_wake)
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
        sweeper = (
            asyncio.create_task(_gc_loop(queue, sweep_interval, bus))
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
                while True:
                    await asyncio.sleep(_sr_poll)
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
                        log.debug("self-retire supersession check failed", exc_info=True)
                        continue
                    if not (superseded and at_safe_point):
                        confirms = 0
                        continue
                    confirms += 1
                    if confirms >= _sr_confirmations:
                        log.info(
                            "superseded by a live newer generation at a safe cutover "
                            "point -- self-retiring (was gen %d, pid %d)",
                            my_gen, my_pid,
                        )
                        server = getattr(_app.state, "uvicorn_server", None)
                        if server is not None:
                            server.should_exit = True
                        return

            self_retire_task = asyncio.create_task(_self_retire_loop())
            log.info(
                "self-retire-on-supersession armed (K=%d, poll=%.0fs)",
                _sr_confirmations, _sr_poll,
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

    def _require(task: Task | None) -> Task:
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return task

    def _emit(event_type: str, task: dict) -> None:
        event_task = _event_task_dict(task)
        bus.publish({"type": event_type, "task": event_task})
        # Generic telemetry seam (no-op unless a consumer registered a sink).
        telemetry.emit(telemetry.task_lifecycle_event(event_type, event_task))

    def _emit_producer_event(event_type: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event_type, "producer_fence": detail})
        telemetry.emit(telemetry.producer_fence_event(event_type, detail))

    def _producer_rejection(exc: ProducerFenceError) -> None:
        _emit_producer_event(
            "task.create_rejected", exc.event(operation="create")
        )

    def _guard(op, event_type: str | None = None) -> dict:
        """Run a queue mutation (TaskError -> 409 / missing -> 404), then emit."""
        try:
            mutation = op()
            if isinstance(mutation, CompletionOutcome):
                event_type = mutation.event_type
                mutation = mutation.task
            result = _task_dict(mutation)
        except ResultTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ResultValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        if event_type is not None:
            _emit(event_type, result)
        return result

    @app.get("/health")
    def health(request: Request, repo: str | None = None) -> dict:
        gate: DrainGate = request.app.state.drain_gate
        return {
            "status": "draining" if gate.draining else "ok",
            "version": __version__,
            "draining": gate.draining,
            "subscribers": bus.subscriber_count,
            "backlog": queue.backlog_health(repo=repo),
            "wakes": queue.wake_metrics(),
        }

    @app.get("/events")
    async def events_stream() -> StreamingResponse:
        async def gen():
            async for event in bus.subscribe():
                yield sse_format(event)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # -- fleet directory (awareness plane) + satellite façade ----------------
    # Every federating instance registers here so the fleet is enumerable from
    # any seat (awareness plane); a coordinator advertises itself so peers
    # *discover* it (claim plane) rather than electing one. A satellite is just
    # a directory entry with role="satellite" -- an outbound-only field machine
    # the coordinator never dials into.
    @app.post("/directory/register")
    def directory_register(body: DirectoryRegisterBody) -> dict:
        return directory.register(
            body.instance,
            role=body.role,
            epoch=body.epoch,
            machine=body.machine,
            worktrees=body.worktrees,
            capabilities=body.capabilities,
            gate_state=body.gate_state,
            agent_versions=body.agent_versions,
            status=body.status,
        )

    @app.post("/directory/{instance}/heartbeat")
    def directory_heartbeat(instance: str, body: DirectoryHeartbeatBody) -> dict:
        try:
            return directory.heartbeat(
                instance,
                status=body.status,
                worktrees=body.worktrees,
                gate_state=body.gate_state,
                role=body.role,
                epoch=body.epoch,
            )
        except UnknownInstance as exc:
            raise HTTPException(
                status_code=404, detail="unknown instance"
            ) from exc

    @app.delete("/directory/{instance}")
    def directory_deregister(instance: str) -> dict:
        return {"deregistered": directory.deregister(instance)}

    @app.get("/directory")
    def directory_list(role: str | None = None) -> list[dict]:
        return directory.discover_peers(role=role)

    @app.get("/directory/coordinator")
    def directory_coordinator() -> dict | None:
        return directory.discover_coordinator()

    # Satellite façade: same store, role pinned to "satellite".
    @app.post("/satellites/register")
    def satellite_register(body: SatelliteRegisterBody) -> dict:
        return directory.register(
            body.machine,
            role=ROLE_SATELLITE,
            worktrees=body.worktrees,
            capabilities=body.capabilities,
            gate_state=body.gate_state,
            agent_versions=body.agent_versions,
            status=body.status,
        )

    @app.post("/satellites/{machine}/heartbeat")
    def satellite_heartbeat(machine: str, body: SatelliteHeartbeatBody) -> dict:
        try:
            return directory.heartbeat(
                machine,
                status=body.status,
                worktrees=body.worktrees,
                gate_state=body.gate_state,
            )
        except UnknownInstance as exc:
            # 404 tells the satellite client to re-register rather than resurrect
            # a reaped entry.
            raise HTTPException(status_code=404, detail="unknown satellite") from exc

    @app.delete("/satellites/{machine}")
    def satellite_deregister(machine: str) -> dict:
        return {"deregistered": directory.deregister(machine)}

    @app.get("/satellites")
    def satellite_list() -> list[dict]:
        return directory.discover_peers(role=ROLE_SATELLITE)

    @app.get("/producer-scopes/status")
    def producer_scope_status(repo: str, source: str) -> dict:
        try:
            return asdict(queue.producer_scope_status(repo, source))
        except ProducerScopeValidationError as exc:
            raise HTTPException(
                status_code=400, detail=exc.detail(operation="status")
            ) from exc

    @app.post(
        "/producer-scopes/handoff",
        dependencies=[
            Depends(_make_control_auth(control_token, _emit_producer_event))
        ],
    )
    def producer_scope_handoff(body: ProducerScopeHandoffBody) -> dict:
        try:
            transition = queue.handoff_producer_scope(
                body.repo,
                body.source,
                producer_id=body.producer_id,
                expected_generation=body.expected_generation,
                required_label=body.required_label,
            )
        except ProducerScopeValidationError as exc:
            detail = exc.detail(operation="transition")
            _emit_producer_event(
                "producer_scope.transition_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except ProducerFenceError as exc:
            detail = exc.detail(operation="transition")
            _emit_producer_event(
                "producer_scope.transition_rejected",
                exc.event(operation="transition"),
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        result = transition.as_dict()
        state = transition.state
        detail: dict[str, object] = {
            "repo": state.scope["repo"],
            "source": state.scope["source"],
            "from_generation": body.expected_generation,
            "to_generation": state.current_generation,
            "active_producer": state.active_producer,
            "replayed": transition.replayed,
        }
        if state.required_label is not None:
            detail["required_label"] = state.required_label
        _emit_producer_event("producer_scope.transitioned", detail)
        return result

    @app.post("/tasks")
    def create(body: CreateBody) -> dict:
        data = body.model_dump()
        proposed = data.pop("proposed")
        try:
            outcome = (
                queue.propose_outcome(**data)
                if proposed
                else queue.create_outcome(**data)
            )
            task = _task_dict(outcome.task)
        except ProducerScopeValidationError as exc:
            detail = exc.detail(operation="create")
            _emit_producer_event(
                "task.create_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except ProducerFenceError as exc:
            _producer_rejection(exc)
            status = 403 if exc.reason == "invalid_capability" else 409
            raise HTTPException(
                status_code=status, detail=exc.detail(operation="create")
            ) from exc
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if outcome.event_type is not None:
            _emit(outcome.event_type, task)
        return task

    @app.get("/tasks")
    def list_tasks(
        repo: str | None = None,
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        evaluator_ref: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        exclusive_key: str | None = None,
        q: str | None = None,
        sweep: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        if sweep:
            return [_bulk_task_dict(t) for t in queue.sweep(repo=repo, limit=limit)]
        if q is not None:
            return [_bulk_task_dict(t) for t in queue.find(q, repo=repo, limit=limit)]
        # ``status`` may be a single state or a comma-separated set (multi-state
        # browse), e.g. ``?status=queued,started``.
        status_filter: str | list[str] | None = None
        if status is not None:
            parts = [s.strip() for s in status.split(",") if s.strip()]
            status_filter = parts[0] if len(parts) == 1 else parts
        tasks = queue.list(
            repo=repo,
            status=status_filter,
            target_machine=target_machine,
            target_repo=target_repo,
            label=label,
            evaluator_ref=evaluator_ref,
            source=source,
            origin_ref=origin_ref,
            exclusive_key=exclusive_key,
            limit=limit,
        )
        return [_bulk_task_dict(t) for t in tasks]

    @app.get("/tasks/mine")
    def mine(machine: str, worktree: str, repo: str | None = None) -> dict:
        result = queue.mine(machine, worktree, repo=repo)
        return {k: [_bulk_task_dict(t) for t in v] for k, v in result.items()}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return _task_with_spawn_dict(queue, _require(queue.get(task_id)))

    @app.get("/tasks/{task_id}/result")
    def get_result(task_id: str) -> dict:
        task = _require(queue.get(task_id))
        return {
            "task_id": task.id,
            "ref": task.result_ref,
            "result": queue.read_result(task),
        }

    @app.get("/tasks/{task_id}/events")
    def get_events(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return queue.events(task_id)

    @app.get("/tasks/{task_id}/wakes")
    def get_wakes(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return [asdict(wake) for wake in queue.list_wakes(task_id)]

    @app.get("/tasks/{task_id}/progress-log")
    def get_progress_log(task_id: str) -> list[dict]:
        """The accumulated append-only progress log (oldest first)."""
        _require(queue.get(task_id))
        return queue.progress_log(task_id)

    @app.get("/tasks/{task_id}/payload")
    def get_payload(task_id: str) -> dict:
        task = _require(queue.get(task_id))
        content = queue.read_payload(task)
        return {
            "task_id": task.id,
            "ref": task.payload_ref,
            "inline": task.payload_inline is not None,
            "payload": content,
        }

    @app.post("/tasks/{task_id}/approve")
    def approve(task_id: str) -> dict:
        return _guard(lambda: queue.approve(task_id), "task.approved")

    @app.post("/claim")
    def claim(request: Request, body: ClaimBody) -> dict | None:
        gate: DrainGate = request.app.state.drain_gate
        # Safe cutover point: once draining, stop handing out new claims so the
        # old coordinator can be retired between claims without stranding work.
        # A worker that gets None simply retries and lands on the new coordinator
        # (clients follow the routing-table flip).
        if gate.draining:
            return None
        owner = body.worker_id
        if owner is None and body.machine and body.worktree:
            owner = worker_id_for(body.machine, body.worktree)
        if owner is None:
            raise HTTPException(
                status_code=422, detail="claim requires worker_id, or both machine and worktree"
            )
        if body.all_repos and body.repo:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "claim_scope_invalid",
                    "message": "claim accepts repo or all_repos=true, not both",
                },
            )
        if not body.all_repos and not body.repo:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "claim_scope_required",
                    "message": "claim requires repo or explicit all_repos=true",
                },
            )
        with gate.track_claim():
            outcome = queue.claim_outcome(
                owner,
                body.capabilities,
                repo=None if body.all_repos else body.repo,
                machine=body.machine,
                worktree=body.worktree,
                task_id=body.task_id,
                lease_seconds=body.lease_seconds,
                evaluation=body.evaluation,
            )
        for rejection in outcome.producer_rejections:
            _emit_producer_event("producer.claim_rejected", rejection)
        task = outcome.task
        if task is None:
            return None
        result = _task_dict(task)
        _emit("task.claimed", result)
        return result

    @app.post("/drain")
    async def drain(request: Request, body: DrainRequest | None = None) -> dict:
        """Internal cutover seam: stop claiming and wait for the safe point.

        Not an operator surface -- the installer's in-process cutover calls this
        (via the zdd CutoverOrchestrator) to quiesce the old coordinator between
        task claims before retiring it. The supervisor + spawned workers are NOT
        drained here: they outlive the swap and re-adopt the new coordinator via
        the durable queue DB + the routing table.
        """
        gate: DrainGate = request.app.state.drain_gate
        opts = body or DrainRequest()
        timeout = max(0.0, float(opts.timeout))
        poll = max(0.05, float(opts.poll))
        gate.set_draining(True)
        clean = await asyncio.to_thread(gate.wait_for_claims, timeout=timeout, poll=poll)
        forced = bool(opts.force and not clean)
        drained = clean or forced
        return {
            "drained": drained,
            "clean": clean,
            "forced": forced,
            "busy_claims": gate.claims,
        }

    @app.post("/undrain")
    async def undrain(request: Request) -> dict:
        """Internal cutover seam: reopen claiming (rollback of an aborted cutover)."""
        gate: DrainGate = request.app.state.drain_gate
        gate.set_draining(False)
        return {"draining": False}

    @app.post("/shutdown")
    def shutdown(request: Request) -> dict:
        """Internal cutover seam: request a clean uvicorn exit (retire this daemon)."""
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
        return {"shutdown": True}

    @app.post("/adopt-relay")
    def adopt_relay() -> dict:
        """Internal cutover seam: agent-dispatch owns no shared relay (no-op)."""
        return {"adopted": False, "reason": "agent-dispatch has no relay"}

    @app.post("/tasks/{task_id}/start")
    def start(task_id: str, body: WorkerBody) -> dict:
        owner_session_id = body.owner_session_id or _resolve_owner_session_id(body.worker_id)
        return _guard(
            lambda: queue.start(task_id, body.worker_id, owner_session_id=owner_session_id),
            "task.started",
        )

    @app.post("/tasks/{task_id}/yield")
    def yield_task(task_id: str, body: YieldBody) -> dict:
        return _guard(
            lambda: queue.yield_task(
                task_id,
                body.worker_id,
                note=body.note,
                exclude=body.exclude,
                release_spawn=body.release_spawn,
            ),
            "task.yielded",
        )

    @app.post("/tasks/{task_id}/suspend")
    def suspend(task_id: str, body: SuspendBody) -> dict:
        return _guard(
            lambda: queue.suspend(task_id, body.worker_id, reason=body.reason),
            "task.suspended",
        )

    @app.post("/tasks/{task_id}/resume")
    def resume(task_id: str, body: ResumeBody) -> dict:
        message = body.message or (
            f"Task {task_id} has been resumed. Continue toward its goal "
            "from the durable progress already recorded."
        )
        adopt_owner_session_id = None
        if body.adopt_session:
            adopt_owner_session_id = _resolve_owner_session_id(body.worker_id)
            if adopt_owner_session_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"cannot adopt task {task_id}: no current live session "
                        f"for owner {body.worker_id!r}"
                    ),
                )
        task = _guard(
            lambda: queue.resume(
                task_id,
                body.worker_id,
                wake_requested=body.wake,
                wake_message=message,
                adopt_owner_session_id=adopt_owner_session_id,
                reuse_session=body.reuse_session,
                expected_owner_session_id=body.expected_owner_session_id,
                expected_generation=body.expected_generation,
            ),
            "task.resumed",
        )
        return {
            **task,
            "resume_woken": None,
            "resume_wake_status": (
                task.get("wake_status") if body.wake else "not_requested"
            ),
        }

    @app.post("/tasks/{task_id}/release")
    def release(task_id: str, body: ReleaseBody) -> dict:
        return _guard(
            lambda: queue.release_suspended(
                task_id, body.worker_id, reason=body.reason
            ),
            "task.released",
        )

    @app.post("/tasks/{task_id}/complete")
    def complete(task_id: str, body: CompleteBody) -> dict:
        return _guard(
            lambda: queue.complete_with_outcome(
                task_id,
                body.worker_id,
                result_ref=body.result_ref,
                result=body.result,
                expected_status=body.expected_status,
                expected_owner_session_id=body.expected_owner_session_id,
                expected_generation=body.expected_generation,
            )
        )

    @app.post("/tasks/{task_id}/abandon")
    def abandon(task_id: str, body: AbandonBody) -> dict:
        return _guard(
            lambda: queue.abandon(
                task_id, worker_id=body.worker_id, permitted=body.permitted, reason=body.reason
            ),
            "task.abandoned",
        )

    @app.post("/tasks/{task_id}/heartbeat")
    def heartbeat(task_id: str, body: WorkerBody) -> dict:
        return _guard(lambda: queue.heartbeat(task_id, body.worker_id))

    @app.post("/tasks/{task_id}/activity")
    def activity(task_id: str, body: ActivityBody) -> dict:
        return _guard(
            lambda: queue.set_activity(
                task_id, body.activity, reservation_key=body.reservation_key
            )
        )

    @app.post("/tasks/{task_id}/owner-session")
    def bind_owner_session(task_id: str, body: OwnerSessionBody) -> dict:
        return _guard(
            lambda: queue.bind_owner_session(
                task_id,
                body.worker_id,
                body.owner_session_id,
                expected_generation=body.expected_generation,
            ),
            "task.owner_session_bound",
        )

    @app.post("/tasks/{task_id}/progress")
    def progress(task_id: str, body: ProgressBody) -> dict:
        return _guard(
            lambda: queue.record_progress(
                task_id,
                body.worker_id,
                phase=body.phase,
                summary=body.summary,
                blocker=body.blocker,
                pr=body.pr,
            ),
            "task.progress",
        )

    @app.post("/tasks/{task_id}/detach")
    def detach(task_id: str) -> dict:
        return _guard(lambda: queue.detach(task_id), "task.detached")

    @app.post("/tasks/{task_id}/card")
    def set_card(task_id: str, body: CardBody) -> dict:
        """Attach a card to a held task (marks it awaiting-steer when the card
        carries a ``request_input`` form)."""
        return _guard(
            lambda: queue.set_card(task_id, body.worker_id, card=body.card),
            "task.card",
        )

    @app.post("/tasks/{task_id}/steer")
    def steer(task_id: str, body: SteerBody) -> dict:
        """Atomically persist an answer, state transition, and wake outbox row."""
        message = body.message or (
            f"The operator answered your card on task {task_id}. Resume, run "
            f"`agent-dispatch steer take {task_id} --all` to read every pending "
            "answer, and "
            "continue toward your goal."
        )
        task = _guard(
            lambda: queue.submit_steer(
                task_id,
                fields=body.fields,
                sender=body.sender,
                wake_requested=body.wake,
                wake_message=message,
            ),
            "task.steer",
        )
        owner = task.get("owner")
        return {
            **task,
            "steer_woken": None,
            "steer_wake_status": (
                task.get("wake_status")
                if body.wake and owner
                else "no_owner" if body.wake else "not_requested"
            ),
        }

    @app.post("/tasks/{task_id}/steer/take")
    def steer_take(task_id: str, body: SteerTakeBody) -> dict:
        """Consume the next pending steer for a task the worker owns (or null)."""
        try:
            steer = queue.take_steer(
                task_id, body.worker_id, all_pending=body.all_pending
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        key = "steers" if body.all_pending else "steer"
        return {"task_id": task_id, key: steer}

    @app.get("/tasks/{task_id}/steer-log")
    def get_steer_log(task_id: str) -> list[dict]:
        """The full steer inbox for a task (oldest first)."""
        _require(queue.get(task_id))
        return queue.steer_log(task_id)

    @app.post("/recover")
    def recover() -> dict:
        """Force a liveness GC pass now (requeue tasks whose owner is gone)."""
        counts = queue.reconcile_liveness()
        # Back-compat: keep the old ``recovered`` key alongside the richer counts.
        return {"recovered": counts["requeued"], **counts}

    # -- spawn reservations --------------------------------------------------

    @app.post("/spawn-reservations")
    def reserve_spawn(body: ReserveSpawnBody) -> dict:
        """Atomically reserve the right to spawn an embody worker for a task.

        Returns ``{"reserved": bool, "reservation": {...}}``. ``reserved`` is
        ``False`` when an active reservation already exists (the caller must NOT
        spawn); ``True`` when this caller now owns a fresh (task, attempt) spawn.
        """
        _require(queue.get(body.task_id))
        try:
            reservation, reserved = queue.reserve_spawn(
                body.task_id, reserved_by=body.reserved_by
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        result = _reservation_dict(reservation)
        if reserved:
            bus.publish({"type": "spawn.reserved", "reservation": result})
        return {"reserved": reserved, "reservation": result}

    def _reservation_guard(op) -> dict:
        try:
            return _reservation_dict(op())
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such reservation") else 409
            raise HTTPException(status_code=status, detail=msg) from exc

    @app.post("/spawn-reservations/{key}/spawned")
    def record_spawn(key: str, body: RecordSpawnBody) -> dict:
        result = _reservation_guard(
            lambda: queue.record_spawn(
                key, session_handle=body.session_handle, worktree=body.worktree
            )
        )
        bus.publish({"type": "spawn.spawned", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/worktree")
    def record_spawn_worktree(key: str, body: RecordSpawnWorktreeBody) -> dict:
        result = _reservation_guard(
            lambda: queue.record_spawn_worktree(
                key,
                body.worktree,
                ownership=body.ownership,
                creating_host=body.creating_host,
                driver=body.driver,
            )
        )
        bus.publish({"type": "spawn.worktree_recorded", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/release")
    def request_spawn_release(key: str, body: RequestSpawnReleaseBody) -> dict:
        result = _reservation_guard(
            lambda: queue.request_spawn_release(
                key,
                detail=body.detail,
                disposition=body.disposition,
            )
        )
        bus.publish({"type": "spawn.release_requested", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/fail")
    def fail_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(lambda: queue.fail_spawn(key, detail=body.detail))
        bus.publish({"type": "spawn.failed", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/cold")
    def record_cold(key: str) -> dict:
        result = _reservation_guard(lambda: queue.record_cold(key))
        bus.publish({"type": "spawn.cold", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/settle")
    def settle_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(
            lambda: queue.settle_spawn(
                key,
                detail=body.detail,
                conclusion_state=body.conclusion_state,
                conclusion_detail=body.conclusion_detail,
            )
        )
        bus.publish({"type": "spawn.settled", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/conclusion")
    def record_spawn_conclusion(key: str, body: ReservationDetailBody) -> dict:
        if not body.conclusion_state or body.conclusion_detail is None:
            raise HTTPException(
                status_code=400,
                detail="conclusion_state and conclusion_detail are required",
            )
        result = _reservation_guard(
            lambda: queue.record_spawn_conclusion(
                key,
                conclusion_state=body.conclusion_state or "",
                conclusion_detail=body.conclusion_detail or "",
            )
        )
        bus.publish({"type": "spawn.conclusion", "reservation": result})
        return result

    @app.post("/spawn-reservations/tasks/{task_id}/rearm")
    def rearm_spawn(task_id: str, body: RearmSpawnBody) -> dict:
        try:
            result = queue.rearm_spawn(
                task_id,
                permitted=body.permitted,
                reason=body.reason,
                min_failures=body.min_failures,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        bus.publish({"type": "spawn.rearmed", "rearm": result})
        return result

    @app.get("/spawn-reservations")
    def list_reservations(
        task_id: str | None = None,
        state: str | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        limit: int = 200,
    ) -> list[dict]:
        states = (
            [s.strip() for s in state.split(",") if s.strip()] if state else None
        )
        return [
            _reservation_dict(r)
            for r in queue.list_reservations(
                task_id=task_id,
                state=states,
                repo=repo,
                label=label,
                conclusion_state=conclusion_state,
                resume_requested=resume_requested,
                limit=limit,
            )
        ]

    @app.get("/spawn-reservations/{key}")
    def get_reservation(key: str) -> dict:
        reservation = queue.get_reservation(key)
        if reservation is None:
            raise HTTPException(status_code=404, detail="no such reservation")
        return _reservation_dict(reservation)

    # -- schedule registry ---------------------------------------------------

    @app.post("/schedules")
    def register_schedule(entry: dict) -> dict:
        """Register (or upsert) a recurring schedule. 400 on a malformed entry."""
        try:
            return asdict(queue.register_schedule(entry))
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/schedules")
    def list_schedules(include_paused: bool = True) -> list[dict]:
        return [asdict(r) for r in queue.list_schedules(include_paused=include_paused)]

    @app.get("/schedules/{sid}")
    def get_schedule(sid: str) -> dict:
        rec = queue.get_schedule(sid)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such schedule")
        return asdict(rec)

    @app.delete("/schedules/{sid}")
    def remove_schedule(sid: str) -> dict:
        if not queue.remove_schedule(sid):
            raise HTTPException(status_code=404, detail="no such schedule")
        return {"removed": True, "id": sid}

    @app.post("/schedules/{sid}/pause")
    def pause_schedule(sid: str) -> dict:
        try:
            return asdict(queue.set_schedule_paused(sid, True))
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/schedules/{sid}/resume")
    def resume_schedule(sid: str) -> dict:
        try:
            return asdict(queue.set_schedule_paused(sid, False))
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- supervisor registrations --------------------------------------------

    @app.post("/registrations")
    def register_registration(body: RegistrationBody) -> dict:
        """Register (or upsert) a supervision unit; return its handle. 400 on a
        malformed kind/spec."""
        try:
            return asdict(
                queue.register_registration(
                    body.kind,
                    body.spec,
                    reg_id=body.id,
                    machine=body.machine,
                    env=body.env,
                )
            )
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/registrations")
    def list_registrations(
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[dict]:
        return [
            asdict(r)
            for r in queue.list_registrations(
                kind=kind, machine=machine, env=env, include_paused=include_paused
            )
        ]

    @app.get("/registrations/{rid}")
    def get_registration(rid: str) -> dict:
        rec = queue.get_registration(rid)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such registration")
        return asdict(rec)

    @app.delete("/registrations/{rid}")
    def remove_registration(rid: str) -> dict:
        if not queue.remove_registration(rid):
            raise HTTPException(status_code=404, detail="no such registration")
        return {"removed": True, "id": rid}

    @app.post("/registrations/{rid}/status")
    def set_registration_status(rid: str, body: RegistrationStatusBody) -> dict:
        try:
            return asdict(queue.set_registration_status(rid, body.status))
        except TaskError as exc:
            code = 404 if str(exc).startswith("no such registration") else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    # -- schedule job-leases -------------------------------------------------

    @app.post("/schedule-leases/{scope}/acquire")
    def acquire_lease(scope: str, body: ScheduleLeaseBody) -> dict:
        lease, granted = queue.acquire_schedule_lease(
            scope, body.holder, holder_session=body.holder_session, ttl=body.ttl
        )
        return {"granted": granted, "lease": asdict(lease)}

    @app.post("/schedule-leases/{scope}/release")
    def release_lease(scope: str, body: ReleaseLeaseBody) -> dict:
        try:
            released = queue.release_schedule_lease(scope, body.holder, force=body.force)
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"released": released, "scope": scope}

    @app.get("/schedule-leases")
    def list_leases() -> list[dict]:
        return [asdict(lease) for lease in queue.list_schedule_leases()]

    @app.get("/schedule-leases/{scope}")
    def get_lease(scope: str) -> dict | None:
        lease = queue.get_schedule_lease(scope)
        return asdict(lease) if lease else None

    # -- external producer resource reservations ----------------------------

    @app.post("/resource-reservations/acquire")
    def acquire_resource_reservation(
        body: AcquireResourceReservationBody,
    ) -> dict:
        try:
            reservation, granted = queue.acquire_resource_reservation(
                body.key, body.owner, ttl=body.ttl, token=body.token
            )
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        payload = asdict(reservation)
        if not granted:
            payload.pop("token", None)
        return {"granted": granted, "reservation": payload}

    @app.post("/resource-reservations/bind")
    def bind_resource_reservation(body: BindResourceReservationBody) -> dict:
        try:
            return asdict(
                queue.bind_resource_reservation(
                    body.key, body.owner, body.token, body.task_id
                )
            )
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/resource-reservations/release")
    def release_resource_reservation(
        body: ReleaseResourceReservationBody,
    ) -> dict:
        try:
            released = queue.release_resource_reservation(
                body.key, body.owner, body.token
            )
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"released": released, "key": body.key}

    @app.get("/resource-reservations")
    def list_resource_reservations(
        owner_prefix: str | None = None,
        task_id: str | None = None,
    ) -> list[dict]:
        return [
            asdict(reservation)
            for reservation in queue.list_resource_reservations(
                owner_prefix=owner_prefix, task_id=task_id
            )
        ]

    if mcp_app is not None:
        # Mounted last so the coordinator's own routes take precedence.
        app.mount("/mcp", mcp_app)

    return app
