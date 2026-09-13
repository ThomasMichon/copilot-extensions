"""Default spawn/liveness/conclusion callables and embody-backend factories.

Split out of :mod:`agent_dispatch.supervisor` (see that module's own docstring
for the invariant these functions serve). This module is deliberately
**stateless**: every function here is a pure default or a factory that closes
over only its own arguments -- none of it touches :class:`Supervisor` instance
state. That is what makes it a safe, low-risk extraction: nothing here is
monkeypatched via ``self``, and every name is re-exported unchanged from
``supervisor.py`` so existing call sites and tests (which patch
``agent_dispatch.supervisor.<name>``) are unaffected.
"""

from __future__ import annotations

import json
import stat
import uuid
from collections.abc import Callable, Mapping
from pathlib import Path


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

#: A local agent-worktrees directory-presence probe: ``worktree -> True`` (still
#: on disk, any tracking status), ``False`` (confirmed absent), or ``None``
#: (resolver failure -- never treated as absence). Used only to retire an
#: unleased, worktree-only spawn reservation whose task never captured an
#: ``owner_session_id`` -- see :meth:`Supervisor.release_requested_bodies`.
WorktreeDirectoryPresentFn = Callable[[str], "bool | None"]

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

#: Stop a local headless bridge session while preserving its durable record.
LocalColdFn = Callable[[str], bool]

#: Stop a remote fleet bridge session while preserving it for later resume.
FleetColdFn = Callable[[str, str], bool]


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


def _default_verdict(worktree: str, machine: str | None, owner_session_id: str | None) -> str:
    """Resolve an embodied session's liveness to a **tri-state verdict** via the
    agent-bridge registry (shells the CLI, cross-machine over SSH). Delegates to
    :func:`agent_dispatch.tracking.liveness_verdict`; every probe failure collapses
    to ``unknown`` (never ``gone``), so recovery never fires on ignorance."""
    from . import tracking

    return tracking.liveness_verdict(worktree, machine=machine, owner_session_id=owner_session_id)


def _default_worktree_directory_present(worktree: str) -> bool | None:
    """Whether ``worktree`` still exists on disk, per the local agent-worktrees
    registry (any tracking status). Delegates to
    :func:`agent_dispatch.tracking.worktree_directory_present`; ``None`` on any
    resolver failure, never a guess."""
    from . import tracking

    return tracking.worktree_directory_present(worktree)


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
    """Build a re-drive sender that uses the same coordinator route as spawn.

    A re-drive targets a **live** embodied session that never claimed its
    spawned task -- the same worktree/session the supervisor originally
    embodied with the full autopilot seed. That worker already had a chance to
    read the charter (``agent-dispatch charter show autopilot``) the first
    time it embodied, so the re-drive seed is built ``concise=True``: a short
    reminder of the task-specific mechanics that points back at the charter
    command instead of re-inlining the whole behavioral essay a second time.
    """

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
            task_id, worker_id=worker_id, route=route, concise=True
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
    return make_redrive_sender()(worktree, machine, task, session, reservation)


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
    rest = session_handle[len(_FLEET_BODY_PREFIX) :]
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
    sid = session_handle[len(_LOCAL_BODY_PREFIX) :]
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


def _target_directory_missing(target_dir: str) -> bool | None:
    """Classify an absolute target directory without treating stat errors as loss."""
    path = Path(target_dir)
    if not path.is_absolute():
        return None
    try:
        target_stat = path.stat()
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return None
    return not stat.S_ISDIR(target_stat.st_mode)


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
        prior_session = _parse_local_body_handle(task.get("spawn_session_handle"))
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
        except bridge.BridgeCarriedSessionBusy as exc:
            return False, {"error": str(exc), "deferred": True}
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


def make_label_routed_spawn(default: SpawnFn, *, overrides: Mapping[str, SpawnFn]) -> SpawnFn:
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
        value = selector(task) if callable(selector) else getattr(selected, name, fallback)
        return value if isinstance(value, str) and value else fallback

    spawn.requires_reusable_worktree_for = requires_reusable_worktree
    spawn.allocation_driver_for = lambda task: selected_attribute(
        task, "allocation_driver", "agent-dispatch"
    )
    spawn.allocation_interface_for = lambda task: selected_attribute(
        task, "allocation_interface", "cli"
    )
    spawn.allocation_project_for = lambda task: selected_attribute(task, "allocation_project", "")
    return spawn
