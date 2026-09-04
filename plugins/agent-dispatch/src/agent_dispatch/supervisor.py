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

import hashlib
import json
import logging
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .bridge_events import BridgeSubscription, SupervisorEventWake
from .client import DispatchClient, DispatchError
from .queue import SpawnState, Status

log = logging.getLogger("agent-dispatch.supervisor")


class SpawnPreparationRetained(RuntimeError):
    """A created worktree could not be recorded; retain the reservation fence."""

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

#: A re-drive sender for a spawned-but-unclaimed embodied worker. The session is
#: known live, but the task is still queued/unowned, so the supervisor re-sends
#: the idempotent autopilot seed instead of spawning a duplicate.
RedriveFn = Callable[[str, "str | None", dict, dict, dict], bool]

#: Prime a terminal CLI worker for ground-layer managed GC:
#: ``(worktree, session) -> structured outcome``.
ConclusionFn = Callable[[str, "str | None"], dict]
AttemptConclusionFn = Callable[[str, str | None, str, str], dict]

#: Stop a local headless bridge session while preserving it for later resume.
LocalColdFn = Callable[[str], bool]

#: Stop a remote fleet bridge session while preserving it for later resume.
FleetColdFn = Callable[[str, str], bool]

_TERMINAL = frozenset({Status.COMPLETED, Status.ABANDONED})
_LEASED = frozenset({Status.CLAIMED, Status.STARTED})
_CONCLUSION_PENDING = "pending"
_CONCLUSION_COMPLETE = "complete"
_CONCLUSION_HELD = "held"
_TRANSIENT_CONCLUSION_REASONS = frozenset({
    "live-mux",
    "live-session",
    "session-identity-unavailable",
})
_CONCLUSION_MAX_ATTEMPTS = 12
_CONCLUSION_RETRY_BASE_SECONDS = 30
_CONCLUSION_PER_CYCLE = 10
_COLD_RESUME_RETRY_SECONDS = 300


def _default_liveness(worktree: str, machine: str | None) -> dict | None:
    """Resolve an embodied session's liveness via the agent-bridge registry.

    Delegates to :func:`agent_dispatch.tracking.resolve_live_session` (shells the
    ``agent-bridge`` CLI, cross-machine over SSH when the owner is remote). All
    failure modes collapse to ``None`` -- so ``None`` means "not confirmed alive",
    which is why the supervisor only *heartbeats* on a positive result and never
    treats ``None`` as proof-of-death.
    """
    from . import tracking

    peer = machine if tracking.remote_dispatch.is_peer_machine(machine) else None
    return tracking.resolve_live_session(worktree, machine=peer)


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


def make_redrive_sender(route: str = "") -> RedriveFn:
    """Build a re-drive sender that uses the same coordinator route as spawn."""

    def redrive(
        worktree: str,
        machine: str | None,
        task: dict,
        session: dict,
        reservation: dict,
    ) -> bool:
        from . import bridge, embody

        task_id = str(task.get("id") or "")
        if not task_id:
            return False
        worker_id = f"redrive-{uuid.uuid4().hex[:8]}"
        prompt = embody.autopilot_worker_prompt(
            task_id, worker_id=worker_id, route=route
        )
        session_id = session.get("session_id")
        expected_session_id = session_id if isinstance(session_id, str) else None
        return bridge.redrive_embodied_worker(
            worktree,
            prompt,
            machine=machine,
            expected_session_id=expected_session_id,
            idempotency_key=f"{reservation.get('key')}:redrive",
        )

    return redrive


def _default_redrive(
    worktree: str,
    machine: str | None,
    task: dict,
    session: dict,
    reservation: dict,
) -> bool:
    """Re-send the autopilot seed to a live worker that never claimed its task."""
    return make_redrive_sender()(
        worktree, machine, task, session, reservation
    )


def _default_conclusion(worktree: str, session: str | None) -> dict:
    from . import embody

    return embody.conclude_disposable_worker(worktree, session)


def _default_attempt_conclusion(
    worktree: str,
    session: str | None,
    reservation_key: str,
    driver: str,
) -> dict:
    from . import embody

    return embody.conclude_dispatch_attempt(
        worktree,
        session,
        reservation_key,
        owner=driver,
    )


def _worktree_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.worktree_from_owner(owner)


def _worktree_from_reservation(reservation: dict, owner: str | None = None) -> str | None:
    """Best-effort worktree handle for a spawn reservation.

    Newer reservations persist ``worktree`` directly. Older rows sometimes only
    have the mux session handle (``wt-<worktree>``); decode that enough to
    reconcile and re-drive rather than leaving the worker invisible forever.
    """
    worktree = reservation.get("worktree")
    if isinstance(worktree, str) and worktree:
        return worktree
    handle = reservation.get("session_handle")
    if isinstance(handle, str) and handle.startswith("wt-") and len(handle) > 3:
        return handle[3:]
    return _worktree_from_owner(owner)


def _machine_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.machine_from_owner(owner)


#: A **fleet-body** liveness verdict resolver: ``(host, bridge_session_id) ->
#: 'live' | 'gone' | 'unknown'``. Probes a headless fleet body's agent-bridge
#: session on its pool host over SSH; ``unknown`` is never treated as death.
#: Injectable so tests drive verdicts deterministically.
FleetVerdictFn = Callable[[str, str], str]
FleetActivityFn = Callable[[str, str], str | None]
FleetEndFn = Callable[[str, str], bool]

#: A **local-body** liveness verdict resolver: ``(bridge_session_id) ->
#: 'live' | 'gone' | 'unknown'``. Probes a *local* headless body's agent-bridge
#: session on this host (no SSH); ``unknown`` is never treated as death.
#: Injectable so tests drive verdicts deterministically.
LocalBodyVerdictFn = Callable[[str], str]
LocalBodyActivityFn = Callable[[str], str | None]
LocalAcpSessionFn = Callable[[str], str | None]
LocalBodyTargetDirFn = Callable[[str], str | None]
LocalEndFn = Callable[[str], bool]
LocalResumeFn = Callable[[str, str], bool]

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


def _default_local_body_activity(bridge_session_id: str) -> str | None:
    from . import tracking

    for session in tracking.list_local_body_sessions():
        if str(session.get("session_id") or "") == bridge_session_id:
            return tracking.session_activity(session)
    return None


def _default_local_acp_session(bridge_session_id: str) -> str | None:
    from . import tracking

    for session in tracking.list_local_body_sessions():
        if str(session.get("session_id") or "") != bridge_session_id:
            continue
        acp_session_id = session.get("acp_session_id")
        return str(acp_session_id) if acp_session_id else None
    return None


def _default_local_body_target_dir(bridge_session_id: str) -> str | None:
    """Return an absolute target directory from a local body snapshot."""
    from . import tracking

    for session in tracking.list_local_body_sessions():
        if str(session.get("session_id") or "") != bridge_session_id:
            continue
        target_dir = session.get("target_dir")
        if not isinstance(target_dir, str) or not target_dir:
            return None
        path = Path(target_dir)
        return str(path) if path.is_absolute() else None
    return None


def _default_local_cold(bridge_session_id: str) -> bool:
    from . import bridge

    return bridge.stop_worker(bridge_session_id)


def _default_local_end(bridge_session_id: str) -> bool:
    from . import bridge

    return bridge.end_worker(bridge_session_id)


def _default_local_resume(bridge_session_id: str, prompt: str) -> bool:
    from . import bridge

    return bridge.resume_worker(bridge_session_id, prompt)


def _default_fleet_cold(host: str, bridge_session_id: str) -> bool:
    from . import embody

    return embody.stop_fleet_body(host, bridge_session_id)


def _default_fleet_end(host: str, bridge_session_id: str) -> bool:
    from . import embody

    return embody.stop_fleet_body(host, bridge_session_id)


def _tracking():
    """Lazy accessor for the ``tracking`` module (its verdict constants)."""
    from . import tracking

    return tracking


def make_embody_spawn(
    *,
    driver: str = "agent-dispatch",
    verify_timeout: int = 0,
    route: str = "",
    all_repos: bool = False,
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
                worktree_id=task.get("spawn_worktree"),
                route=route,
                repo=None if all_repos else task.get("repo"),
                all_repos=all_repos,
                verify_timeout=verify_timeout,
            )
        except embody.EmbodyUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            return False, {"error": (result.stderr or "").strip()[:200] or "nonzero exit"}
        handle = embody.parse_handle(result)
        return True, handle
    spawn.requires_reusable_worktree = True
    spawn.allocation_driver = driver
    spawn.allocation_interface = "cli"
    return spawn


