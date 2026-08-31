"""Generic embody spawn supervisor -- turn queued tasks into host embody sessions.

The supervisor is the delegation layer's answer to "a queued task should become
exactly one host-side embody autopilot, durably." It sits on top of the
:mod:`~agent_dispatch.queue` **spawn-reservation** primitive (see
``docs/spawn-supervisor.md``) and is deliberately **generic**: no producer- or
consumer-specific logic leaks into it.

Safety is the whole point, so the loop is built around a single hard invariant:

    **A task is spawned only when a fresh spawn reservation is acquired for it.**

Because ``reserve_spawn`` returns ``reserved=False`` whenever an *active*
(``reserving``/``spawned``) reservation already exists for a task, a task that is
already being spawned -- or was spawned and later re-queued (e.g. its lease
expired while the embody is merely slow) -- is **never** spawned a second time.
Lease expiry is *not* treated as death: a re-queued task keeps its ``spawned``
reservation and is skipped, so a slow-but-alive embody can never be
double-spawned (the exact failure this component exists to prevent).

A reservation is released for a **fresh** spawn only when its task reaches a
**terminal** state (``completed``/``abandoned`` -> ``reconcile`` settles it) or
when an operator explicitly fails it (having confirmed the embody is gone). That
means **auto-recovery of a genuinely dead-but-non-terminal embody is
intentionally NOT done here** -- it requires embody-session *liveness detection*
(so lease expiry can be trusted as death and the supervisor can drive the
heartbeat of a live-but-quiet worker). That liveness-aware slice is future work;
until then, a dead embody's task is held (its ``spawned`` reservation blocks
re-spawn) and surfaced for a human, which is the safe default.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .client import DispatchClient, DispatchError
from .queue import SpawnState, Status

log = logging.getLogger("agent-dispatch.supervisor")

#: A spawn function: given a task snapshot, launch a worker and report
#: ``(ok, handle)`` where ``handle`` carries ``session``/``worktree`` (on
#: success) or ``error`` (on failure).
SpawnFn = Callable[[dict], "tuple[bool, dict]"]

#: A liveness probe: ``(worktree, machine) -> session dict`` when the embodied
#: session is **confirmed alive**, else ``None`` (dead *or* unresolvable).
LivenessFn = Callable[[str, "str | None"], "dict | None"]

#: A liveness **verdict** resolver: ``(worktree, machine, owner_session_id) ->
#: 'live' | 'gone' | 'unknown'`` (identity-keyed; ``unknown`` is never treated as
#: death). Injectable so tests drive verdicts deterministically.
VerdictFn = Callable[[str, "str | None", "str | None"], str]

#: A nudge sender: ``(worktree, machine, task) -> sent?``. Delivers a non-blocking
#: steering message to a stalled-but-live embodied session. Injectable for tests.
NudgeFn = Callable[[str, "str | None", dict], bool]

#: A turn-state resolver: ``(worktree, machine) -> 'running' | 'idle' | None``.
#: Reads an embodied worker's coarse turn state (the derived turn boundary the
#: coordination layer computes from its session events); ``None`` when the worker
#: has no observable live session. Injectable so tests drive turn boundaries
#: deterministically.
TurnStateFn = Callable[[str, "str | None"], "str | None"]

_TERMINAL = frozenset({Status.COMPLETED, Status.ABANDONED})
_LEASED = frozenset({Status.CLAIMED, Status.STARTED})


def _default_liveness(worktree: str, machine: str | None) -> dict | None:
    """Resolve an embodied session's liveness via the agent-bridge registry.

    Delegates to :func:`agent_dispatch.tracking.resolve_live_session` (shells the
    ``agent-bridge`` CLI, cross-machine over SSH when the owner is remote). All
    failure modes collapse to ``None`` -- so ``None`` means "not confirmed alive",
    which is why the supervisor only *heartbeats* on a positive result and never
    treats ``None`` as proof-of-death.
    """
    from . import tracking

    return tracking.resolve_live_session(worktree, machine=machine)


def _reservation_made_progress(reservation: dict, task: dict) -> bool:
    """Whether this spawned body durably advanced the task after reservation.

    A headless body commonly ends its one turn after posting a card/progress beat.
    That is a successful embodiment round, not a failed spawn attempt. Compare the
    durable activity timestamps to this reservation so stale progress from an
    earlier body cannot mask a newly crashing replacement.
    """
    try:
        reserved_at = float(reservation.get("reserved_at") or 0)
    except (TypeError, ValueError):
        reserved_at = 0.0
    timestamps: list[object] = []
    card = task.get("card")
    if isinstance(card, dict):
        timestamps.append(card.get("ts"))
    progress = task.get("latest_progress")
    if isinstance(progress, str):
        try:
            progress = json.loads(progress)
        except json.JSONDecodeError:
            progress = None
    if isinstance(progress, dict):
        timestamps.append(progress.get("ts"))
    for value in timestamps:
        try:
            if float(value) > reserved_at:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _default_verdict(
    worktree: str, machine: str | None, owner_session_id: str | None
) -> str:
    """Resolve an embodied session's liveness to a **tri-state verdict** via the
    agent-bridge registry (shells the CLI, cross-machine over SSH). Delegates to
    :func:`agent_dispatch.tracking.liveness_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import tracking

    return tracking.liveness_verdict(
        worktree, machine=machine, owner_session_id=owner_session_id
    )


def _default_nudge(worktree: str, machine: str | None, task: dict) -> bool:
    """Deliver a non-blocking nudge to a stalled-but-live embodied session.

    Builds a terse *notify*-kind steering message pointing the worker back at its
    goal (or at recording a blocker) and shells it via
    :func:`agent_dispatch.bridge.send_nudge`. Best-effort -- a failed send is not
    fatal (recovery, not the nudge, handles a genuinely-gone worker)."""
    from . import bridge

    tid = task.get("id")
    goal = task.get("goal") or task.get("title") or "your dispatched task"
    message = (
        f"[agent-dispatch] You appear stalled on task {tid} -- no progress "
        f"recorded recently. Goal: {goal}. Continue toward it and record a "
        f"progress beat (agent-dispatch progress {tid} --phase <p> --summary "
        f"<line>), or record a blocker (--blocker <why>); if it is already done, "
        f"complete it; if it is not yours, yield it."
    )
    return bridge.send_nudge(worktree, message)


def _default_turn_state(worktree: str, machine: str | None) -> str | None:
    """Resolve an embodied worker's coarse **turn state** via the agent-bridge
    registry (shells the CLI, cross-machine over SSH).

    Reads the ``turn_state`` field of the worktree's live session -- the coarse
    ``running``/``idle`` boundary the coordination layer *derives from its own
    session events* (``assistant.turn_end``), so sampling it here reads the same
    turn signal without agent-dispatch subscribing to a raw event stream or
    importing agent-bridge. Every failure mode (no CLI/ssh, unreachable bridge,
    no live session, no turn signal yet) collapses to ``None`` -- so ``None`` means
    "no observable turn boundary", which the reactive wait treats as *no signal*
    (falling back to the periodic poll), never as a turn-end.
    """
    from . import tracking

    session = tracking.resolve_live_session(worktree, machine=machine)
    if not session:
        return None
    state = session.get("turn_state")
    return state if isinstance(state, str) and state else None


def _worktree_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.worktree_from_owner(owner)


def _machine_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.machine_from_owner(owner)


#: A **fleet-body** liveness verdict resolver: ``(host, bridge_session_id) ->
#: 'live' | 'gone' | 'unknown'``. Probes a headless fleet body's agent-bridge
#: session on its pool host over SSH; ``unknown`` is never treated as death.
#: Injectable so tests drive verdicts deterministically.
FleetVerdictFn = Callable[[str, str], str]
FleetActivityFn = Callable[[str, str], str | None]

#: A **local-body** liveness verdict resolver: ``(bridge_session_id) ->
#: 'live' | 'gone' | 'unknown'``. Probes a *local* headless body's agent-bridge
#: session on this host (no SSH); ``unknown`` is never treated as death.
#: Injectable so tests drive verdicts deterministically.
LocalBodyVerdictFn = Callable[[str], str]

#: Prefix stamped on the reservation ``session_handle`` of a headless fleet body,
#: encoding its recovery handle as ``fleet-body:<host>:<bridge-session-id>`` (see
#: :meth:`agent_dispatch.fleet.FleetSpawner.__call__`).
_FLEET_BODY_PREFIX = "fleet-body:"

#: Prefix stamped on the reservation ``session_handle`` of a **local** headless
#: body, encoding its recovery handle as ``local-body:<bridge-session-id>`` (see
#: :func:`make_headless_spawn`). Unlike a fleet body there is no host component --
#: the session lives on *this* machine's agent-bridge daemon.
_LOCAL_BODY_PREFIX = "local-body:"


def _parse_fleet_body_handle(session_handle: str | None) -> tuple[str, str] | None:
    """Decode a ``fleet-body:<host>:<bridge-session-id>`` reservation handle.

    Returns ``(host, bridge_session_id)`` for a headless fleet body whose recovery
    handle was captured at spawn, else ``None`` (a worktree-backed embody, a
    fleet body whose session id could not be captured, or any other handle).
    """
    if not session_handle or not session_handle.startswith(_FLEET_BODY_PREFIX):
        return None
    rest = session_handle[len(_FLEET_BODY_PREFIX):]
    host, _sep, sid = rest.partition(":")
    if not host or not sid:
        return None
    return host, sid


def _default_fleet_verdict(host: str, bridge_session_id: str) -> str:
    """Resolve a headless fleet body's liveness to a tri-state verdict by probing
    its agent-bridge session on the pool ``host`` over SSH. Delegates to
    :func:`agent_dispatch.embody.fleet_body_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import embody

    return embody.fleet_body_verdict(host, bridge_session_id)


