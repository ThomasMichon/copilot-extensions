"""Fleet dispatch: turn queued tasks into embody bodies on a **pool of hosts**.

The base supervisor (:mod:`agent_dispatch.supervisor`) spawns an embody body on
its **own** machine. Fleet dispatch lets an always-on supervisor instead fan
bodies out across a **pool of capable-but-not-always-on hosts** -- the shape a
containerized, always-on producer needs when the real work should run on
workstations elsewhere in the mesh.

The design (Model C) keeps the strong guarantee where it belongs:

- **Origin-owned lease.** The spawn reservation and the task lease stay on the
  supervisor's (origin's) coordinator, so at-most-once is **fleet-wide**, not
  per-pool-host. Only the *body* runs remotely; it drives the origin task back
  over the existing bidirectional SSH mesh (see
  :func:`agent_dispatch.embody.spawn_fleet_embodied_worker`). No new network bind
  is introduced on the origin.
- **Liveness-gated selection.** A pool host is a candidate only when it is
  reachable over SSH **and** has ``agent-worktrees`` (so it can actually embody).
  The first live candidate by policy (config order) is chosen.
- **Defer, don't fail, when the pool is asleep.** :meth:`FleetSpawner.can_spawn`
  is the supervisor's **capacity gate**: when no host is live, the task is skipped
  for this cycle **without a reservation**, so an all-asleep pool never burns
  spawn attempts toward the dead-letter bound. Selection is cached briefly so the
  gate and the subsequent spawn agree on the same host without re-probing.

Nothing here is consumer-specific: a :class:`FleetSpawner` is keyed only on host
aliases + task ids.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable, Sequence

from . import embody

log = logging.getLogger("agent-dispatch.fleet")

#: A liveness probe for a pool host: ``alias -> bool`` (reachable + can embody).
LivenessFn = Callable[[str], bool]

#: How long a host's liveness result is trusted before it is re-probed. Keeps the
#: capacity gate + the spawn from double-probing the same host within a cycle.
_LIVENESS_TTL = 15.0


def _ssh_alias(host: str) -> str:
    """Lowercased SSH alias for ``host`` (facility ``Host`` blocks are lowercase)."""
    return host.strip().lower()


def host_can_embody(host: str, *, timeout: float = 8.0) -> bool:
    """True when ``host`` is reachable over SSH **and** has ``agent-worktrees``.

    A single cheap probe that doubles as reachability + capability: SSH to the
    host (its facility alias, never a raw IP; ``BatchMode`` so a missing key fails
    fast) and check ``command -v agent-worktrees``. Any failure -- ssh absent,
    unreachable host, timeout, or no ``agent-worktrees`` -- returns False, so an
    asleep or unprovisioned host is simply not a candidate.
    """
    exe = shutil.which("ssh")
    if exe is None:
        return False
    cmd = [
        exe, "-o", "BatchMode=yes", "-o", "ConnectTimeout=5",
        _ssh_alias(host), "command -v agent-worktrees",
    ]
    try:
        result = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            cmd, check=False, capture_output=True, text=True, timeout=timeout
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class FleetSpawner:
    """A :data:`~agent_dispatch.supervisor.SpawnFn` that dispatches embody bodies
    across a **pool** of remote hosts, with a liveness capacity gate.

    Wire it into a :class:`~agent_dispatch.supervisor.Supervisor` as **both** the
    ``spawn_fn`` and the ``capacity_gate``::

        fleet = FleetSpawner(["host-a", "host-b"], origin="my-alias")
        Supervisor(client, spawn_fn=fleet, capacity_gate=fleet.can_spawn, ...)

    ``pool`` is the ordered candidate list (first live host wins). ``origin`` is
    the supervisor machine's own SSH alias, which each dispatched body uses to
    report its lease back (Model C). ``liveness`` is injectable for testing;
    it defaults to :func:`host_can_embody`.
    """

    def __init__(
        self,
        pool: Sequence[str],
        *,
        origin: str,
        driver: str = embody.DEFAULT_DRIVER,
        verify_timeout: int = 0,
        liveness: LivenessFn | None = None,
        spawn_fn: Callable[..., subprocess.CompletedProcess] | None = None,
        now: Callable[[], float] = time.time,
    ):
        self.pool = [h.strip() for h in pool if h and h.strip()]
        if not self.pool:
            raise ValueError("FleetSpawner requires a non-empty pool of host aliases")
        self.origin = origin.strip()
        if not self.origin:
            raise ValueError("FleetSpawner requires a non-empty origin alias")
        self.driver = driver
        self.verify_timeout = verify_timeout
        self._liveness = liveness or host_can_embody
        self._spawn = spawn_fn or embody.spawn_fleet_embodied_worker
        self._now = now
        #: task_id -> chosen host, so can_spawn() and __call__() agree per cycle.
        self._selection: dict[str, str] = {}
        #: host -> (checked_at, is_live); short-TTL so a cycle probes each host once.
        self._live_cache: dict[str, tuple[float, bool]] = {}

    # -- host selection ------------------------------------------------------

    def _is_live(self, host: str) -> bool:
        now = self._now()
        cached = self._live_cache.get(host)
        if cached is not None and (now - cached[0]) < _LIVENESS_TTL:
            return cached[1]
        live = bool(self._liveness(host))
        self._live_cache[host] = (now, live)
        return live

    def _candidates(self, task: dict) -> list[str]:
        """Ordered candidate hosts for ``task``.

        A task pinned to a ``target_machine`` that is in the pool is tried
        **first** (honor an explicit affinity), then the rest of the pool in
        config order. A ``target_machine`` outside the pool is ignored here -- the
        pool is the authority on where fleet bodies may run.
        """
        target = (task.get("target_machine") or "").strip()
        if target and target in self.pool:
            return [target] + [h for h in self.pool if h != target]
        return list(self.pool)

    def select(self, task: dict) -> str | None:
        """Pick the first live candidate host for ``task``; cache and return it,
        or ``None`` when the whole pool is asleep/unreachable this cycle."""
        tid = str(task.get("id"))
        for host in self._candidates(task):
            if self._is_live(host):
                self._selection[tid] = host
                return host
        self._selection.pop(tid, None)
        return None

    def can_spawn(self, task: dict) -> bool:
        """Capacity gate: True iff a live pool host is available for ``task``.

        Used as the supervisor's ``capacity_gate`` so a task is reserved only when
        the fleet can actually take it -- an all-asleep pool defers the task
        (no reservation, no burned attempt) instead of failing it.
        """
        return self.select(task) is not None

    # -- spawn ---------------------------------------------------------------

    def __call__(self, task: dict) -> tuple[bool, dict]:
        """Spawn the task's body on its selected (or freshly selected) pool host.

        Returns ``(ok, handle)`` in the :data:`SpawnFn` contract. ``handle`` carries
        ``session``/``worktree`` (and the chosen ``machine`` + synthetic ``owner``)
        on success, or ``error`` on failure. A no-live-host result reports failure
        with ``deferred=True`` -- but the capacity gate normally prevents the
        supervisor from reserving in that case, so this is only a race backstop.
        """
        tid = str(task.get("id"))
        host = self._selection.get(tid) or self.select(task)
        if host is None:
            return False, {"error": "no live pool host", "deferred": True}
        # Synthetic, opaque lease-holder id assigned by the supervisor: it is what
        # the remote body claims/completes the ORIGIN task under (its own worktree
        # cannot identify it to the origin). Stable for this spawn attempt.
        owner = f"fleet-{tid}-{uuid.uuid4().hex[:6]}"
        try:
            result = self._spawn(
                host,
                tid,
                origin=self.origin,
                owner=owner,
                worker_id=owner,
                driver=self.driver,
                verify_timeout=self.verify_timeout,
            )
        except embody.EmbodyUnavailable as exc:
            return False, {"error": str(exc)}
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[:200] or "nonzero exit"
            return False, {"error": f"embody on {host!r} failed: {detail}"}
        handle = embody.parse_handle(result)
        handle["machine"] = host
        handle["owner"] = owner
        # The reservation now holds this task's state; the per-cycle selection
        # cache only needs to bridge can_spawn() -> __call__() within one
        # iteration, so drop it here to keep the cache bounded to in-flight
        # selections over a long-running supervisor.
        self._selection.pop(tid, None)
        log.info("fleet-dispatched task %s to %s (owner %s)", tid, host, owner)
        return True, handle