def make_headless_spawn(
    *,
    agent: str = "task-worker",
    route: str = "",
    all_repos: bool = False,
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

    The supervisor pre-creates and records the headless body's worktree before
    launch, then passes that exact target to agent-bridge. It also records a
    ``local-body:<bridge-session-id>`` recovery handle, so a
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
            task["id"],
            worker_id=worker_id,
            route=route,
            repo=None if all_repos else task.get("repo"),
            all_repos=all_repos,
            explicit_worker_identity=True,
        )
        prior_session = _parse_local_body_handle(
            task.get("spawn_session_handle")
        )
        try:
            result = bridge.spawn_or_resume_worker(
                task["id"],
                agent=agent,
                worker_id=worker_id,
                prompt=seed,
                prior_session_id=prior_session,
                liveness_fn=embody.local_body_verdict,
                target_dir=task.get("spawn_worktree_path"),
                worktree_id=task.get("spawn_worktree"),
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
        return True, {
            "session": handle,
            "worktree": task.get("spawn_worktree"),
        }

    spawn.requires_reusable_worktree = True
    spawn.allocation_driver = "agent-dispatch"
    spawn.allocation_interface = "acp"
    spawn.allocation_project_for = lambda _task: (
        bridge.registered_agent_project(agent, timeout=30.0, strict=True) or ""
    )
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

    def requires_reusable_worktree(task: dict) -> bool:
        selected = default
        for label in task.get("labels") or []:
            if label in overrides:
                selected = overrides[label]
                break
        return bool(getattr(selected, "requires_reusable_worktree", False))

    def selected_attribute(task: dict, name: str, fallback: str) -> str:
        selected = default
        for label in task.get("labels") or []:
            if label in overrides:
                selected = overrides[label]
                break
        selector = getattr(selected, f"{name}_for", None)
        value = selector(task) if callable(selector) else getattr(
            selected, name, fallback
        )
        return value if isinstance(value, str) and value else fallback

    spawn.requires_reusable_worktree_for = requires_reusable_worktree
    spawn.allocation_driver_for = lambda task: selected_attribute(
        task, "allocation_driver", "agent-dispatch"
    )
    spawn.allocation_interface_for = lambda task: selected_attribute(
        task, "allocation_interface", "cli"
    )
    spawn.allocation_project_for = lambda task: selected_attribute(
        task, "allocation_project", ""
    )
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
        reactive: bool = False,
        reactive_interval: float = 2.0,
        stall_seconds: float = 600.0,
        liveness_fn: LivenessFn | None = None,
        verdict_fn: VerdictFn | None = None,
        fleet_verdict_fn: FleetVerdictFn | None = None,
        fleet_activity_fn: FleetActivityFn | None = None,
        local_body_verdict_fn: LocalBodyVerdictFn | None = None,
        local_body_activity_fn: LocalBodyActivityFn | None = None,
        local_acp_session_fn: LocalAcpSessionFn | None = None,
        local_body_target_dir_fn: LocalBodyTargetDirFn | None = None,
        local_cold_fn: LocalColdFn | None = None,
        local_end_fn: LocalEndFn | None = None,
        local_resume_fn: LocalResumeFn | None = None,
        fleet_cold_fn: FleetColdFn | None = None,
        fleet_end_fn: FleetEndFn | None = None,
        nudge_fn: NudgeFn | None = None,
        redrive_fn: RedriveFn | None = None,
        disposable_cli_labels: Sequence[str] | None = None,
        conclusion_fn: ConclusionFn | None = None,
        attempt_conclusion_fn: AttemptConclusionFn | None = None,
        machine: str | None = None,
        capacity_gate: Callable[[dict], bool] | None = None,
        evaluator: Any | None = None,
        evaluator_ref: str | None = None,
        evaluate_limit: int = 100,
        event_wake: SupervisorEventWake | None = None,
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
        if machine is None:
            from . import remote_dispatch

            machine = remote_dispatch.local_machine()
        lane_identity = json.dumps(
            {
                "machine": machine,
                "repo": repo,
                "labels": sorted(labels or ()),
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        self.supervisor_id = supervisor_id or (
            "supervisor-" + hashlib.sha256(lane_identity.encode()).hexdigest()[:16]
        )
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
        self.local_body_activity_fn = (
            local_body_activity_fn or _default_local_body_activity
        )
        self.local_acp_session_fn = (
            local_acp_session_fn or _default_local_acp_session
        )
        self.local_body_target_dir_fn = (
            local_body_target_dir_fn or _default_local_body_target_dir
        )
        self.local_cold_fn = local_cold_fn or _default_local_cold
        self.local_end_fn = local_end_fn or _default_local_end
        self.local_resume_fn = local_resume_fn or _default_local_resume
        self.fleet_cold_fn = fleet_cold_fn or _default_fleet_cold
        self.fleet_end_fn = fleet_end_fn or _default_fleet_end
        self._cooled_reservations: set[str] = set()
        self._cold_retry_after: dict[str, float] = {}
        self._resume_retry_after: dict[str, float] = {}
        #: Nudge sender used by :meth:`nudge_stalled`. Injectable for tests.
        self.nudge_fn = nudge_fn or _default_nudge
        #: Re-drive sender used when a spawned CLI body is alive but still has
        #: not claimed its queued task after a supervisor/bridge restart.
        self.redrive_fn = redrive_fn or _default_redrive
        #: Push acceleration is optional. The former implementation sampled
        #: Agent Bridge state every two seconds; this path owns one aggregate
        #: local stream and never changes periodic reconciliation correctness.
        self.reactive = reactive
        #: Retained for declaration/CLI compatibility; never drives polling.
        self.reactive_interval = max(0.25, float(reactive_interval))
        #: Explicit label-scoped terminal conclusion policy. Only a terminal
        #: task carrying one of these labels is handed to the disposable CLI
        #: conclusion path; arbitrary CLI worktrees remain untouched.
        self.disposable_cli_labels = set(disposable_cli_labels or ())
        self.conclusion_fn = conclusion_fn or _default_conclusion
        self.attempt_conclusion_fn = (
            attempt_conclusion_fn or _default_attempt_conclusion
        )
        self.machine = machine
        self._event_caller_id = (
            "agent-dispatch:"
            + hashlib.sha256(self.supervisor_id.encode()).hexdigest()[:24]
        )
        self.event_wake = event_wake or (
            SupervisorEventWake() if self.reactive else None
        )
        #: task_id -> last nudge ts (in-memory cooldown so a persistently-quiet
        #: live worker is nudged at most once per stall window, not every cycle).
        self._last_nudge: dict[str, float] = {}
        #: reservation key -> redrive attempted in this supervisor process. A
        #: restarted supervisor may retry; a healthy worker claims promptly.
        self._redriven_spawn_keys: set[str] = set()
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
        self.evaluator_ref = evaluator_ref
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
            if not self._matches_pool(t):
                continue  # not opted in
            out.append(t)
        out.sort(key=lambda t: t.get("created_at") or 0)
        return out

    def _matches_pool(self, task: dict) -> bool:
        if self.repo is not None and task.get("repo") != self.repo:
            return False
        if self.labels is not None and not (
            self.labels & set(task.get("labels") or [])
        ):
            return False
        return True

    def _active_reservations(self) -> list[dict]:
        reservations = self._pool_reservations(
            state=(
                f"{SpawnState.RESERVING},{SpawnState.SPAWNED},"
                f"{SpawnState.RELEASING}"
            )
        )
        active: list[dict] = []
        for reservation in reservations:
            try:
                task = self.client.get(reservation["task_id"])
            except DispatchError:
                active.append(reservation)
                continue
            if (
                self._matches_pool(task)
                and self._reservation_has_live_process(reservation, task)
            ):
                active.append(reservation)
        return active

    def _pool_reservations(
        self,
        *,
        state: str,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
    ) -> list[dict]:
        """List reservations filtered server-side to this pool before limit."""
        if not self.labels:
            return self.client.list_reservations(
                state=state,
                repo=self.repo,
                conclusion_state=conclusion_state,
                resume_requested=resume_requested,
                limit=10000,
            )
        by_key: dict[str, dict] = {}
        for label in self.labels:
            for reservation in self.client.list_reservations(
                state=state,
                repo=self.repo,
                label=label,
                conclusion_state=conclusion_state,
                resume_requested=resume_requested,
                limit=10000,
            ):
                key = str(reservation.get("key") or "")
                if key:
                    by_key[key] = reservation
        return list(by_key.values())

    def _spawn_requires_reusable_worktree(self, task: dict) -> bool:
        selector = getattr(
            self.spawn_fn,
            "requires_reusable_worktree_for",
            None,
        )
        if callable(selector):
            return bool(selector(task))
        return bool(
            getattr(self.spawn_fn, "requires_reusable_worktree", False)
        )

    def _spawn_attribute(self, task: dict, name: str, default: str) -> str:
        selector = getattr(self.spawn_fn, f"{name}_for", None)
        if callable(selector):
            value = selector(task)
        else:
            value = getattr(self.spawn_fn, name, default)
        return value if isinstance(value, str) and value else default

    def _prepare_spawn_task(self, task: dict, reservation: dict) -> dict:
        """Pre-create or resolve a backend-required worktree before launch."""
        if not self._spawn_requires_reusable_worktree(task):
            return task
        from . import embody

        driver = self._spawn_attribute(
            task, "allocation_driver", "agent-dispatch"
        )
        interface = self._spawn_attribute(task, "allocation_interface", "cli")
        project = self._spawn_attribute(
            task, "allocation_project", embody.project_for_task(task) or ""
        )
        prepared = embody.prepare_reusable_worktree(
            task,
            reservation,
            project=project or None,
            interface=interface,
            driver=driver,
            supervisor=self.supervisor_id,
        )
        worktree = str(prepared["worktree"])
        replaced = bool(prepared.get("replaced"))
        ownership = str(prepared.get("ownership") or "unknown")
        if (
            reservation.get("worktree") != worktree
            or reservation.get("worktree_ownership") != ownership
        ):
            try:
                self.client.record_spawn_worktree(
                    reservation["key"],
                    worktree,
                    ownership=ownership,
                    creating_host=self.machine if ownership == "created" else None,
                    driver=driver,
                )
            except DispatchError as exc:
                if ownership == "created":
                    raise SpawnPreparationRetained(
                        f"created worktree {worktree} could not be recorded; "
                        "reservation retained for repair"
                    ) from exc
                raise
        return {
            **task,
            "spawn_worktree": worktree,
            "spawn_worktree_path": prepared["path"],
            "spawn_worktree_ownership": ownership,
            "spawn_session_handle": (
                None if replaced else reservation.get("session_handle")
            ),
        }

    def _reservation_has_live_process(
        self, reservation: dict, task: dict
    ) -> bool:
        if reservation.get("state") == SpawnState.RELEASING:
            fleet = _parse_fleet_body_handle(reservation.get("session_handle"))
            if fleet is not None:
                try:
                    return self.fleet_verdict_fn(*fleet) != _tracking().GONE
                except Exception:
                    return True
            local_sid = _parse_local_body_handle(
                reservation.get("session_handle")
            )
            if local_sid is not None:
                try:
                    return self.local_body_verdict_fn(local_sid) != _tracking().GONE
                except Exception:
                    return True
            worktree = _worktree_from_reservation(
                reservation,
                task.get("owner"),
            )
            if not worktree:
                return bool(reservation.get("session_handle"))
            try:
                return (
                    self.verdict_fn(
                        worktree,
                        _machine_from_owner(task.get("owner")),
                        task.get("owner_session_id"),
                    )
                    != _tracking().GONE
                )
            except Exception:
                return True
        if task.get("status") != Status.SUSPENDED:
            return True
        key = str(reservation.get("key") or "")
        if key in self._cooled_reservations:
            return False
        fleet = _parse_fleet_body_handle(reservation.get("session_handle"))
        if fleet is not None:
            try:
                return self.fleet_verdict_fn(*fleet) != _tracking().GONE
            except Exception:
                return True
        local_sid = _parse_local_body_handle(reservation.get("session_handle"))
        if local_sid is not None:
            try:
                return self.local_body_verdict_fn(local_sid) != _tracking().GONE
            except Exception:
                return True
        # CLI suspension hands its process to the hibernation layer.
        return False

    def cool_dormant_bodies(self) -> int:
        """Stop headless processes for suspended/blocked tasks.

        The durable spawned reservation remains as the cold-session handle.
        Steering a suspended headless task settles that reservation and queues a
        fresh embodiment, so dormant tasks consume no live-process capacity.
        """
        cooled = 0
        attempted = 0
        now = time.time()
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
            if attempted >= 10:
                break
            key = str(res.get("key") or "")
            if not key or key in self._cooled_reservations:
                continue
            if now < self._cold_retry_after.get(key, 0.0):
                continue
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if not self._matches_pool(task):
                continue
            if task.get("status") != Status.SUSPENDED:
                continue
            fleet = _parse_fleet_body_handle(res.get("session_handle"))
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if fleet is None and local_sid is None:
                continue
            attempted += 1
            stopped = False
            try:
                if fleet is not None:
                    stopped = (
                        self.fleet_verdict_fn(*fleet) == _tracking().GONE
                        or self.fleet_cold_fn(*fleet)
                    )
                elif local_sid is not None:
                    stopped = (
                        self.local_body_verdict_fn(local_sid)
                        == _tracking().GONE
                        or self.local_cold_fn(local_sid)
                    )
            except Exception:
                log.exception("failed to cool dormant reservation %s", key)
            if stopped:
                try:
                    self.client.record_cold(key)
                except DispatchError:
                    log.exception(
                        "failed to record cold reservation %s", key
                    )
                    self._cold_retry_after[key] = now + 60.0
                    continue
                self._cooled_reservations.add(key)
                self._cold_retry_after.pop(key, None)
                cooled += 1
                log.info(
                    "cooled dormant worker for task %s (%s)",
                    task.get("id"),
                    key,
                )
            else:
                self._cold_retry_after[key] = now + 60.0
        return cooled

    def suspend_idle_headless_tasks(self) -> int:
        """Treat a headless ACP turn-end as an implicit suspend request."""
        suspended = 0
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if (
                not self._matches_pool(task)
                or task.get("status") != Status.STARTED
                or not task.get("owner")
            ):
                continue
            activity = None
            fleet = _parse_fleet_body_handle(res.get("session_handle"))
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            try:
                if fleet is not None:
                    activity = self.fleet_activity_fn(*fleet)
                elif local_sid is not None:
                    activity = self.local_body_activity_fn(local_sid)
            except Exception:
                log.exception(
                    "failed to read headless turn state for task %s",
                    task.get("id"),
                )
                continue
            if activity != "IDLE":
                continue
            try:
                self.client.suspend(
                    task["id"],
                    task["owner"],
                    reason="headless ACP turn ended",
                )
                self.client.set_activity(
                    task["id"], "IDLE", reservation_key=res["key"]
                )
                suspended += 1
            except DispatchError:
                log.exception(
                    "failed to suspend idle headless task %s", task.get("id")
                )
        return suspended

    def bind_headless_owner_sessions(self) -> int:
        """Attach held local headless tasks to their exact bridge session."""
        bound = 0
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if local_sid is None:
                continue
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if (
                not self._matches_pool(task)
                or task.get("status") not in _LEASED
                or not task.get("owner")
                or task.get("owner_session_id")
            ):
                continue
            try:
                self.client.bind_owner_session(
                    task["id"],
                    task["owner"],
                    local_sid,
                    expected_generation=task.get("generation"),
                )
                bound += 1
            except DispatchError:
                log.exception(
                    "failed to bind headless owner session for task %s",
                    task.get("id"),
                )
        return bound

    def release_resumed_cold_tasks(self, *, now: float | None = None) -> int:
        """Resume viable cold bodies or release ones whose worktree vanished."""
        now = time.time() if now is None else now
        handled = 0
        for res in self._pool_reservations(
            state=SpawnState.COLD, resume_requested=True
        ):
            key = str(res.get("key") or "")
            if not key:
                continue
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            owner = task.get("owner")
            if task.get("status") != Status.SUSPENDED or not owner:
                continue
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if local_sid is None:
                continue
            try:
                target_dir = self.local_body_target_dir_fn(local_sid)
            except Exception:
                log.exception(
                    "failed to resolve target directory for cold session %s",
                    local_sid,
                )
                target_dir = None
            if target_dir is not None and not Path(target_dir).is_dir():
                try:
                    self.client.release(
                        task["id"],
                        owner,
                        reason=(
                            "recorded worktree is missing; released for fresh "
                            "re-embodiment"
                        ),
                    )
                except DispatchError:
                    log.exception(
                        "failed to release missing-worktree cold task %s",
                        task["id"],
                    )
                    continue
                self._cooled_reservations.discard(key)
                self._cold_retry_after.pop(key, None)
                self._resume_retry_after.pop(key, None)
                handled += 1
                log.warning(
                    "released suspended task %s after its recorded worktree "
                    "disappeared (%s); durable task state retained for fresh "
                    "re-embodiment",
                    task["id"],
                    target_dir,
                )
                continue
            if now < self._resume_retry_after.get(key, 0.0):
                continue
            prompt = (
                f"Task {task['id']} has new durable steering. Resume this same "
                "task and ACP conversation, run `agent-dispatch steer take "
                f"{task['id']} --all`, and continue from the recorded progress. "
                "Do not create a replacement task or worktree."
            )
            try:
                self.client.record_spawn(
                    res["key"],
                    session_handle=res.get("session_handle"),
                    worktree=res.get("worktree"),
                )
                self._cooled_reservations.discard(str(res["key"]))
                self._cold_retry_after.pop(str(res["key"]), None)
                try:
                    process_resumed = self.local_resume_fn(local_sid, prompt)
                except Exception:
                    self._resume_retry_after[key] = (
                        now + _COLD_RESUME_RETRY_SECONDS
                    )
                    log.exception(
                        "failed to resume cold local body %s for task %s",
                        local_sid,
                        task.get("id"),
                    )
                    continue
                if not process_resumed:
                    self._resume_retry_after[key] = (
                        now + _COLD_RESUME_RETRY_SECONDS
                    )
                    continue
                self.client.resume(
                    task["id"],
                    owner,
                    wake=False,
                    reuse_session=True,
                    expected_owner_session_id=task.get("owner_session_id"),
                    expected_generation=task.get("generation"),
                )
                self._resume_retry_after.pop(key, None)
                handled += 1
                log.info(
                    "resumed cold task %s in existing ACP session %s",
                    task.get("id"),
                    local_sid,
                )
            except DispatchError:
                self._resume_retry_after[key] = (
                    now + _COLD_RESUME_RETRY_SECONDS
                )
                log.exception(
                    "failed to finalize cold-task resume for %s",
                    task.get("id"),
                )
        return handled

    def reconcile_reserving(self) -> int:
        """Recover pre-launch reservations after a supervisor interruption.

        A worktree-backed reservation is safe to classify because the reusable
        worktree id is recorded before launch. Confirmed-live worktrees are
        promoted to ``spawned`` with their observed session; confirmed-gone
        worktrees fail for a fresh attempt; unknown state remains reserved.
        Reservations without a durable handle remain untouched.
        """
        reconciled = 0
        for res in self._pool_reservations(state=SpawnState.RESERVING):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if not self._matches_pool(task):
                continue
            local_sid = _parse_local_body_handle(res.get("session_handle"))
            if local_sid is not None:
                try:
                    verdict = self.local_body_verdict_fn(local_sid)
                except Exception:
                    verdict = _tracking().UNKNOWN
                if verdict == _tracking().UNKNOWN:
                    continue
                if verdict == _tracking().LIVE:
                    try:
                        activity = self.local_body_activity_fn(local_sid)
                    except Exception:
                        activity = None
                    if activity == "IDLE" and res.get("worktree"):
                        try:
                            self.client.fail_spawn(
                                res["key"],
                                detail=(
                                    "carried local body is idle and ready "
                                    "for safe resume"
                                ),
                            )
                            reconciled += 1
                        except DispatchError:
                            log.exception(
                                "failed to rearm idle reserving body %s",
                                res["key"],
                            )
                        continue
                    if activity not in {"ACTIVE", "STALLED"}:
                        continue
                    try:
                        self.client.record_spawn(
                            res["key"],
                            session_handle=res.get("session_handle"),
                            worktree=res.get("worktree"),
                        )
                        reconciled += 1
                    except DispatchError:
                        log.exception(
                            "failed to promote live reserving body %s",
                            res["key"],
                        )
                    continue
                try:
                    detail = "carried local body confirmed gone while reserving"
                    if res.get("worktree_ownership") == "created":
                        self.client.request_spawn_release(
                            res["key"],
                            detail=detail,
                            disposition="failed",
                        )
                    else:
                        self.client.fail_spawn(res["key"], detail=detail)
                    reconciled += 1
                except DispatchError:
                    log.exception(
                        "failed to release gone reserving body %s", res["key"]
                    )
                continue

            worktree = _worktree_from_reservation(res, task.get("owner"))
            if not worktree:
                continue
            try:
                verdict = self.verdict_fn(
                    worktree,
                    _machine_from_owner(task.get("owner")),
                    task.get("owner_session_id"),
                )
            except Exception:
                verdict = _tracking().UNKNOWN
            if verdict == _tracking().UNKNOWN:
                continue
            if verdict == _tracking().GONE:
                try:
                    detail = "reserved worktree confirmed without a live worker"
                    if res.get("worktree_ownership") == "created":
                        self.client.request_spawn_release(
                            res["key"],
                            detail=detail,
                            disposition="failed",
                        )
                    else:
                        self.client.fail_spawn(res["key"], detail=detail)
                    reconciled += 1
                except DispatchError:
                    log.exception(
                        "failed to release gone reserving worktree %s",
                        res["key"],
                    )
                continue
            try:
                session = self.liveness_fn(
                    worktree,
                    _machine_from_owner(task.get("owner")),
                )
            except Exception:
                session = None
            if not session:
                continue
            session_id = session.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                continue
            try:
                self.client.record_spawn(
                    res["key"],
                    session_handle=session_id,
                    worktree=session.get("worktree_id") or worktree,
                )
                reconciled += 1
            except DispatchError:
                log.exception(
                    "failed to promote live reserving worktree %s",
                    res["key"],
                )
        return reconciled

    def _conclude_released_attempt(
        self,
        reservation: dict,
        *,
        session_id: str | None,
    ) -> tuple[str, dict]:
        """Conclude only an allocation proven created by this reservation."""
        worktree = reservation.get("worktree")
        if not isinstance(worktree, str) or not worktree:
            return _CONCLUSION_COMPLETE, {
                "action": "skipped",
                "reason": "reservation-has-no-worktree",
            }
        ownership = reservation.get("worktree_ownership")
        if ownership in {"targeted", "reused"}:
            return _CONCLUSION_COMPLETE, {
                "action": "preserved",
                "reason": f"{ownership}-worktree",
            }
        if ownership != "created":
            return _CONCLUSION_HELD, {
                "action": "skipped",
                "reason": "allocation-ownership-unknown",
            }
        creating_host = reservation.get("creating_host")
        if (
            not isinstance(creating_host, str)
            or not creating_host
            or not self.machine
            or creating_host.casefold() != self.machine.casefold()
        ):
            return _CONCLUSION_HELD, {
                "action": "skipped",
                "reason": "foreign-or-unknown-creating-host",
            }
        driver = reservation.get("driver")
        if not isinstance(driver, str) or not driver:
            return _CONCLUSION_HELD, {
                "action": "skipped",
                "reason": "allocation-driver-unknown",
            }
        try:
            outcome = self.attempt_conclusion_fn(
                worktree,
                session_id,
                str(reservation["key"]),
                driver,
            )
        except Exception as exc:
            log.exception(
                "attempt conclusion failed for reservation %s",
                reservation.get("key"),
            )
            outcome = {"action": "failed", "reason": str(exc)[:300]}
        if session_id:
            outcome = {**outcome, "acp_session_id": session_id}
        return self._conclusion_state(outcome), outcome

    def release_requested_bodies(self) -> int:
        """Fulfill durable yield/release requests after safe body teardown."""
        released = 0
        reservations = self._pool_reservations(
            state=(
                f"{SpawnState.RESERVING},{SpawnState.SPAWNED},"
                f"{SpawnState.COLD},{SpawnState.RELEASING}"
            )
        )
        for res in reservations:
            if (
                res.get("state") == SpawnState.RELEASING
                and res.get("conclusion_state") == _CONCLUSION_HELD
            ):
                continue
            if not res.get("release_requested"):
                continue
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if not self._matches_pool(task):
                continue
            can_release = False
            conclusion_session: str | None = None
            fleet = _parse_fleet_body_handle(res.get("session_handle"))
            if fleet is not None:
                try:
                    verdict = self.fleet_verdict_fn(*fleet)
                except Exception:
                    verdict = _tracking().UNKNOWN
                if verdict == _tracking().UNKNOWN:
                    continue
                if verdict == _tracking().GONE:
                    can_release = True
                else:
                    try:
                        can_release = self.fleet_end_fn(*fleet)
                    except Exception:
                        log.exception(
                            "failed to end release-requested fleet body %s",
                            fleet,
                        )
            else:
                local_sid = _parse_local_body_handle(
                    res.get("session_handle")
                )
                if local_sid is not None:
                    retry_payload = self._conclusion_retry_payload(res)
                    recorded_acp = retry_payload.get("acp_session_id")
                    try:
                        resolved_acp = self.local_acp_session_fn(local_sid)
                    except Exception:
                        resolved_acp = None
                    conclusion_session = (
                        str(recorded_acp)
                        if recorded_acp
                        else (resolved_acp or task.get("owner_session_id"))
                    )
                    try:
                        verdict = self.local_body_verdict_fn(local_sid)
                    except Exception:
                        verdict = _tracking().UNKNOWN
                    if verdict == _tracking().UNKNOWN:
                        continue
                    if verdict == _tracking().GONE:
                        can_release = True
                    else:
                        try:
                            can_release = self.local_end_fn(local_sid)
                        except Exception:
                            log.exception(
                                "failed to end release-requested local body %s",
                                local_sid,
                            )
                else:
                    raw_session = res.get("session_handle")
                    if isinstance(raw_session, str) and raw_session:
                        conclusion_session = raw_session
                    worktree = _worktree_from_reservation(
                        res,
                        task.get("owner"),
                    )
                    if not worktree:
                        can_release = (
                            res.get("state") == SpawnState.RESERVING
                            and not res.get("session_handle")
                        )
                    else:
                        try:
                            can_release = (
                                self.verdict_fn(
                                    worktree,
                                    _machine_from_owner(task.get("owner")),
                                    task.get("owner_session_id"),
                                )
                                == _tracking().GONE
                            )
                        except Exception:
                            can_release = False
            if not can_release:
                continue
            conclusion_state, outcome = self._conclude_released_attempt(
                res,
                session_id=conclusion_session,
            )
            conclusion_detail = json.dumps(
                outcome,
                sort_keys=True,
                separators=(",", ":"),
            )
            if conclusion_state in {_CONCLUSION_PENDING, _CONCLUSION_HELD}:
                try:
                    self.client.record_spawn_conclusion(
                        res["key"],
                        conclusion_state=conclusion_state,
                        conclusion_detail=conclusion_detail,
                    )
                except DispatchError:
                    log.exception(
                        "failed to persist attempt conclusion for %s",
                        res["key"],
                    )
                continue
            try:
                detail = self._append_conclusion_detail(
                    res.get("detail") or "spawn release requested",
                    outcome,
                )
                if (
                    res.get("state") == SpawnState.RELEASING
                    and res.get("release_disposition") != "settled"
                ):
                    self.client.fail_spawn(res["key"], detail=detail)
                else:
                    self.client.settle_spawn(
                        res["key"],
                        detail=detail,
                        conclusion_state=conclusion_state,
                        conclusion_detail=conclusion_detail,
                    )
                released += 1
            except DispatchError:
                log.exception(
                    "failed to settle release-requested reservation %s",
                    res["key"],
                )
        return released

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

    def _conclude_terminal_worker(
        self,
        reservation: dict,
        task: dict,
        *,
        session_override: str | None = None,
    ) -> dict | None:
        """Run the opt-in exact-identity terminal conclusion policy."""
        if not self.disposable_cli_labels.intersection(task.get("labels") or []):
            return None
        worktree = reservation.get("worktree")
        if not isinstance(worktree, str) or not worktree:
            return {"action": "skipped", "reason": "reservation-has-no-worktree"}
        recorded_handle = reservation.get("session_handle")
        bridge_session = (
            _parse_local_body_handle(recorded_handle)
            if isinstance(recorded_handle, str)
            else None
        )
        session = session_override or recorded_handle
        if not isinstance(session, str) or not session:
            session = None
        elif bridge_session and session_override is None:
            retry_payload = self._conclusion_retry_payload(reservation)
            recorded_acp = retry_payload.get("acp_session_id")
            try:
                session = (
                    str(recorded_acp)
                    if recorded_acp
                    else self.local_acp_session_fn(bridge_session)
                )
            except Exception:
                log.exception(
                    "ACP session identity lookup failed for bridge session %s",
                    bridge_session,
                )
                session = None
            if not session:
                return {
                    "action": "failed",
                    "reason": "session-identity-unavailable",
                }
        try:
            outcome = self.conclusion_fn(worktree, session)
            if bridge_session and session:
                outcome = {**outcome, "acp_session_id": session}
            return outcome
        except Exception as exc:
            log.exception(
                "terminal conclusion failed for task %s reservation %s",
                task.get("id"),
                reservation.get("key"),
            )
            failure = {"action": "failed", "reason": str(exc)[:300]}
            if bridge_session and session:
                failure["acp_session_id"] = session
            return failure

    def _nudge_terminal_conclusion(self, reservation: dict, task: dict) -> bool:
        """Reawaken the exact local body once to finish its worktree lifecycle."""
        session_id = _parse_local_body_handle(reservation.get("session_handle"))
        worktree = reservation.get("worktree")
        if session_id is None or not isinstance(worktree, str) or not worktree:
            return False
        prompt = (
            f"Task {task.get('id')} is already terminal, but managed worktree "
            f"{worktree} is not FINAL. Conclude this same worktree now: finalize "
            "landed work, or explicitly unwind/transfer any outstanding work to "
            "a named tracked objective. Do not create a replacement worktree. "
            "End the turn after the worktree reaches FINAL or the tracked "
            "transfer/blocker is recorded."
        )
        try:
            return bool(self.local_resume_fn(session_id, prompt))
        except Exception:
            log.exception(
                "failed to nudge terminal conclusion for task %s session %s",
                task.get("id"),
                session_id,
            )
            return False

    def _refresh_terminal_session(
        self, reservation: dict, task: dict
    ) -> dict:
        """Capture an exact live session id before releasing the reservation."""
        worktree = reservation.get("worktree")
        if not isinstance(worktree, str) or not worktree:
            return reservation
        recorded_session = reservation.get("session_handle")
        if (
            isinstance(recorded_session, str)
            and recorded_session
            and recorded_session != f"wt-{worktree}"
        ):
            return reservation
        durable_session = task.get("owner_session_id")
        raw_completed_by = task.get("completed_by")
        completed_by = _worktree_from_owner(raw_completed_by)
        if (
            isinstance(durable_session, str)
            and durable_session
            and (completed_by is None or completed_by == worktree)
        ):
            session_handle = durable_session
            if (
                isinstance(raw_completed_by, str)
                and raw_completed_by.startswith("headless-")
            ):
                session_handle = f"{_LOCAL_BODY_PREFIX}{durable_session}"
            try:
                self.client.record_spawn(
                    reservation["key"],
                    session_handle=session_handle,
                    worktree=worktree,
                )
            except DispatchError:
                return reservation
            return {
                **reservation,
                "session_handle": session_handle,
                "worktree": worktree,
            }
        try:
            session = self.liveness_fn(worktree, None)
        except Exception:
            session = None
        if not session:
            return reservation
        session_id = session.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            return reservation
        exact_worktree = session.get("worktree_id")
        if not isinstance(exact_worktree, str) or not exact_worktree:
            exact_worktree = worktree
        try:
            self.client.record_spawn(
                reservation["key"],
                session_handle=session_id,
                worktree=exact_worktree,
            )
        except DispatchError:
            return reservation
        return {
            **reservation,
            "session_handle": session_id,
            "worktree": exact_worktree,
        }

    @staticmethod
    def _conclusion_state(outcome: dict) -> str:
        action = str(outcome.get("action") or "")
        reason = str(outcome.get("reason") or "")
        if action in {
            "primed",
            "already-primed",
            "removed",
            "already-removed",
        }:
            return _CONCLUSION_COMPLETE
        if action == "failed" or reason in _TRANSIENT_CONCLUSION_REASONS:
            return _CONCLUSION_PENDING
        return _CONCLUSION_HELD

    @staticmethod
    def _append_conclusion_detail(detail: str, outcome: dict | None) -> str:
        if outcome is None:
            return detail
        action = str(outcome.get("action") or "unknown")
        reason = str(outcome.get("reason") or "")
        suffix = f"terminal conclusion {action}"
        if reason:
            suffix += f" ({reason})"
        return f"{detail}; {suffix}"

    @staticmethod
    def _conclusion_retry_payload(reservation: dict) -> dict:
        raw = reservation.get("conclusion_detail")
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        if not isinstance(payload, dict):
            return {}
        if not payload.get("acp_session_id") and payload.get("session"):
            payload["acp_session_id"] = payload["session"]
        return payload

    @classmethod
    def _conclusion_retry_meta(cls, reservation: dict) -> tuple[int, float]:
        payload = cls._conclusion_retry_payload(reservation)
        try:
            attempts = max(0, int(payload.get("attempts", 0)))
            next_at = max(0.0, float(payload.get("next_attempt_at", 0)))
        except (TypeError, ValueError):
            return 0, 0.0
        return attempts, next_at

    def _exclusive_terminal_release_ready(
        self,
        reservation: dict,
        task: dict,
        *,
        policy_applies: bool,
    ) -> tuple[bool, dict | None]:
        """Require body conclusion before releasing an exclusive reservation."""
        local_sid = _parse_local_body_handle(
            reservation.get("session_handle")
        )
        if (
            not reservation.get("exclusive_key")
            and not (policy_applies and local_sid is not None)
        ):
            return True, None

        fleet = _parse_fleet_body_handle(reservation.get("session_handle"))
        if fleet is not None:
            try:
                verdict = self.fleet_verdict_fn(*fleet)
            except Exception:
                verdict = _tracking().UNKNOWN
            if verdict == _tracking().UNKNOWN:
                return False, None
            if verdict == _tracking().GONE:
                return True, None
            try:
                return self.fleet_end_fn(*fleet), None
            except Exception:
                log.exception(
                    "failed to end terminal exclusive fleet body %s for task %s",
                    fleet,
                    task.get("id"),
                )
                return False, None

        if local_sid is not None:
            try:
                verdict = self.local_body_verdict_fn(local_sid)
            except Exception:
                verdict = _tracking().UNKNOWN
            if verdict == _tracking().UNKNOWN:
                return False, None
            if verdict == _tracking().GONE and not policy_applies:
                return True, None
            if policy_applies:
                if verdict != _tracking().GONE:
                    try:
                        if self.local_body_activity_fn(local_sid) != "IDLE":
                            return False, None
                    except Exception:
                        return False, None
                attempts, next_attempt_at = self._conclusion_retry_meta(
                    reservation
                )
                now = time.time()
                if attempts >= _CONCLUSION_MAX_ATTEMPTS or next_attempt_at > now:
                    return False, None
                prior_payload = self._conclusion_retry_payload(reservation)
                acp_session = prior_payload.get("acp_session_id")
                if not acp_session:
                    try:
                        acp_session = self.local_acp_session_fn(local_sid)
                    except Exception:
                        acp_session = None
                if not acp_session:
                    attempts += 1
                    failure = {
                        "action": "failed",
                        "reason": "session-identity-unavailable",
                        "attempts": attempts,
                        "next_attempt_at": now + min(
                            300,
                            _CONCLUSION_RETRY_BASE_SECONDS
                            * (2 ** max(0, attempts - 1)),
                        ),
                    }
                    try:
                        self.client.record_spawn_conclusion(
                            reservation["key"],
                            conclusion_state=(
                                _CONCLUSION_HELD
                                if attempts >= _CONCLUSION_MAX_ATTEMPTS
                                else _CONCLUSION_PENDING
                            ),
                            conclusion_detail=json.dumps(
                                failure,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        )
                    except DispatchError:
                        pass
                    return False, None
                checkpoint = {
                    "action": "pending",
                    "reason": "ending-terminal-body",
                    "acp_session_id": str(acp_session),
                    "attempts": attempts,
                    "next_attempt_at": 0,
                }
                try:
                    self.client.record_spawn_conclusion(
                        reservation["key"],
                        conclusion_state=_CONCLUSION_PENDING,
                        conclusion_detail=json.dumps(
                            checkpoint,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                except DispatchError:
                    return False, None
                try:
                    ended = self.local_end_fn(local_sid)
                except Exception:
                    log.exception(
                        "failed to end terminal disposable local body %s "
                        "for task %s",
                        local_sid,
                        task.get("id"),
                    )
                    ended = False
                if not ended:
                    attempts += 1
                    failure = {
                        **checkpoint,
                        "action": "failed",
                        "reason": "terminal-body-end-failed",
                        "attempts": attempts,
                        "next_attempt_at": now + min(
                            300,
                            _CONCLUSION_RETRY_BASE_SECONDS
                            * (2 ** max(0, attempts - 1)),
                        ),
                    }
                    try:
                        self.client.record_spawn_conclusion(
                            reservation["key"],
                            conclusion_state=(
                                _CONCLUSION_HELD
                                if attempts >= _CONCLUSION_MAX_ATTEMPTS
                                else _CONCLUSION_PENDING
                            ),
                            conclusion_detail=json.dumps(
                                failure,
                                sort_keys=True,
                                separators=(",", ":"),
                            ),
                        )
                    except DispatchError:
                        pass
                    return False, None
                outcome = self._conclude_terminal_worker(
                    reservation,
                    task,
                    session_override=str(acp_session),
                )
                return True, outcome
            if (
                task.get("status") == Status.COMPLETED
                and task.get("completed_by")
            ):
                try:
                    if self.local_body_activity_fn(local_sid) == "IDLE":
                        return True, None
                except Exception:
                    pass
            try:
                return self.local_end_fn(local_sid), None
            except Exception:
                log.exception(
                    "failed to end terminal exclusive local body %s for task %s",
                    local_sid,
                    task.get("id"),
                )
                return False, None

        worktree = _worktree_from_reservation(reservation, task.get("owner"))
        if not worktree:
            return reservation.get("state") == SpawnState.RESERVING, None
        try:
            verdict = self.verdict_fn(
                worktree,
                _machine_from_owner(
                    task.get("completed_by") or task.get("owner")
                ),
                task.get("owner_session_id"),
            )
        except Exception:
            verdict = _tracking().UNKNOWN
        if verdict == _tracking().GONE:
            return True, None
        if verdict == _tracking().UNKNOWN or not policy_applies:
            return False, None
        outcome = self._conclude_terminal_worker(reservation, task)
        return self._conclusion_state(outcome or {}) == _CONCLUSION_COMPLETE, outcome

    def reconcile(self) -> int:
        """Settle ``spawned`` reservations whose task reached a terminal state.

        This is the *only* automatic release of a reservation -- and only for a
        provably-finished task -- so it can never free a still-running spawn for a
        double-launch. A completed **goal** is verified (*verify-the-completion-
        claim*) as it settles: an empty "done" is flagged in the reservation
        detail rather than silently accepted. Returns the number settled.
        """
        settled = 0
        reservations = self._pool_reservations(
            state=(
                f"{SpawnState.RESERVING},{SpawnState.SPAWNED},"
                f"{SpawnState.COLD}"
            )
        )
        reservations.extend(
            self._pool_reservations(
                state=SpawnState.SETTLED,
                conclusion_state=_CONCLUSION_PENDING,
            )[:_CONCLUSION_PER_CYCLE]
        )
        now = time.time()
        for res in reservations:
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue  # task vanished; leave the reservation for a human
            if task.get("status") in _TERMINAL:
                if (
                    res.get("worktree_ownership") == "created"
                    and res.get("state") != SpawnState.SETTLED
                ):
                    try:
                        if res.get("state") != SpawnState.RELEASING:
                            self.client.request_spawn_release(
                                res["key"],
                                detail=self._completion_detail(task),
                                disposition="settled",
                            )
                    except DispatchError:
                        log.exception(
                            "could not fence terminal allocation cleanup for %s",
                            res["key"],
                        )
                    continue
                prior_attempts = 0
                exclusive = bool(res.get("exclusive_key"))
                if res.get("state") == SpawnState.RESERVING and not exclusive:
                    continue
                policy_applies = bool(
                    self.disposable_cli_labels.intersection(
                        task.get("labels") or []
                    )
                )
                if (
                    policy_applies
                    and res.get("state") != SpawnState.SETTLED
                    and res.get("conclusion_state") in {
                        _CONCLUSION_PENDING,
                        _CONCLUSION_HELD,
                    }
                ):
                    prior_attempts, next_attempt_at = self._conclusion_retry_meta(res)
                    if res.get("conclusion_state") == _CONCLUSION_HELD:
                        continue
                    if next_attempt_at > now:
                        continue
                    if prior_attempts >= _CONCLUSION_MAX_ATTEMPTS:
                        try:
                            self.client.record_spawn_conclusion(
                                res["key"],
                                conclusion_state=_CONCLUSION_HELD,
                                conclusion_detail=res.get("conclusion_detail") or "{}",
                            )
                        except DispatchError:
                            pass
                        continue
                if (
                    policy_applies
                    and res.get("state") != SpawnState.SETTLED
                ):
                    res = self._refresh_terminal_session(res, task)
                local_sid = _parse_local_body_handle(res.get("session_handle"))
                if (
                    policy_applies
                    and local_sid is not None
                    and res.get("state") != SpawnState.SETTLED
                ):
                    try:
                        body_verdict = self.local_body_verdict_fn(local_sid)
                    except Exception:
                        body_verdict = _tracking().UNKNOWN
                    try:
                        body_activity = self.local_body_activity_fn(local_sid)
                    except Exception:
                        body_activity = None
                    if (
                        body_verdict != _tracking().GONE
                        and body_activity != "IDLE"
                    ):
                        if body_verdict == _tracking().LIVE:
                            continue
                        attempts = prior_attempts + 1
                        checkpoint = {
                            **self._conclusion_retry_payload(res),
                            "action": "skipped",
                            "reason": (
                                "body-not-idle"
                                if body_verdict == _tracking().LIVE
                                else "body-liveness-unknown"
                            ),
                            "liveness": body_verdict,
                            "activity": body_activity,
                            "attempts": attempts,
                            "next_attempt_at": now + min(
                                300,
                                _CONCLUSION_RETRY_BASE_SECONDS
                                * (2 ** max(0, attempts - 1)),
                            ),
                        }
                        state = (
                            _CONCLUSION_HELD
                            if attempts >= _CONCLUSION_MAX_ATTEMPTS
                            else _CONCLUSION_PENDING
                        )
                        try:
                            self.client.record_spawn_conclusion(
                                res["key"],
                                conclusion_state=state,
                                conclusion_detail=json.dumps(
                                    checkpoint,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                ),
                            )
                        except DispatchError:
                            pass
                        continue
                if (
                    res.get("state") == SpawnState.SETTLED
                    and res.get("conclusion_state") == _CONCLUSION_PENDING
                ):
                    ready, preconcluded = True, None
                else:
                    ready, preconcluded = self._exclusive_terminal_release_ready(
                        res,
                        task,
                        policy_applies=policy_applies,
                    )
                if not ready:
                    continue
                local_sid = _parse_local_body_handle(res.get("session_handle"))
                if (
                    not exclusive
                    and local_sid is not None
                    and res.get("state") != SpawnState.SETTLED
                    and not policy_applies
                ):
                    try:
                        verdict = self.local_body_verdict_fn(local_sid)
                    except Exception:
                        verdict = _tracking().UNKNOWN
                    if verdict == _tracking().UNKNOWN:
                        continue
                    try:
                        ended = self.local_end_fn(local_sid)
                    except Exception:
                        log.exception(
                            "failed to end terminal local body %s for task %s",
                            local_sid,
                            task.get("id"),
                        )
                        continue
                    if not ended:
                        continue
                detail = self._completion_detail(task)
                if res.get("state") == SpawnState.SETTLED:
                    if (
                        not policy_applies
                        or res.get("conclusion_state") != _CONCLUSION_PENDING
                    ):
                        continue
                    prior_attempts, next_attempt_at = self._conclusion_retry_meta(res)
                    if next_attempt_at > now:
                        continue
                    if prior_attempts >= _CONCLUSION_MAX_ATTEMPTS:
                        try:
                            self.client.settle_spawn(
                                res["key"],
                                detail=detail,
                                conclusion_state=_CONCLUSION_HELD,
                                conclusion_detail=res.get("conclusion_detail"),
                            )
                        except DispatchError:
                            pass
                        continue
                else:
                    if policy_applies:
                        local_sid = _parse_local_body_handle(
                            res.get("session_handle")
                        )
                    if (
                        policy_applies
                        and local_sid is not None
                        and preconcluded is None
                    ):
                        preconcluded = self._conclude_terminal_worker(res, task)
                        if (
                            preconcluded is not None
                            and self._conclusion_state(preconcluded)
                            == _CONCLUSION_PENDING
                        ):
                            retry_key = f"conclusion:{res['key']}"
                            if now < self._resume_retry_after.get(retry_key, 0.0):
                                continue
                            prior_payload = self._conclusion_retry_payload(res)
                            if prior_payload.get("same_owner_nudge") == "delivered":
                                attempts = prior_attempts + 1
                                checkpoint = {
                                    **preconcluded,
                                    "attempts": attempts,
                                    "next_attempt_at": now + min(
                                        300,
                                        _CONCLUSION_RETRY_BASE_SECONDS
                                        * (2 ** max(0, attempts - 1)),
                                    ),
                                    "same_owner_nudge": "delivered",
                                }
                                state = (
                                    _CONCLUSION_HELD
                                    if attempts >= _CONCLUSION_MAX_ATTEMPTS
                                    else _CONCLUSION_PENDING
                                )
                                try:
                                    self.client.record_spawn_conclusion(
                                        res["key"],
                                        conclusion_state=state,
                                        conclusion_detail=json.dumps(
                                            checkpoint,
                                            sort_keys=True,
                                            separators=(",", ":"),
                                        ),
                                    )
                                except DispatchError:
                                    pass
                                if exclusive:
                                    continue
                                preconcluded = checkpoint
                            elif not self._nudge_terminal_conclusion(res, task):
                                attempts = prior_attempts + 1
                                conclusion_payload = {
                                    **preconcluded,
                                    "attempts": attempts,
                                    "next_attempt_at": now + min(
                                        300,
                                        _CONCLUSION_RETRY_BASE_SECONDS
                                        * (2 ** max(0, attempts - 1)),
                                    ),
                                    "same_owner_nudge": "unavailable",
                                }
                                state = (
                                    _CONCLUSION_HELD
                                    if attempts >= _CONCLUSION_MAX_ATTEMPTS
                                    else _CONCLUSION_PENDING
                                )
                                try:
                                    self.client.record_spawn_conclusion(
                                        res["key"],
                                        conclusion_state=state,
                                        conclusion_detail=json.dumps(
                                            conclusion_payload,
                                            sort_keys=True,
                                            separators=(",", ":"),
                                        ),
                                    )
                                except DispatchError:
                                    pass
                                self._resume_retry_after[retry_key] = (
                                    now + _CONCLUSION_RETRY_BASE_SECONDS
                                )
                                continue
                            else:
                                self._resume_retry_after[retry_key] = (
                                    now + _CONCLUSION_RETRY_BASE_SECONDS
                                )
                                preconcluded = {
                                    **preconcluded,
                                    "attempts": prior_attempts + 1,
                                    "next_attempt_at": 0,
                                    "same_owner_nudge": "delivered",
                                }
                                try:
                                    res = self.client.record_spawn_conclusion(
                                        res["key"],
                                        conclusion_state=_CONCLUSION_PENDING,
                                        conclusion_detail=json.dumps(
                                            preconcluded,
                                            sort_keys=True,
                                            separators=(",", ":"),
                                        ),
                                    )
                                except DispatchError:
                                    continue
                                self._resume_retry_after.pop(retry_key, None)
                                if exclusive:
                                    continue
                    try:
                        # Release the process slot before priming the worktree.
                        # A durable pending marker keeps transient liveness or
                        # command failures retryable after settlement.
                        self.client.settle_spawn(
                            res["key"],
                            detail=detail,
                            conclusion_state=(
                                _CONCLUSION_PENDING if policy_applies else None
                            ),
                        )
                        settled += 1
                    except DispatchError:
                        continue
                outcome = (
                    preconcluded
                    if preconcluded is not None
                    else self._conclude_terminal_worker(res, task)
                )
                if outcome is None:
                    continue
                final_detail = self._append_conclusion_detail(detail, outcome)
                conclusion_state = self._conclusion_state(outcome)
                conclusion_payload: dict = dict(outcome)
                if conclusion_state == _CONCLUSION_PENDING:
                    attempts = prior_attempts + 1
                    conclusion_payload["attempts"] = attempts
                    conclusion_payload["next_attempt_at"] = now + min(
                        300,
                        _CONCLUSION_RETRY_BASE_SECONDS * (2 ** (attempts - 1)),
                    )
                    if attempts == 1 and "same_owner_nudge" not in conclusion_payload:
                        conclusion_payload["same_owner_nudge"] = (
                            "delivered"
                            if self._nudge_terminal_conclusion(res, task)
                            else "unavailable"
                        )
                    if attempts >= _CONCLUSION_MAX_ATTEMPTS:
                        conclusion_state = _CONCLUSION_HELD
                try:
                    self.client.settle_spawn(
                        res["key"],
                        detail=final_detail,
                        conclusion_state=conclusion_state,
                        conclusion_detail=json.dumps(
                            conclusion_payload,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    )
                except DispatchError:
                    log.exception(
                        "could not record terminal conclusion outcome for %s",
                        res["key"],
                    )
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
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
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
            probe_worktree = _worktree_from_reservation(res, owner)
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
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
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
                                release_spawn=False,
                            )
                        except DispatchError:
                            pass  # lease-expiry GC is the backstop requeue
                    try:
                        detail = f"fleet body confirmed gone ({host}:{bridge_sid})"
                        productive = _reservation_made_progress(res, task)
                        if res.get("worktree_ownership") == "created":
                            self.client.request_spawn_release(
                                res["key"],
                                detail=(
                                    f"{detail}; productive turn completed"
                                    if productive
                                    else detail
                                ),
                                disposition="settled" if productive else "failed",
                            )
                        elif productive:
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
                                release_spawn=False,
                            )
                        except DispatchError:
                            pass  # lease-expiry GC is the backstop requeue
                    try:
                        detail = f"local body confirmed gone ({local_sid})"
                        productive = _reservation_made_progress(res, task)
                        if res.get("worktree_ownership") == "created":
                            self.client.request_spawn_release(
                                res["key"],
                                detail=(
                                    f"{detail}; productive turn completed"
                                    if productive
                                    else detail
                                ),
                                disposition="settled" if productive else "failed",
                            )
                        elif productive:
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
            worktree = _worktree_from_reservation(res, owner)
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
                            release_spawn=False,
                        )
                    except DispatchError:
                        pass  # lease-expiry GC is the backstop requeue
                detail = f"owner confirmed gone ({worktree})"
                productive = _reservation_made_progress(res, task)
                if res.get("worktree_ownership") == "created":
                    self.client.request_spawn_release(
                        res["key"],
                        detail=(
                            f"{detail}; productive turn completed"
                            if productive
                            else detail
                        ),
                        disposition="settled" if productive else "failed",
                    )
                elif productive:
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

    def redrive_unclaimed_spawns(self) -> int:
        """Prompt live embodied workers that exist but never claimed the task.

        A supervisor/bridge restart can leave a reservation in ``spawned`` while
        the task is still ``queued`` and unowned: the body exists, but its seed
        was lost or never resumed. That reservation must remain active (to
        prevent duplicate spawns), but the live worker needs one explicit drive
        prompt so it can claim/start/complete the task. Only a confirmed live
        worktree session is re-driven; unknown bridge state is left untouched.
        """
        redriven = 0
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
            key = res.get("key")
            if not key or key in self._redriven_spawn_keys:
                continue
            if res.get("release_requested"):
                continue
            if _parse_fleet_body_handle(res.get("session_handle")) is not None:
                continue
            if _parse_local_body_handle(res.get("session_handle")) is not None:
                continue
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if task.get("status") != Status.QUEUED or task.get("owner"):
                continue
            worktree = _worktree_from_reservation(res, task.get("owner"))
            if not worktree:
                continue
            machine = _machine_from_owner(task.get("owner"))
            try:
                session = self.liveness_fn(worktree, machine)
            except Exception:
                session = None
            if not session:
                continue
            try:
                self.client.record_spawn(
                    key,
                    session_handle=session.get("session_id"),
                    worktree=session.get("worktree_id") or worktree,
                )
            except DispatchError:
                pass
            try:
                if self.redrive_fn(worktree, machine, task, session, res):
                    self._redriven_spawn_keys.add(key)
                    redriven += 1
                    if self.publish_activity:
                        try:
                            self.client.set_activity(
                                task["id"], "ACTIVE", reservation_key=key
                            )
                        except DispatchError:
                            pass
                    log.info(
                        "re-drove live unclaimed embody for task %s (%s)",
                        task["id"], key,
                    )
            except Exception:
                log.exception("redrive failed for reservation %s", key)
        return redriven

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
        for res in self._pool_reservations(state=SpawnState.SPAWNED):
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
            worktree = _worktree_from_reservation(res, owner)
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

    # -- retired reactive wait compatibility seam -----------------------------

    def wait_for_turn_end(
        self,
        timeout: float,
        *,
        sleep: Callable[[float], None] | None = None,
    ) -> bool:
        """Wait for one coalesced push wake or the ordinary interval."""
        sleep = sleep or time.sleep
        if timeout < 0:
            raise ValueError("supervisor interval must be non-negative")
        if self.event_wake is not None:
            return self.event_wake.wait(timeout)
        sleep(timeout)
        return False

    def _sync_event_subscriptions(self) -> None:
        if self.event_wake is None:
            return
        subscriptions: list[BridgeSubscription] = []
        for reservation in self._pool_reservations(state=SpawnState.SPAWNED):
            fleet = _parse_fleet_body_handle(reservation.get("session_handle"))
            if fleet is None:
                continue
            host, session_id = fleet
            subscriptions.append(
                BridgeSubscription(
                    host=host,
                    session_id=session_id,
                    caller_id=self._event_caller_id,
                )
            )
        self.event_wake.update(subscriptions)

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
                evaluator_ref=self.evaluator_ref or "",
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
            # Query-side filtering keeps the result limit fair on upgraded
            # coordinators; this defensive check preserves isolation when a
            # version-skewed coordinator ignores the new query parameter.
            if task.get("evaluator_ref") != self.evaluator_ref:
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
        for res in self._pool_reservations(state=SpawnState.FAILED):
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
        self.reconcile_reserving()
        self.release_requested_bodies()
        self.bind_headless_owner_sessions()
        self.suspend_idle_headless_tasks()
        self.cool_dormant_bodies()
        self.release_resumed_cold_tasks(now=now)
        if self.evaluator is not None:
            self.advance_via_evaluator()
        if self.heartbeat or self.publish_activity:
            self.hold_live_leases()
        if self.recover:
            self.recover_gone()
        self.redrive_unclaimed_spawns()
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
            reservation = resp["reservation"]
            key = reservation["key"]
            from .embody import EmbodyUnavailable

            try:
                spawn_task = self._prepare_spawn_task(task, reservation)
            except SpawnPreparationRetained as exc:
                log.error(
                    "spawn preparation retained reservation %s for task %s: %s",
                    key,
                    task["id"],
                    exc,
                )
                continue
            except (DispatchError, EmbodyUnavailable) as exc:
                try:
                    self.client.fail_spawn(
                        key,
                        detail=f"reusable worktree preparation failed: {exc}",
                    )
                except DispatchError:
                    log.exception(
                        "bookkeeping failed for reservation %s", key
                    )
                log.warning(
                    "spawn preparation failed for task %s (%s): %s",
                    task["id"],
                    key,
                    exc,
                )
                continue
            ok, handle = self.spawn_fn(spawn_task)
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
                    detail = handle.get("error", "spawn failed")
                    if (
                        spawn_task.get("spawn_worktree_ownership") == "created"
                    ):
                        self.client.request_spawn_release(
                            key,
                            detail=detail,
                            disposition="failed",
                        )
                    else:
                        self.client.fail_spawn(key, detail=detail)
                    log.warning(
                        "spawn failed for task %s (%s): %s",
                        task["id"], key, handle.get("error"),
                    )
            except DispatchError:
                log.exception("bookkeeping failed for reservation %s", key)
        if self.event_wake is not None:
            self.event_wake.acknowledge()
        self._sync_event_subscriptions()
        return spawned

    def serve(
        self,
        *,
        interval: float = 30.0,
        on_cycle: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Run :meth:`poll_once` each cycle, waiting between cycles.

        Push delivery changes latency only; the timeout always preserves ordinary
        fixed-interval reconciliation as the correctness floor.
        """
        try:
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
                    self.wait_for_turn_end(interval)
                except KeyboardInterrupt:
                    return
        finally:
            if self.event_wake is not None:
                self.event_wake.close()
