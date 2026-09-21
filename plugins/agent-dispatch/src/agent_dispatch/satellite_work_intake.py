"""Satellite work-intake -- the claim-loop half of a ``role=satellite`` node.

Phase 1/2 (shipped) gave a satellite outbound presence, status push-out, and
a default-closed gate; Design item D (shipped) let the union view actually
*read* whatever it embodies. This module closes the remaining gap the
``satellite-agent-exposure`` effort's Phase 3 called out: a satellite
advertised presence but never **pulled** any of its own affinitied work.

There is no existing generic "poll the queue and spawn locally" daemon in
agent-dispatch to extend (every current spawn path,
``embody.spawn_embodied_worker``, fires once at task-creation time on
whichever machine created the task) -- see the effort's Proposal §Phase 3
Design for the investigation this module implements. The design is
deliberately conservative about **not** inventing a new claim transport: this
module only *discovers* candidate `queued` tasks affinitied to this machine
and triggers a local spawn for each; the spawned session performs the actual
atomic claim itself, under its own worktree identity, exactly the way every
other CLI-backed dispatch worker already does (see
``embody_prompts.autopilot_worker_prompt``). That means the natural
queued -> claimed transition already provides idempotency across ticks: once
a task is claimed, it stops showing up in a `status=queued` read. The one gap
that leaves open is a **tight race between ticks** -- a task discovered this
tick may not yet show as claimed by the time the *next* tick runs, if the
just-triggered spawn hasn't gotten around to claiming it yet -- so this module
also tracks a short-lived "recently triggered" set to avoid re-spawning for
the same task_id before that race window closes.
"""

from __future__ import annotations

import time
from typing import Any, Callable, Protocol


class _TaskLister(Protocol):
    def list(self, **params: Any) -> list[dict]: ...


#: How long a triggered-but-not-yet-observed-as-claimed task_id counts against
#: the concurrency cap before this loop is willing to reconsider it (seconds).
#: Long enough for a fresh ``agent-worktrees embody --new`` spawn to come up
#: and claim the task; short enough that a spawn that silently never claimed
#: it (a crashed session, a declined task) doesn't permanently squat on a
#: concurrency slot.
DEFAULT_TRIGGER_TTL_S = 180.0

#: Task statuses counted as "already actively occupying a concurrency slot"
#: for this machine, independent of this loop's own in-memory bookkeeping --
#: the live, coordinator-authoritative half of the concurrency cap.
_ACTIVE_STATUSES = "claimed,started"


class SatelliteWorkIntake:
    """Discovers this machine's own affinitied `queued` work on the shared
    coordinator and triggers a bounded number of local embodied spawns for it.

    Call :meth:`tick` periodically (from :class:`~agent_dispatch.federation_runner.FederationRunner`'s
    satellite branch, gated identically to the presence/heartbeat gate --
    this loop is never consulted while the outbound gate is closed).
    """

    def __init__(
        self,
        client: _TaskLister,
        *,
        machine: str,
        worker_id: str | None = None,
        project: str | None = None,
        repo: str | None = None,
        max_concurrent: int = 1,
        trigger_ttl: float = DEFAULT_TRIGGER_TTL_S,
        spawn_fn: Callable[..., Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if not machine:
            raise ValueError("machine is required")
        self._client = client
        self._machine = machine
        self._worker_id = worker_id or f"satellite:{machine}"
        self._project = project
        self._repo = repo
        self._max_concurrent = max(1, int(max_concurrent))
        self._trigger_ttl = float(trigger_ttl)
        self._spawn_fn = spawn_fn
        self._clock = clock
        #: task_id -> the tick timestamp a spawn was last triggered for it.
        self._recent_triggers: dict[str, float] = {}

    def _reap_expired_triggers(self, now: float) -> None:
        expired = [
            task_id
            for task_id, triggered_at in self._recent_triggers.items()
            if (now - triggered_at) >= self._trigger_ttl
        ]
        for task_id in expired:
            del self._recent_triggers[task_id]

    def _spawn(self, task_id: str) -> Any:
        spawn_fn = self._spawn_fn
        if spawn_fn is None:
            from .embody import spawn_embodied_worker as spawn_fn  # noqa: PLC0415
        return spawn_fn(
            task_id,
            worker_id=self._worker_id,
            project=self._project,
            repo=self._repo,
            route=" --shared",
        )

    def tick(self) -> dict:
        """Discover + trigger spawns for at most one concurrency slot's worth
        of this machine's own queued work. Never raises: a transient
        coordinator/spawn failure degrades to an empty result so a periodic
        caller's loop is never brought down by it (mirrors
        :meth:`FederationRunner.run`'s own "a transient error must not kill
        the loop" contract)."""
        now = self._clock()
        self._reap_expired_triggers(now)
        try:
            active = self._client.list(
                status=_ACTIVE_STATUSES,
                target_machine=self._machine,
                repo=self._repo,
            )
        except Exception:
            # Can't confirm current occupancy -- degrade to "assume full" so
            # a coordinator hiccup can never be misread as "clear to spawn
            # more", which would risk over-claiming past the cap.
            return {"spawned": [], "skipped_at_capacity": True, "error": "list_failed"}
        capacity = self._max_concurrent - len(active) - len(self._recent_triggers)
        if capacity <= 0:
            return {"spawned": [], "skipped_at_capacity": True}
        try:
            queued = self._client.list(
                status="queued", target_machine=self._machine, repo=self._repo
            )
        except Exception:
            return {"spawned": [], "skipped_at_capacity": False, "error": "list_failed"}
        spawned: list[str] = []
        for task in queued:
            if capacity <= 0:
                break
            task_id = task.get("id")
            if not task_id or task_id in self._recent_triggers:
                continue
            self._recent_triggers[task_id] = now
            try:
                self._spawn(task_id)
            except Exception:
                # Spawn failed to even launch -- release the slot immediately
                # rather than let a hard failure squat on it for the full TTL.
                del self._recent_triggers[task_id]
                continue
            spawned.append(task_id)
            capacity -= 1
        return {"spawned": spawned, "skipped_at_capacity": False}
