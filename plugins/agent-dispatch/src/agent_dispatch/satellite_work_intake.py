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

import math
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

#: Default bound (seconds) on one spawn attempt (``agent-worktrees embody``).
#: This loop's spawn call runs synchronously inside the same tick that also
#: asserts this node's presence -- an unbounded launch could hang that tick
#: (and every heartbeat behind it) indefinitely.
DEFAULT_SPAWN_TIMEOUT_S = 30.0

#: Default cap on spawn *attempts* triggered within a single :meth:`tick`
#: call, independent of how much concurrency `max_concurrent` still allows.
#: Each attempt runs synchronously and is individually bounded by
#: `spawn_timeout`, but several in the same tick would sum against the
#: federation directory's presence TTL (default 90s) and could starve
#: heartbeats behind them even though no single attempt hangs forever.
#: Default 1: at most one `spawn_timeout` of blocking per tick; any
#: remaining capacity simply rolls forward to the next tick.
DEFAULT_MAX_SPAWNS_PER_TICK = 1

#: Task statuses counted as "already actively occupying a concurrency slot"
#: for this machine, independent of this loop's own in-memory bookkeeping --
#: the live, coordinator-authoritative half of the concurrency cap.
_ACTIVE_STATUSES = "claimed,started"

#: Minimum page size for the queued-task discovery read, matching the
#: `/tasks` endpoint's own default. See `_fetch_all_queued`'s fairness note:
#: the endpoint orders newest-first with no oldest-first/cursor option, so a
#: single page this small could still let a steady stream of newer tasks
#: starve an older one indefinitely -- `_fetch_all_queued` pages past it
#: rather than trusting one bounded read.
_QUEUE_DISCOVERY_MIN_LIMIT = 200