def _default_fleet_activity(host: str, bridge_session_id: str) -> str | None:
    from . import embody

    return embody.fleet_body_activity(host, bridge_session_id)


def _parse_local_body_handle(session_handle: str | None) -> str | None:
    """Decode a ``local-body:<bridge-session-id>`` reservation handle.

    Returns the local agent-bridge ``session_id`` for a headless body embodied on
    *this* machine whose recovery handle was captured at spawn, else ``None`` (a
    worktree-backed embody, a fleet body, a headless body whose session id could
    not be captured, or any other handle).
    """
    if not session_handle or not session_handle.startswith(_LOCAL_BODY_PREFIX):
        return None
    sid = session_handle[len(_LOCAL_BODY_PREFIX):]
    return sid or None


def _default_local_body_verdict(bridge_session_id: str) -> str:
    """Resolve a *local* headless body's liveness to a tri-state verdict by
    probing its agent-bridge session on this host (no SSH). Delegates to
    :func:`agent_dispatch.embody.local_body_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import embody

    return embody.local_body_verdict(bridge_session_id)


def _tracking():
    """Lazy accessor for the ``tracking`` module (its verdict constants)."""
    from . import tracking

    return tracking


def make_embody_spawn(
    *, driver: str = "agent-dispatch", verify_timeout: int = 0, route: str = ""
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker via ``agent-worktrees``.

    Degrades cleanly: if the ``agent-worktrees`` CLI is absent, the spawn reports
    failure (the supervisor fails the reservation, leaving the task queued).

    The supervisor runs CWD-neutral (a service whose working directory is its own
    runtime dir, not any repo), so the spawn **names the target project
    explicitly** -- derived from the task's lane -- via embody's ``--project``
    global, rather than relying on git-like CWD discovery (which would fail with
    "Could not resolve a project for 'embody'"). See the
    ``project-scoped-invocation`` pattern.

    ``route`` is the coordinator routing intent handed to the worker's
    ``agent-dispatch`` commands (``""`` for local discovery, ``" --shared"`` for
    the shared moniker); never a raw ``--url`` (the caller rejects that).
    """
    from . import embody

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"embody-{uuid.uuid4().hex[:8]}"
        try:
            result = embody.spawn_embodied_worker(
                task["id"],
                worker_id=worker_id,
                driver=driver,
                project=embody.project_for_task(task),
                route=route,
                verify_timeout=verify_timeout,
            )
        except embody.EmbodyUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            return False, {"error": (result.stderr or "").strip()[:200] or "nonzero exit"}
        handle = embody.parse_handle(result)
        return True, handle
    return spawn


