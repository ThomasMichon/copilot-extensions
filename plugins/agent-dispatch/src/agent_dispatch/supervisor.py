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

import logging
import time
import uuid
from collections.abc import Callable, Sequence

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


def _worktree_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.worktree_from_owner(owner)


def _machine_from_owner(owner: str | None) -> str | None:
    from . import tracking

    return tracking.machine_from_owner(owner)


def make_embody_spawn(
    coordinator_url: str, *, driver: str = "agent-dispatch", verify_timeout: int = 0
) -> SpawnFn:
    """Build a :data:`SpawnFn` that embodies a worker via ``agent-worktrees``.

    Degrades cleanly: if the ``agent-worktrees`` CLI is absent, the spawn reports
    failure (the supervisor fails the reservation, leaving the task queued).
    """
    from . import embody

    def spawn(task: dict) -> tuple[bool, dict]:
        worker_id = f"embody-{uuid.uuid4().hex[:8]}"
        try:
            result = embody.spawn_embodied_worker(
                task["id"],
                coordinator_url=coordinator_url,
                worker_id=worker_id,
                driver=driver,
                verify_timeout=verify_timeout,
            )
        except embody.EmbodyUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            return False, {"error": (result.stderr or "").strip()[:200] or "nonzero exit"}
        return True, embody.parse_handle(result)

    return spawn


class Supervisor:
    """Reserve -> spawn -> record, with terminal-state reconciliation.

    ``max_concurrent`` caps the number of in-flight spawns (``reserving`` +
    ``spawned`` reservations). ``max_attempts`` bounds failed spawn attempts per
    task before it is **dead-lettered** (held, no longer auto-retried; 0 disables
    the bound). ``repo`` scopes the lane; ``labels`` (if given) restricts spawning
    to queued tasks carrying at least one of them -- the **opt-in** so a
    supervisor only embodies work explicitly marked for autopilot.
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
        supervisor_id: str | None = None,
        heartbeat: bool = True,
        liveness_fn: LivenessFn | None = None,
        capacity_gate: Callable[[dict], bool] | None = None,
    ):
        self.client = client
        self.spawn_fn = spawn_fn
        self.repo = repo
        self.labels = set(labels) if labels else None
        self.max_concurrent = max(1, int(max_concurrent))
        #: Bound on failed spawn attempts per task before it is dead-lettered
        #: (held, no longer auto-retried). 0 disables the bound (retry forever).
        self.max_attempts = max(0, int(max_attempts))
        self.supervisor_id = supervisor_id or f"supervisor-{uuid.uuid4().hex[:8]}"
        self.heartbeat = heartbeat
        self.liveness_fn = liveness_fn or _default_liveness
        #: Optional pre-reservation capacity gate. When it returns False for a
        #: task, the task is **skipped this cycle without a reservation** -- so a
        #: transient "no capacity" (e.g. a fleet pool that is entirely asleep)
        #: defers the task instead of burning a spawn attempt toward the
        #: dead-letter bound. Default (None) always admits, preserving the local
        #: spawn behavior exactly.
        self.capacity_gate = capacity_gate

    # -- helpers -------------------------------------------------------------

    def _eligible(self, now: float) -> list[dict]:
        """Queued, due tasks in the lane matching the label opt-in (oldest first)."""
        tasks = self.client.list(repo=self.repo, status=Status.QUEUED, limit=200)
        out: list[dict] = []
        for t in tasks:
            if (t.get("not_before") or 0) > now:
                continue  # deferred: not due yet
            if self.labels is not None and not (self.labels & set(t.get("labels") or [])):
                continue  # not opted in
            out.append(t)
        out.sort(key=lambda t: t.get("created_at") or 0)
        return out

    def _active_reservations(self) -> list[dict]:
        return self.client.list_reservations(
            state=f"{SpawnState.RESERVING},{SpawnState.SPAWNED}", limit=500
        )

    # -- phases --------------------------------------------------------------

    def reconcile(self) -> int:
        """Settle ``spawned`` reservations whose task reached a terminal state.

        This is the *only* automatic release of a reservation -- and only for a
        provably-finished task -- so it can never free a still-running spawn for a
        double-launch. Returns the number settled.
        """
        settled = 0
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue  # task vanished; leave the reservation for a human
            if task.get("status") in _TERMINAL:
                try:
                    self.client.settle_spawn(res["key"], detail=f"task {task['status']}")
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
        held = 0
        for res in self.client.list_reservations(state=SpawnState.SPAWNED, limit=500):
            worktree = res.get("worktree")
            try:
                task = self.client.get(res["task_id"])
            except DispatchError:
                continue
            if task.get("status") not in _LEASED:
                continue
            owner = task.get("owner")
            probe_worktree = worktree or _worktree_from_owner(owner)
            if not probe_worktree or not owner:
                continue
            try:
                session = self.liveness_fn(probe_worktree, _machine_from_owner(owner))
            except Exception:  # liveness is best-effort -- never let a probe be fatal
                session = None
            if not session:
                continue  # not confirmed alive -> let the lease ride
            try:
                self.client.heartbeat(task["id"], owner)
                held += 1
            except DispatchError:
                pass
        return held

    def _dead_lettered(self) -> set[str]:
        """Task ids that have exhausted their spawn attempts (>= ``max_attempts``
        failed reservations) and should no longer be auto-retried.

        Held, not lost: the failed reservation history stays queryable
        (``reservations list --state failed``) and an operator can intervene.
        Returns an empty set when the bound is disabled (``max_attempts == 0``).
        """
        if not self.max_attempts:
            return set()
        counts: dict[str, int] = {}
        for res in self.client.list_reservations(state=SpawnState.FAILED, limit=1000):
            counts[res["task_id"]] = counts.get(res["task_id"], 0) + 1
        return {tid for tid, n in counts.items() if n >= self.max_attempts}

    def poll_once(self, *, now: float | None = None) -> list[str]:
        """One supervision cycle: reconcile, hold live leases, then spawn eligible
        tasks up to the cap.

        Returns the ids of tasks spawned this cycle.
        """
        now = time.time() if now is None else now
        self.reconcile()
        if self.heartbeat:
            self.hold_live_leases()
        dead_lettered = self._dead_lettered()
        active = len(self._active_reservations())
        spawned: list[str] = []
        for task in self._eligible(now):
            if active >= self.max_concurrent:
                break
            if task["id"] in dead_lettered:
                log.warning(
                    "task %s dead-lettered (>= %d failed spawn attempts); skipping",
                    task["id"], self.max_attempts,
                )
                continue
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
        """Run :meth:`poll_once` every ``interval`` seconds until interrupted."""
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
                time.sleep(interval)
            except KeyboardInterrupt:
                return