#: Hard ceiling on how large a single discovery page is allowed to grow
#: while paging for the full backlog. Bounds worst-case work against a
#: pathological/adversarial backlog; a real satellite's own affinitied
#: queue realistically never approaches this.
_QUEUE_DISCOVERY_MAX_LIMIT = 5000


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
        spawn_timeout: float | None = DEFAULT_SPAWN_TIMEOUT_S,
        max_spawns_per_tick: int = DEFAULT_MAX_SPAWNS_PER_TICK,
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
        self._max_spawns_per_tick = max(1, int(max_spawns_per_tick))
        self._trigger_ttl = float(trigger_ttl)
        # Normalize here too, not just in config.satellite_spawn_timeout():
        # a direct constructor caller (tests, a future non-FederationRunner
        # embedder) passing `None`/non-positive/non-finite must not silently
        # forward an unbounded timeout straight to subprocess.run -- every
        # spawn attempt is documented as bounded, full stop.
        if spawn_timeout is None or spawn_timeout <= 0 or not math.isfinite(spawn_timeout):
            spawn_timeout = DEFAULT_SPAWN_TIMEOUT_S
        self._spawn_timeout = spawn_timeout
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

    def _clear_observed_triggers(self, active: list[dict]) -> None:
        """Drop any in-memory "recently triggered" entry the coordinator now
        confirms as actually `claimed`/`started` -- it no longer needs this
        loop's own placeholder bookkeeping and, left in place, would keep
        squatting on a concurrency slot for the rest of its TTL even after
        the task it stood in for has already finished (default cap of 1: a
        single stale entry would starve all newly queued work for up to
        `trigger_ttl` seconds after every job)."""
        for task in active:
            task_id = task.get("id")
            if task_id is not None:
                self._recent_triggers.pop(task_id, None)

    def _resolve_project(self, task: dict) -> str | None:
        """The ``--project`` to embody a claimed task under: an explicit
        override always wins; otherwise derive it from the task's own repo
        lane (:func:`agent_dispatch.embody.project_for_task`) rather than
        ever falling back to CWD discovery -- this loop runs from a
        daemon/service context with no meaningful CWD, so an unresolvable
        project must surface as a per-task spawn failure (handled the same
        as any other failed launch), never a silent misembodiment."""
        if self._project:
            return self._project
        from .embody import project_for_task  # noqa: PLC0415

        return project_for_task(task)

    def _spawn(self, task: dict) -> Any:
        spawn_fn = self._spawn_fn
        if spawn_fn is None:
            from .embody import spawn_embodied_worker as spawn_fn  # noqa: PLC0415
        project = self._resolve_project(task)
        if project is None:
            raise ValueError(
                f"cannot resolve a project for task {task.get('id')!r} -- no "
                f"AGENT_DISPATCH_SATELLITE_PROJECT configured and the task "
                f"carries no repo lane to derive one from"
            )
        return spawn_fn(
            task.get("id"),
            worker_id=self._worker_id,
            # Prefer the DISCOVERED task's own repo lane over this loop's
            # (often unset -- discovering across every lane) `self._repo`
            # filter: `spawn_embodied_worker`'s seed only adds a `--repo`
            # claim scope when it receives a non-None repo, and without one
            # the spawned session's first atomic claim is rejected outright
            # (claim requires repo context or `--all-repos`) -- the task
            # would be spawned but never actually claimed.
            repo=self._repo or task.get("repo"),
            project=project,
            route=" --shared",
            timeout=self._spawn_timeout,
        )

    def _fetch_all_queued(self, capacity: int) -> list[dict]:
        """The full `queued` backlog affinitied to this machine -- not just
        one bounded page.

        The `/tasks` endpoint orders newest-first with **no** oldest-first
        or cursor option. A single request sized to `capacity` (or even a
        larger fixed page) only ever sees the newest rows -- under a steady
        stream of newer targeted tasks, an older queued task could be pushed
        past every page's edge and starve indefinitely, and re-sorting a
        single truncated page oldest-first does not fix that (it only
        reorders what already made it into the page). So this pages: keep
        requesting a larger `limit` as long as the returned count equals the
        requested one (a full page is itself evidence more rows may exist),
        stopping once a page comes back short (the true end of the backlog)
        or :data:`_QUEUE_DISCOVERY_MAX_LIMIT` is reached (a bound against a
        pathological/adversarial backlog -- a real satellite's own
        affinitied queue realistically never approaches it).
        """
        limit = max(_QUEUE_DISCOVERY_MIN_LIMIT, capacity + len(self._recent_triggers))
        while True:
            rows = self._client.list(
                status="queued",
                target_machine=self._machine,
                repo=self._repo,
                limit=limit,
            )
            if len(rows) < limit or limit >= _QUEUE_DISCOVERY_MAX_LIMIT:
                return rows
            limit = min(limit * 2, _QUEUE_DISCOVERY_MAX_LIMIT)

    def tick(self) -> dict:
        """Discover + trigger spawns for at most one concurrency slot's worth
        of this machine's own queued work. Never raises: a transient
        coordinator/spawn failure degrades to an empty result so a periodic
        caller's loop is never brought down by it (mirrors
        :meth:`FederationRunner.run`'s own "a transient error must not kill
        the loop" contract)."""
        now = self._clock()
        self._reap_expired_triggers(now)
        # `limit` defaults to the `/tasks` endpoint's own 200 -- request at
        # least `max_concurrent` so an operator-configured cap above 200
        # can never be undercounted into "capacity available" by a
        # truncated active-task read.
        active_limit = max(200, self._max_concurrent)
        try:
            active = self._client.list(
                status=_ACTIVE_STATUSES,
                target_machine=self._machine,
                repo=self._repo,
                limit=active_limit,
            )
        except Exception:
            # Can't confirm current occupancy -- degrade to "assume full" so
            # a coordinator hiccup can never be misread as "clear to spawn
            # more", which would risk over-claiming past the cap.
            return {"spawned": [], "skipped_at_capacity": True, "error": "list_failed"}
        self._clear_observed_triggers(active)
        capacity = self._max_concurrent - len(active) - len(self._recent_triggers)
        if capacity <= 0:
            return {"spawned": [], "skipped_at_capacity": True}
        try:
            queued = self._fetch_all_queued(capacity)
        except Exception:
            return {"spawned": [], "skipped_at_capacity": False, "error": "list_failed"}
        # Oldest-first across the WHOLE fetched backlog (not just one page):
        # `_bulk_task_dict` (server) carries `created_at`; fall back to
        # `0.0` (sorts first) for a malformed/legacy row missing it rather
        # than raising on `sorted()`.
        queued = sorted(queued, key=lambda t: t.get("created_at") or 0.0)
        spawned: list[str] = []
        attempts = 0
        for task in queued:
            if capacity <= 0:
                break
            if attempts >= self._max_spawns_per_tick:
                # Spawn attempts run synchronously, each individually
                # bounded by `spawn_timeout` -- but summed across several
                # in one tick they could still exceed the federation
                # directory's presence TTL (default 90s) and starve
                # heartbeats behind them, even though every single attempt
                # is itself bounded. Cap attempts per tick (default 1) so
                # the worst case per tick is one `spawn_timeout`, not
                # `capacity * spawn_timeout`; remaining capacity rolls
                # forward to the next tick.
                break
            task_id = task.get("id")
            if not task_id or task_id in self._recent_triggers:
                continue
            self._recent_triggers[task_id] = now
            attempts += 1
            if not self._try_spawn(task):
                # Failed to even launch (or launched but exited nonzero) --
                # release the slot immediately rather than let a hard
                # failure squat on it for the full TTL.
                del self._recent_triggers[task_id]
                continue
            spawned.append(task_id)
            capacity -= 1
        return {"spawned": spawned, "skipped_at_capacity": False}

    def _try_spawn(self, task: dict) -> bool:
        """Trigger a spawn for ``task``; ``True`` only on a genuine launch.

        ``embody.spawn_embodied_worker`` runs its subprocess with
        ``check=False``, so a nonzero ``returncode`` (the ``agent-worktrees
        embody`` invocation itself failing) does **not** raise -- it comes
        back as an ordinary result. Duck-type for a ``returncode`` attribute
        so both that real return shape and a caller-supplied ``spawn_fn`` in
        tests (which may return a plain sentinel with no such attribute) are
        handled: a present, nonzero ``returncode`` is a failure exactly like
        a raised exception; anything else (no such attribute, or zero) is a
        successful trigger.
        """
        try:
            result = self._spawn(task)
        except Exception:
            return False
        returncode = getattr(result, "returncode", None)
        return returncode is None or returncode == 0