def make_headless_spawn(
    *, agent: str = "task-worker", route: str = "",
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker as a **headless
    agent-bridge ACP** session -- no mux, no CLI-start-prompt.

    This is the embodiment for **self-contained, bounded** tasks that need no
    human attach: a scheduled/reactive sweep that claims a task, runs it to a
    deliberate completion, and is torn down. It sidesteps the CLI-start-prompt
    delivery path entirely (a seeded CLI session can race the input caret and
    never deliver its seed), so a headless-marked task never deadlocks on that
    path.

    It reuses the **same autopilot seed** as the CLI backend
    (:func:`agent_dispatch.embody.autopilot_worker_prompt` -- claim-under-identity,
    contract-net evaluation, deferred completion), so a headless-embodied task is
    driven identically to a CLI-embodied one; only the *body* differs. Degrades
    cleanly: if the ``agent-bridge`` CLI is absent, the spawn reports failure (the
    supervisor fails the reservation, leaving the task queued).

    A headless body is not a parallel worktree, so no worktree handle is recorded
    -- the supervisor's worktree-keyed lease heartbeat does not apply to it.
    Instead it records a ``local-body:<bridge-session-id>`` recovery handle, so a
    body that ends before completing (crash, or an explicit ``agent-bridge end``
    after a run cancel) is **liveness-recovered**: the supervisor probes the
    session locally and, on a confirmed-gone verdict, settles the orphaned
    ``spawned`` reservation -- freeing the label's concurrency slot instead of
    starving it. Reconciliation still settles the reservation when the task
    reaches a terminal state.

    ``route`` is the coordinator routing intent handed to the worker's
    ``agent-dispatch`` commands (``""`` for local discovery, ``" --shared"`` for
    the shared moniker); never a raw ``--url`` (the caller rejects that).
    """
    from . import bridge, embody

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"headless-{uuid.uuid4().hex[:8]}"
        seed = embody.autopilot_worker_prompt(
            task["id"], worker_id=worker_id, route=route
        )
        try:
            result = bridge.spawn_worker(
                task["id"],
                agent=agent,
                worker_id=worker_id,
                prompt=seed,
                wait=False,
                json_output=True,
            )
        except bridge.BridgeUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            return False, {"error": (result.stderr or "").strip()[:200] or "nonzero exit"}
        # Capture the created local agent-bridge session id and encode it as a
        # `local-body:<sid>` recovery handle so a *gone* body (ended/cancelled)
        # is liveness-recovered by the supervisor -- freeing its spawn slot --
        # instead of orphaning its `spawned` reservation forever. When the id
        # can't be captured, fall back to the opaque worker id (degrade safe:
        # unprobeable, exactly the pre-fix behavior).
        sid = embody.parse_fleet_body_session(result)
        handle = f"{_LOCAL_BODY_PREFIX}{sid}" if sid else worker_id
        return True, {"session": handle, "worktree": None}

    return spawn


def make_label_routed_spawn(
    default: SpawnFn, *, overrides: Mapping[str, SpawnFn]
) -> SpawnFn:
    """Return a :data:`SpawnFn` that routes a task to an **override** backend when
    any of its labels has one, else to the ``default`` backend.

    This lets a *single* supervisor embody different task classes with different
    bodies -- e.g. self-contained sweep labels headless (bridge) while
    interactive/standalone worktree work stays CLI-first (embody) -- without
    splitting into multiple services. When a task carries several overridden
    labels, the first match in the task's own label order wins. With no overrides,
    the ``default`` is returned unwrapped (no behavior change).
    """
    if not overrides:
        return default

    def spawn(task: dict) -> tuple[bool, dict]:
        for label in task.get("labels") or []:
            fn = overrides.get(label)
            if fn is not None:
                return fn(task)
        return default(task)

    return spawn


class Supervisor:
    """Reserve -> spawn -> record, with terminal-state reconciliation.

    ``max_concurrent`` caps the number of in-flight spawns (``reserving`` +
    ``spawned`` reservations). ``max_attempts`` bounds failed spawn attempts per
    task before it is **dead-lettered** (held, no longer auto-retried; 0 disables
    the bound). ``label_max_attempts`` optionally overrides that bound **per
    label** (agent type): a task carrying an overridden label uses the override
    instead of the global ``max_attempts`` (the most-permissive override wins when
    a task carries several). This decouples unrelated task classes -- e.g.
    reviving one label's dead-lettered tasks (raise its bound) without also
    reviving another label's stale tasks. ``repo`` scopes the lane; ``labels`` (if
    given) restricts spawning to queued tasks carrying at least one of them -- the
    **opt-in** so a supervisor only embodies work explicitly marked for autopilot.
    """

    def __init__(
        self,
        client: DispatchClient,
        *,
        spawn_fn: SpawnFn,
        repo: str | None = None,
        labels: Sequence[str] | None = None,
        max_concurrent: int = 1,
        max_attempts: int = 3,
        label_max_attempts: Mapping[str, int] | None = None,
        supervisor_id: str | None = None,
        heartbeat: bool = True,
        publish_activity: bool = False,
        recover: bool = True,
        nudge: bool = True,
        reactive: bool = True,
        reactive_interval: float = 2.0,
        stall_seconds: float = 600.0,
        liveness_fn: LivenessFn | None = None,
        verdict_fn: VerdictFn | None = None,
        fleet_verdict_fn: FleetVerdictFn | None = None,
        fleet_activity_fn: FleetActivityFn | None = None,
        local_body_verdict_fn: LocalBodyVerdictFn | None = None,
        nudge_fn: NudgeFn | None = None,
        turn_state_fn: TurnStateFn | None = None,
        capacity_gate: Callable[[dict], bool] | None = None,
        evaluator: Any | None = None,
        evaluate_limit: int = 100,
    ):
        self.client = client
        self.spawn_fn = spawn_fn
        self.repo = repo
        self.labels = set(labels) if labels else None
        self.max_concurrent = max(1, int(max_concurrent))
        #: Bound on failed spawn attempts per task before it is dead-lettered
        #: (held, no longer auto-retried). 0 disables the bound (retry forever).
        self.max_attempts = max(0, int(max_attempts))
        #: Per-label override of ``max_attempts`` (0 = retry-forever for that
        #: label). A task's effective bound is the max override across its labels,
        #: falling back to the global ``max_attempts`` when none apply.
        self.label_max_attempts = {
            str(k): max(0, int(v)) for k, v in (label_max_attempts or {}).items()
        }
        self.supervisor_id = supervisor_id or f"supervisor-{uuid.uuid4().hex[:8]}"
        self.heartbeat = heartbeat
        #: Publish exact execution state into coordinator-owned task rows. This
        #: keeps read surfaces pure API queries instead of shelling to bridge.
        self.publish_activity = publish_activity
        #: When True, release the spawn reservation of a *confirmed-gone* embody
        #: so its task can be re-embodied (auto-recovery -- see
        #: :meth:`recover_gone`). Liveness-gated: only a ``gone`` verdict releases;
        #: ``unknown``/``live`` never do. Off restores the hold-for-a-human default.
        self.recover = recover
        #: When True, a confirmed-ALIVE worker that has recorded no progress for
        #: ``stall_seconds`` is nudged (a non-blocking steering message), at most
        #: once per stall window -- prod, don't kill (*nudge-before-recover*).
        self.nudge = nudge
        #: Quiet-but-live window before a nudge. 0 disables nudging.
        self.stall_seconds = max(0.0, float(stall_seconds))
        self.liveness_fn = liveness_fn or _default_liveness
        #: Tri-state verdict resolver used by :meth:`recover_gone`. Injectable so
        #: tests drive ``gone``/``live``/``unknown`` deterministically.
        self.verdict_fn = verdict_fn or _default_verdict
        #: Tri-state verdict resolver for **headless fleet bodies** (probes the
        #: body's agent-bridge session on its pool host over SSH). Used by
        #: :meth:`recover_gone` (re-embody a confirmed-gone body) and
        #: :meth:`hold_live_leases` (heartbeat a confirmed-live one). Injectable
        #: for tests; ``unknown`` is never treated as death.
        self.fleet_verdict_fn = fleet_verdict_fn or _default_fleet_verdict
        self.fleet_activity_fn = fleet_activity_fn or _default_fleet_activity
        #: Tri-state verdict resolver for a **local headless body** (probes the
        #: body's agent-bridge session on this host, no SSH). Used by
        #: :meth:`recover_gone` (re-embody/free a confirmed-gone local body) and
        #: :meth:`hold_live_leases` (heartbeat a confirmed-live one). Injectable
        #: for tests; ``unknown`` is never treated as death.
        self.local_body_verdict_fn = (
            local_body_verdict_fn or _default_local_body_verdict
        )
        #: Nudge sender used by :meth:`nudge_stalled`. Injectable for tests.
        self.nudge_fn = nudge_fn or _default_nudge
        #: When True, the inter-cycle wait in :meth:`serve` is **interruptible by a
        #: turn boundary**: it returns early when an embodied worker settles a turn
        #: (goes idle), so a completed goal is reconciled and the next task embodied
        #: promptly instead of only on the poll cadence (*react-to-turn-end*). The
        #: periodic poll remains the correctness floor; off restores a plain sleep.
        self.reactive = reactive
        #: Sub-sampling cadence for the reactive wait -- how often
        #: :meth:`wait_for_turn_end` re-checks embodied workers' turn state within a
        #: single inter-cycle wait. Clamped to a sane floor so it never busy-loops.
        self.reactive_interval = max(0.25, float(reactive_interval))
        #: Turn-state resolver used by :meth:`wait_for_turn_end`. Injectable for tests.
        self.turn_state_fn = turn_state_fn or _default_turn_state
        #: task_id -> last nudge ts (in-memory cooldown so a persistently-quiet
        #: live worker is nudged at most once per stall window, not every cycle).
        self._last_nudge: dict[str, float] = {}
        #: Optional pre-reservation capacity gate. When it returns False for a
        #: task, the task is **skipped this cycle without a reservation** -- so a
        #: transient "no capacity" (e.g. a fleet pool that is entirely asleep)
        #: defers the task instead of burning a spawn attempt toward the
        #: dead-letter bound. Default (None) always admits, preserving the local
        #: spawn behavior exactly.
        self.capacity_gate = capacity_gate
        #: Optional **evaluator** (a producer's lifecycle handler with an
        #: ``evaluate(event) -> [decision]`` method, e.g.
        #: :class:`~agent_dispatch.producers.evaluator.SpecEvaluator`). When set,
        #: :meth:`poll_once` runs :meth:`advance_via_evaluator` each cycle: it feeds
        #: each newly-terminal task's lifecycle event to the evaluator and applies
        #: the resulting decisions (emit a follow-up task). This is the
        #: **service-driven** half of *a-loop-runs-with-or-without-a-service* -- a
        #: standing supervisor advances a domain's loop across events without a
        #: bespoke module. Idempotent: emitted follow-ups carry the evaluator's
        #: ``dedup_key`` (dedup-before-create), and an in-process guard fires each
        #: task's terminal event at most once.
        self.evaluator = evaluator
        #: Max terminal tasks scanned per evaluator pass (newest first).
        self.evaluate_limit = max(1, int(evaluate_limit))
        #: Task ids whose terminal lifecycle event has already been dispatched to
        #: the evaluator this process (dedup_key is the cross-restart guard).
        self._evaluated: set[str] = set()
        #: Last compact dead-letter signature emitted by this process. Unchanged
        #: task ids, failed counts, and caps stay quiet across poll cycles.
        self._dead_letter_signature: tuple[tuple[str, int, int], ...] = ()

    # -- helpers -------------------------------------------------------------

    def _eligible(self, now: float) -> list[dict]:
        """Queued, due tasks in the lane matching the label opt-in (oldest first)."""
        tasks = self.client.list(repo=self.repo, status=Status.QUEUED, limit=200)
        out: list[dict] = []
        for t in tasks:
            if (t.get("not_before") or 0) > now:
                continue  # deferred: not due yet
            if t.get("awaiting_steer"):
                continue  # blocked on the operator; Confirm clears this to wake
            if self.labels is not None and not (self.labels & set(t.get("labels") or [])):
                continue  # not opted in
            out.append(t)
        out.sort(key=lambda t: t.get("created_at") or 0)
        return out

    def _active_reservations(self) -> list[dict]:
        reservations = self.client.list_reservations(
            state=f"{SpawnState.RESERVING},{SpawnState.SPAWNED}", limit=500
        )
        active: list[dict] = []
        for reservation in reservations:
            try:
                task = self.client.get(reservation["task_id"])
            except DispatchError:
                active.append(reservation)
                continue
            if task.get("status") != Status.SUSPENDED:
                active.append(reservation)
        return active

    # -- phases --------------------------------------------------------------

    def _completion_detail(self, task: dict) -> str:
        """Settle-detail for a terminal task, with completion-claim verification.

        Implements *verify-the-completion-claim*: a **goal-bearing** task that
        reaches ``completed`` is corroborated against what was recorded -- a
        result reference, or at least one progress-log entry. A goal completed
        with **neither** is not trusted at face value: it is flagged in the
        reservation detail and logged, so an empty "done" is **held for review**
        rather than silently accepted. A plain one-shot task (no goal) keeps the
        simple deferred-completion contract.
        """
        status = task.get("status")
        if status != Status.COMPLETED or not task.get("goal"):
            return f"task {status}"
        if task.get("result_ref"):
            return "task completed (result-ref recorded)"
        if task.get("result") is not None or task.get("has_result"):
            return "task completed (structured result recorded)"
        try:
            has_progress = bool(self.client.progress_log(task["id"]))
        except DispatchError:
            has_progress = True  # can't read the log -> don't cry wolf
        if has_progress:
            return "task completed (progress recorded)"
        log.warning(
            "task %s completed as a GOAL with no result-ref and no recorded "
            "progress -- completion unverified, flagged for review",
            task["id"],
        )
        return (
            "completion UNVERIFIED: goal-bearing task marked done with no "
            "result-ref and no progress -- held for review"
        )

    def reconcile(self) -> int:
        """Settle ``spawned`` reservations whose task reached a terminal state.

        This is the *only* automatic release of a reservation -- and only for a
        provably-finished task -- so it can never free a still-running spawn for a
        double-launch. A completed **goal** is verified (*verify-the-completion-
        claim*) as it settles: an empty "done" is flagged in the reservation
        detail rather than silently accepted. Returns the number settled.
        """
        settled = 0
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue  # task vanished; leave the reservation for a human
            if task.get("status") in _TERMINAL:
                try:
                    self.client.settle_spawn(res["key"], detail=self._completion_detail(task))
                    settled += 1
                except DispatchError:
                    pass
        return settled

    def hold_live_leases(self) -> int:
        """Heartbeat the lease of every **confirmed-alive** embodied worker.

        For each ``spawned`` reservation whose task is leased (``claimed``/
        ``started``), probe the embody session's liveness; when it is *confirmed
        alive*, send a lease heartbeat on the task's behalf. This keeps a
        live-but-quiet worker (one not emitting progress) from having its lease
        expire and being wrongly recovered/re-spawned -- the exact "don't trust
        the LLM to emit progress to hold its lease" gap.

        Safety: heartbeats fire **only** on a positive liveness result. A ``None``
        probe (dead *or* unreachable bridge) is never treated as alive *or* as
        proof-of-death here -- the lease simply rides its natural course, so a
        genuinely dead worker's lease still expires (its task is then held for
        recovery), and a transient bridge miss cannot mask a live worker (the
        worker's own activity still extends its lease). Returns the count held.
        """
        tracking = _tracking()
        local_by_id: dict[str, dict] | None = None
        held = 0
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            worktree = res.get("worktree")
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if task.get("status") == Status.SUSPENDED:
                continue
            owner = task.get("owner")
            # Headless fleet body: probe its agent-bridge session on the pool host;
            # heartbeat the origin lease only on a *confirmed-live* verdict, so a
            # live-but-quiet body (no progress between beats) doesn't have its lease
            # expire and get wrongly re-embodied. unknown/gone -> no heartbeat (the
            # lease rides its course; recover_gone handles a confirmed-gone body).
            fleet = _parse_fleet_body_handle(res.get("session_handle"))
            if fleet is not None:
                host, bridge_sid = fleet
                if self.publish_activity:
                    try:
                        fleet_activity = self.fleet_activity_fn(host, bridge_sid)
                    except Exception:  # best-effort observation, never fatal
                        fleet_activity = None
                    try:
                        self.client.set_activity(
                            task["id"],
                            fleet_activity,
                            reservation_key=res["key"],
                        )
                    except DispatchError:
                        pass
                if not owner:
                    continue
                try:
                    fverdict = self.fleet_verdict_fn(host, bridge_sid)
                except Exception:  # liveness is best-effort -- never fatal
                    fverdict = _tracking().UNKNOWN
                if fverdict == _tracking().LIVE and self.heartbeat:
                    try:
                        self.client.heartbeat(task["id"], owner)
                        held += 1
                    except DispatchError:
                        pass
                continue
            # Local headless body: probe its agent-bridge session on THIS host (no
            # SSH); heartbeat the lease only on a *confirmed-live* verdict, same as
            # the fleet path. unknown/gone -> no heartbeat (the lease rides its
            # course; recover_gone frees a confirmed-gone body).
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if local_sid is not None:
                if self.publish_activity:
                    if local_by_id is None:
                        local_by_id = {
                            str(row.get("session_id")): row
                            for row in tracking.list_local_body_sessions()
                            if isinstance(row, dict) and row.get("session_id")
                        }
                    try:
                        self.client.set_activity(
                            task["id"],
                            tracking.session_activity(local_by_id.get(local_sid)),
                            reservation_key=res["key"],
                        )
                    except DispatchError:
                        pass
                if task.get("status") not in _LEASED:
                    continue
                if not owner:
                    continue
                try:
                    lverdict = self.local_body_verdict_fn(local_sid)
                except Exception:  # liveness is best-effort -- never fatal
                    lverdict = _tracking().UNKNOWN
                if lverdict == _tracking().LIVE and self.heartbeat:
                    try:
                        self.client.heartbeat(task["id"], owner)
                        held += 1
                    except DispatchError:
                        pass
                continue
            if task.get("status") not in _LEASED:
                if self.publish_activity:
                    try:
                        self.client.set_activity(
                            task["id"], None, reservation_key=res["key"]
                        )
                    except DispatchError:
                        pass
                continue
            probe_worktree = worktree or _worktree_from_owner(owner)
            if not probe_worktree or not owner:
                continue
            try:
                session = self.liveness_fn(probe_worktree, _machine_from_owner(owner))
            except Exception:  # liveness is best-effort -- never let a probe be fatal
                session = None
            if not session:
                if self.publish_activity:
                    try:
                        self.client.set_activity(
                            task["id"], None, reservation_key=res["key"]
                        )
                    except DispatchError:
                        pass
                continue  # not confirmed alive -> let the lease ride
            if self.publish_activity:
                try:
                    self.client.set_activity(
                        task["id"],
                        tracking.session_activity(session),
                        reservation_key=res["key"],
                    )
                except DispatchError:
                    pass
            try:
                if self.heartbeat:
                    self.client.heartbeat(task["id"], owner)
                    held += 1
            except DispatchError:
                pass
        return held

    def recover_gone(self) -> int:
        """Release the spawn reservation of a **confirmed-gone** embody so its
        task can be re-embodied -- the auto-recovery half of the liveness model.

        For each ``spawned`` reservation, resolve the embodied session's liveness
        to the tri-state verdict (identity-keyed on the task's captured
        ``owner_session_id``) and act **only on a confirmed** result:

        - ``gone``    -> the embody is provably absent (its worktree is empty, or a
          different session reused it). Release the reservation (``fail_spawn``) so
          the next :meth:`poll_once` can re-reserve and re-embody; the replacement
          resumes from the task's ``progress_log``. A still-leased task is first
          **requeued on the gone owner's behalf** (an on-behalf ``yield_task``,
          across worktree, fleet, and local bodies alike) so re-embody is prompt
          rather than waiting out the lease -- the coordinator's own lease-expiry
          GC is only the backstop. A task whose embody died *before* it claimed is
          already queued.
        - ``live``    -> leave it (:meth:`hold_live_leases` heartbeats it).
        - ``unknown`` -> leave it. A still-starting-up worker, or an unreachable
          bridge, is **never** treated as death -- recovery never fires on
          ignorance (the safety guarantee behind liveness-not-lease).

        A terminal task is settled by :meth:`reconcile`; a dead-lettered one is
        settled here (held, not re-spawned). A body that posted a card/progress
        beat after its reservation is also settled: its turn succeeded, so its
        normal exit must not consume the failed-*spawn* budget. An unproductive
        disappearance uses ``fail_spawn`` and still counts toward dead-lettering.
        Returns the count recovered.
        """
        from . import tracking

        recovered = 0
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue  # task vanished; leave the reservation for a human
            status = task.get("status")
            if status in _TERMINAL:
                continue  # reconcile() settles provably-finished tasks
            if status == Status.SUSPENDED:
                continue  # dormant ownership is intentional, not a gone body
            if status == Status.DEAD_LETTER:
                try:
                    self.client.settle_spawn(res["key"], detail="task dead_lettered")
                except DispatchError:
                    pass
                continue
            owner = task.get("owner")
            # Headless fleet body: no worktree handle, but its recovery handle is
            # the pool host's agent-bridge session -- probe THAT for liveness and
            # release a *confirmed-gone* body so poll_once re-embodies it (the
            # replacement resumes from the task's progress_log). Same tri-state
            # safety as the worktree path: only GONE releases; live/unknown never.
            fleet = _parse_fleet_body_handle(res.get("session_handle"))
            if fleet is not None:
                host, bridge_sid = fleet
                try:
                    fverdict = self.fleet_verdict_fn(host, bridge_sid)
                except Exception:  # liveness is best-effort -- never fatal
                    fverdict = tracking.UNKNOWN
                if fverdict == tracking.GONE:
                    # Requeue the task if the dead body still holds its lease --
                    # yield on its behalf (preserving goal + progress_log) so
                    # re-embody is PROMPT instead of waiting out the 15-min lease
                    # (the origin can't liveness-probe a synthetic owner, so its
                    # own GC would only requeue on expiry). Then release the
                    # reservation so poll_once re-embodies from the recorded
                    # progress. A queued task (body died before claiming) needs no
                    # yield -- just the release.
                    if status in _LEASED and owner:
                        try:
                            self.client.yield_task(
                                task["id"], owner,
                                note="fleet body confirmed gone; requeued for re-embody",
                            )
                        except DispatchError:
                            pass  # lease-expiry GC is the backstop requeue
                    try:
                        detail = f"fleet body confirmed gone ({host}:{bridge_sid})"
                        if _reservation_made_progress(res, task):
                            self.client.settle_spawn(
                                res["key"],
                                detail=f"{detail}; productive turn completed",
                            )
                        else:
                            self.client.fail_spawn(res["key"], detail=detail)
                        recovered += 1
                        log.info(
                            "recovered gone fleet body for task %s (%s); reservation "
                            "released for re-embody",
                            task["id"], res["key"],
                        )
                    except DispatchError:
                        log.exception(
                            "recovery release failed for reservation %s", res["key"]
                        )
                continue  # fleet body handled -> don't fall to the worktree path
            # Local headless body: no worktree handle either, but its recovery
            # handle is THIS host's agent-bridge session -- probe it locally (no
            # SSH) and release a *confirmed-gone* body so poll_once re-embodies it.
            # This is the fix for the orphaned-reservation slot-starve: an
            # ended/cancelled local headless body (e.g. `agent-bridge end
            # <session>` after a run cancel) is now settled automatically instead
            # of holding the label's concurrency slot forever. Same tri-state
            # safety: only GONE releases; live/unknown never.
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if local_sid is not None:
                try:
                    lverdict = self.local_body_verdict_fn(local_sid)
                except Exception:  # liveness is best-effort -- never fatal
                    lverdict = tracking.UNKNOWN
                if lverdict == tracking.GONE:
                    # Requeue the task if the dead body still holds its lease
                    # (yield on its behalf, preserving goal + progress_log) so
                    # re-embody is prompt, then release the reservation. A queued
                    # task (body died before claiming) needs no yield.
                    if status in _LEASED and owner:
                        try:
                            self.client.yield_task(
                                task["id"], owner,
                                note="local body confirmed gone; requeued for re-embody",
                            )
                        except DispatchError:
                            pass  # lease-expiry GC is the backstop requeue
                    try:
                        detail = f"local body confirmed gone ({local_sid})"
                        if _reservation_made_progress(res, task):
                            self.client.settle_spawn(
                                res["key"],
                                detail=f"{detail}; productive turn completed",
                            )
                        else:
                            self.client.fail_spawn(res["key"], detail=detail)
                        recovered += 1
                        log.info(
                            "recovered gone local body for task %s (%s); reservation "
                            "released for re-embody",
                            task["id"], res["key"],
                        )
                    except DispatchError:
                        log.exception(
                            "recovery release failed for reservation %s", res["key"]
                        )
                continue  # local body handled -> don't fall to the worktree path
            worktree = res.get("worktree") or _worktree_from_owner(owner)
            if not worktree:
                continue  # headless / no worktree handle -> not recoverable here
            try:
                verdict = self.verdict_fn(
                    worktree, _machine_from_owner(owner), task.get("owner_session_id")
                )
            except Exception:  # liveness is best-effort -- never let a probe be fatal
                verdict = tracking.UNKNOWN
            if verdict != tracking.GONE:
                continue  # live or unknown -> never recover on ignorance
            try:
                # Requeue the task if the gone owner still holds its lease -- yield
                # on its behalf (preserving goal + progress_log) so re-embody is
                # PROMPT instead of waiting out the lease. This matches the fleet/
                # local body paths above; without it a confirmed-gone worktree
                # owner's task lingers LEASED (not spawn-eligible) until the
                # coordinator's lease-expiry GC requeues it -- a lease-window where
                # the replacement is needlessly delayed (the liveness-not-lease
                # gap). A queued task (embody died before claiming) needs no yield.
                if status in _LEASED and owner:
                    try:
                        self.client.yield_task(
                            task["id"], owner,
                            note="worktree owner confirmed gone; requeued for re-embody",
                        )
                    except DispatchError:
                        pass  # lease-expiry GC is the backstop requeue
                detail = f"owner confirmed gone ({worktree})"
                if _reservation_made_progress(res, task):
                    self.client.settle_spawn(
                        res["key"],
                        detail=f"{detail}; productive turn completed",
                    )
                else:
                    self.client.fail_spawn(res["key"], detail=detail)
                recovered += 1
                log.info(
                    "recovered gone embody for task %s (%s); reservation released "
                    "for re-embody",
                    task["id"], res["key"],
                )
            except DispatchError:
                log.exception("recovery release failed for reservation %s", res["key"])
        return recovered

    def nudge_stalled(self, *, now: float | None = None) -> int:
        """Nudge a worker that is **confirmed alive but has gone quiet** -- no
        progress within ``stall_seconds`` (*nudge-before-recover*).

        A nudge is an attributed, non-blocking steering message; it is **not**
        recovery (that is gated on a *gone* verdict, :meth:`recover_gone`). Only a
        **confirmed-alive** worker is nudged (a ``None`` liveness result is left to
        recovery, never nudged into the void), and at most **once per stall window**
        per task -- so a slow-but-live worker is prodded, never spammed, and elapsed
        quiet never escalates past a prod on its own. Returns the count nudged.
        """
        if not self.stall_seconds:
            return 0
        now = time.time() if now is None else now
        nudged = 0
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if task.get("status") not in _LEASED:
                continue  # only a worker actively holding the task can be stalled
            last = task.get("last_seen_at") or task.get("started_at") or 0
            if (now - last) < self.stall_seconds:
                continue  # recently active -> not stalled
            if (now - self._last_nudge.get(task["id"], 0.0)) < self.stall_seconds:
                continue  # cooldown -> already nudged this window
            owner = task.get("owner")
            worktree = res.get("worktree") or _worktree_from_owner(owner)
            if not worktree:
                continue
            machine = _machine_from_owner(owner)
            try:
                alive = self.liveness_fn(worktree, machine)
            except Exception:  # liveness is best-effort -- never let a probe be fatal
                alive = None
            if not alive:
                continue  # not confirmed alive -> recovery's job, not a nudge
            try:
                if self.nudge_fn(worktree, machine, task):
                    self._last_nudge[task["id"]] = now
                    nudged += 1
                    log.info(
                        "nudged stalled-but-live worker for task %s (%s)",
                        task["id"], worktree,
                    )
            except Exception:  # a failed nudge is never fatal
                log.exception("nudge failed for task %s", task["id"])
        return nudged

    # -- reactive wait (react-to-turn-end) -----------------------------------

    def _embodied_owners(self) -> list[tuple[str, str | None]]:
        """``(worktree, machine)`` for each spawned reservation whose task is
        currently **leased** -- the set of workers with a live turn to react to.

        A headless reservation (no worktree handle) or a task not presently leased
        (``claimed``/``started``) contributes nothing: there is no live turn
        boundary to watch. Deduped, order-stable.
        """
        owners: list[tuple[str, str | None]] = []
        seen: set[tuple[str, str | None]] = set()
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if task.get("status") not in _LEASED:
                continue
            owner = task.get("owner")
            worktree = res.get("worktree") or _worktree_from_owner(owner)
            if not worktree:
                continue
            key = (worktree, _machine_from_owner(owner))
            if key in seen:
                continue
            seen.add(key)
            owners.append(key)
        return owners

    def _safe_turn_state(self, owner: tuple[str, str | None]) -> str | None:
        """Resolve one worker's coarse turn state, swallowing any probe error.

        A resolver failure is treated as *no signal* (``None``), never as a
        turn-end -- so a flaky bridge only costs promptness, never correctness."""
        worktree, machine = owner
        try:
            return self.turn_state_fn(worktree, machine)
        except Exception:  # turn-state is best-effort -- never let a probe be fatal
            return None

    def wait_for_turn_end(
        self,
        timeout: float,
        *,
        sleep: Callable[[float], None] | None = None,
        clock: Callable[[], float] | None = None,
    ) -> bool:
        """Block up to ``timeout`` seconds, returning early on a worker **turn-end**.

        The interruptible inter-cycle wait behind *react-to-turn-end*. It baselines
        each embodied worker's coarse turn state (:meth:`_embodied_owners`), then
        re-samples every :attr:`reactive_interval` seconds and returns ``True`` as
        soon as any worker is observed transitioning **running -> idle** (a turn just
        settled), so the caller runs its next supervision pass immediately.

        Returns ``False`` when ``timeout`` elapses with no turn-end observed -- the
        periodic poll then fires as the correctness **floor**. Only a *transition* to
        idle wakes it: a worker already idle at entry is not a fresh turn-end (that
        would wake instantly every cycle), and a worker with no observable turn state
        (``None``) contributes no signal -- so with no embodied workers, or no
        reachable bridge, the wait degrades to exactly a plain sleep. ``sleep`` and
        ``clock`` are injectable for deterministic tests.
        """
        sleep = sleep or time.sleep
        clock = clock or time.monotonic
        if timeout <= 0 or not self.reactive:
            return False
        owners = self._embodied_owners()
        if not owners:
            sleep(timeout)  # nothing to react to -> one plain sleep of the interval
            return False
        seen = {o: self._safe_turn_state(o) for o in owners}
        deadline = clock() + timeout
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                return False
            sleep(min(self.reactive_interval, remaining))
            for o in owners:
                state = self._safe_turn_state(o)
                if state == "idle" and seen.get(o) == "running":
                    return True  # a turn just settled -> supervise now
                seen[o] = state

    def _effective_max_attempts(self, task: dict) -> int:
        """The dead-letter bound for ``task``: the most-permissive per-label
        override across its labels, else the global ``max_attempts`` (0 = no
        bound)."""
        overrides = [
            self.label_max_attempts[label]
            for label in (task.get("labels") or [])
            if label in self.label_max_attempts
        ]
        return max(overrides) if overrides else self.max_attempts

    def advance_via_evaluator(self) -> int:
        """Feed each newly-terminal task's lifecycle event to the evaluator and
        apply its decisions (the service-driven loop-advancement pass).

        Lists recent terminal tasks in the lane (completed / abandoned), and for
        each one not yet seen this process, synthesizes the coordinator-shaped
        lifecycle event ``{"type": "task.completed"|"task.abandoned", "task":
        {...}}``, runs the evaluator, and applies the returned decisions through
        :func:`~agent_dispatch.producers.evaluator.apply_decisions` (an ``Emit``
        creates a follow-up task in this lane). Returns the number of follow-up
        tasks emitted.

        Best-effort and non-fatal: a bad evaluator or a failed create is logged
        and skipped, never allowed to abort the supervision cycle. Each task's
        terminal event fires **at most once per process**; the emitted follow-up's
        ``dedup_key`` is the durable cross-restart guard against duplicates.
        """
        if self.evaluator is None:
            return 0
        from .producers.evaluator import apply_decisions

        try:
            terminal = self.client.list(
                repo=self.repo,
                status=[Status.COMPLETED, Status.ABANDONED],
                limit=self.evaluate_limit,
            )
        except DispatchError:
            log.exception("evaluator pass: listing terminal tasks failed")
            return 0

        emitted = 0
        for task in terminal:
            tid = task.get("id")
            if not tid or tid in self._evaluated:
                continue
            self._evaluated.add(tid)  # fire once per process, success or not
            event = {"type": f"task.{task.get('status')}", "task": task}
            try:
                decisions = self.evaluator.evaluate(event)
                results = apply_decisions(
                    decisions, creator=self.client.create, repo=self.repo
                )
            except Exception:  # a domain evaluator/create must never crash the loop
                log.exception("evaluator pass: advancing task %s failed", tid)
                continue
            for r in results:
                if r.get("decision") == "emit" and r.get("created"):
                    emitted += 1
                    log.info(
                        "evaluator pass: task %s (%s) -> emitted follow-up %s",
                        tid, event["type"], r["created"].get("id"),
                    )
        # Bound the in-process guard so a long-lived supervisor doesn't grow it
        # without limit -- keep the most recent terminal ids (dedup_key still
        # guards anything evicted).
        if len(self._evaluated) > 4 * self.evaluate_limit:
            keep = {t.get("id") for t in terminal if t.get("id")}
            self._evaluated = keep
        return emitted

    def _failed_spawn_counts(self) -> dict[str, int]:
        """Count FAILED spawn reservations per task id (the dead-letter signal)."""
        counts: dict[str, int] = {}
        for res in self.client.list_reservations(state=SpawnState.FAILED, limit=1000):
            counts[res["task_id"]] = counts.get(res["task_id"], 0) + 1
        return counts

    def _is_dead_lettered(self, task: dict, failed_counts: dict[str, int]) -> bool:
        """Whether ``task`` has exhausted its (possibly per-label) spawn-attempt
        bound and should no longer be auto-retried.

        Held, not lost: the failed reservation history stays queryable
        (``reservations list --state failed``) and an operator can intervene.
        A bound of 0 (global or per-label) disables dead-lettering for the task.
        """
        cap = self._effective_max_attempts(task)
        if not cap:
            return False
        return failed_counts.get(task["id"], 0) >= cap

    def _log_dead_lettered(
        self, tasks: Sequence[dict], failed_counts: dict[str, int]
    ) -> set[str]:
        blocked = [
            (
                task["id"],
                failed_counts.get(task["id"], 0),
                self._effective_max_attempts(task),
            )
            for task in tasks
            if self._is_dead_lettered(task, failed_counts)
        ]
        signature = tuple(sorted(blocked))
        if signature != self._dead_letter_signature:
            self._dead_letter_signature = signature
            if signature:
                shown = ", ".join(
                    f"{task_id} ({failures}/{cap})"
                    for task_id, failures, cap in signature[:10]
                )
                suppressed = (
                    f"; +{len(signature) - 10} more" if len(signature) > 10 else ""
                )
                log.warning(
                    "%d spawn-dead-lettered task(s): %s%s; inspect with "
                    "`agent-dispatch reservations list --state failed`; rearm one "
                    "with `agent-dispatch reservations rearm <task> --permit "
                    "--reason <reason>`",
                    len(signature),
                    shown,
                    suppressed,
                )
        return {task_id for task_id, _failures, _cap in signature}

    def poll_once(self, *, now: float | None = None) -> list[str]:
        """One supervision cycle: reconcile, hold live leases, then spawn eligible
        tasks up to the cap.

        Returns the ids of tasks spawned this cycle.
        """
        now = time.time() if now is None else now
        self.reconcile()
        if self.evaluator is not None:
            self.advance_via_evaluator()
        if self.heartbeat or self.publish_activity:
            self.hold_live_leases()
        if self.recover:
            self.recover_gone()
        if self.nudge:
            self.nudge_stalled(now=now)
        failed_counts = self._failed_spawn_counts()
        eligible = list(self._eligible(now))
        dead_lettered = self._log_dead_lettered(eligible, failed_counts)
        active = len(self._active_reservations())
        spawned: list[str] = []
        for task in eligible:
            if task["id"] in dead_lettered:
                continue
            if active >= self.max_concurrent:
                break
            if self.capacity_gate is not None and not self.capacity_gate(task):
                # No capacity for this task right now (e.g. a fleet pool that is
                # entirely asleep). Defer WITHOUT reserving so no spawn attempt is
                # burned toward the dead-letter bound -- it is retried next cycle.
                continue
            try:
                resp = self.client.reserve_spawn(task["id"], reserved_by=self.supervisor_id)
            except DispatchError:
                continue
            if not resp.get("reserved"):
                continue  # already actively reserved -> never double-spawn
            key = resp["reservation"]["key"]
            ok, handle = self.spawn_fn(task)
            try:
                if ok:
                    self.client.record_spawn(
                        key,
                        session_handle=handle.get("session"),
                        worktree=handle.get("worktree"),
                    )
                    if self.publish_activity:
                        self.client.set_activity(
                            task["id"], "ACTIVE", reservation_key=key
                        )
                    active += 1
                    spawned.append(task["id"])
                    log.info("spawned embody for task %s (%s)", task["id"], key)
                else:
                    self.client.fail_spawn(key, detail=handle.get("error", "spawn failed"))
                    log.warning(
                        "spawn failed for task %s (%s): %s",
                        task["id"], key, handle.get("error"),
                    )
            except DispatchError:
                log.exception("bookkeeping failed for reservation %s", key)
        return spawned

    def serve(
        self,
        *,
        interval: float = 30.0,
        on_cycle: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Run :meth:`poll_once` each cycle, waiting between cycles.

        The inter-cycle wait is **interruptible by a worker turn-end** when
        :attr:`reactive` is set (*react-to-turn-end*): it returns as soon as an
        embodied worker settles a turn, so a completed goal is reconciled and the
        next task embodied promptly instead of only on the ``interval`` cadence.
        The full ``interval`` remains the floor (and the whole wait when nothing is
        embodied); ``reactive=False`` restores a plain fixed sleep.
        """
        while True:
            try:
                spawned = self.poll_once()
                if on_cycle is not None:
                    on_cycle(spawned)
            except KeyboardInterrupt:
                return
            except Exception:  # pragma: no cover -- never let the loop die on a blip
                log.exception("supervision cycle failed")
            try:
                if self.reactive:
                    self.wait_for_turn_end(interval)
                else:
                    time.sleep(interval)
            except KeyboardInterrupt:
                return
