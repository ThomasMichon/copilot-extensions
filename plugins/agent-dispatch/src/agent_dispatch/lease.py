"""The **fenced-epoch coordinator lease** -- agent-dispatch federation's standby
failover, layered on top of the Phase-1 rendezvous seam.

Federation funnels the claim plane through a **single coordinator**: peers pull
work through whichever coordinator the directory advertises, so nothing is
double-claimed. This module makes that coordinator *survive its host failing*
without a leader election -- single-user federation has no Byzantine actors, so
crash/partition tolerance plus dedup suffice (see the
``agent-dispatch-federation`` effort and
``visions/agent-fabric/agent-dispatch`` §Behaviors/*coordinator-is-discovered-not-
elected* + *peers-discover-and-federate*).

Two halves of one mechanism, both **backing-agnostic** (they speak only the
:class:`~agent_dispatch.federation.Rendezvous` Protocol, never a concrete queue,
so the hosted-coordinator (Phase 3) and Dev Tunnels (Phase 4) backends drive the *same* code):

* :class:`CoordinatorLease` -- the write-side state machine. A periodic
  :meth:`~CoordinatorLease.tick` renews our own lease, stands by behind a healthy
  peer coordinator, or -- when the active one is stale or gone -- **takes over**
  with the *next* monotonic epoch. The epoch is a **fencing token**: bumping it on
  takeover fences the deposed writer.
* :class:`FencingGuard` -- the authority-side check. A directive presented below
  the current coordinator epoch is the deposed writer's late write and is
  **rejected** (:class:`FencedError`).

This module deliberately holds no transport keepalive/backoff (Phase 4) and no
server-side wiring of the guard onto claim/write endpoints -- there is no shared
external directory in the coordinator-hosted/local backend, so that enforcement
lands with the hosted-coordinator / Dev Tunnels backends where a real multi-coordinator
directory exists. Phase 2 lays only the lease + fence *mechanism*.

.. note::
   A single :class:`CoordinatorLease` is meant to be driven from **one** loop
   (the instance's supervisor tick); it is not internally locked. The directory
   it talks to *is* concurrency-safe -- the lease's single-winner guarantee on a
   simultaneous takeover comes from the directory's total order (see
   :meth:`CoordinatorLease._take_over`), not from local locking.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .satellites import ROLE_COORDINATOR, ROLE_STANDBY

if TYPE_CHECKING:
    from .federation import Rendezvous

#: Default staleness threshold: an advertised coordinator whose entry ``age`` has
#: reached this many seconds is considered failed, and a standby takes over. Kept
#: **shorter** than the directory presence TTL (``satellites.DEFAULT_TTL_SECONDS``)
#: so failover happens *before* the dead coordinator's entry is TTL-reaped.
DEFAULT_LEASE_TTL_SECONDS = 30.0


class FencedError(Exception):
    """A directive presented with a stale fencing token -- an epoch below the
    current coordinator epoch. It is the deposed (superseded) writer's late write,
    and is rejected so a failed-over-from coordinator cannot double-claim."""


@dataclass(frozen=True)
class LeaseState:
    """The outcome of one :meth:`CoordinatorLease.tick`.

    ``role`` is ``coordinator`` when ``is_active`` (this instance is the discovered
    writer) and ``standby`` otherwise. ``epoch`` is the fencing token this instance
    currently holds -- the epoch its writes carry while active.
    """

    instance: str
    epoch: int
    role: str
    is_active: bool


class CoordinatorLease:
    """A fenced-epoch coordinator lease over a :class:`Rendezvous` directory.

    Drive :meth:`tick` periodically. Each call resolves this instance's standing
    against the directory exactly once and returns the resulting :class:`LeaseState`:

    * **Renew** -- the discovered coordinator *is us*: refresh our entry at the held
      epoch (re-asserting it if a tight reap race expired it between beats).
    * **Stand by** -- a *different*, still-fresh coordinator holds the role
      (``age < lease_ttl``): we are a standby. If we had been the coordinator we have
      been superseded, so we stop advertising the role (the fence still guards any
      late write we might attempt).
    * **Take over** -- no coordinator is live, or the active one is stale
      (``age >= lease_ttl``): register ourselves ``role=coordinator`` at
      ``observed_max_epoch + 1``, then **re-read** the directory to confirm we won.
      The directory orders coordinators by ``(epoch, instance)``, so a simultaneous
      takeover resolves to a single winner deterministically -- the loser sees the
      winner and stands down. No same-epoch split-brain.
    """

    def __init__(
        self,
        rendezvous: Rendezvous,
        instance: str,
        *,
        machine: str | None = None,
        capabilities: list[str] | None = None,
        lease_ttl: float = DEFAULT_LEASE_TTL_SECONDS,
    ) -> None:
        if not instance:
            raise ValueError("instance is required")
        self._rv = rendezvous
        self._instance = instance
        self._machine = machine
        self._capabilities = list(capabilities) if capabilities else None
        self._lease_ttl = float(lease_ttl)
        # The epoch we currently hold (the token our writes carry).
        self._epoch = 0
        # The highest coordinator epoch we have ever observed (the base a takeover
        # increments above), so a new epoch is strictly greater than any predecessor.
        self._observed_epoch = 0
        # Whether we are the current (discovered) coordinator.
        self._active = False
        # Whether our directory entry currently advertises ``role=coordinator`` --
        # tracked so a deposed/lost-tie instance downgrades its own entry to standby.
        self._advertised_coordinator = False

    # -- properties ----------------------------------------------------------

    @property
    def instance(self) -> str:
        return self._instance

    @property
    def epoch(self) -> int:
        """The fencing token this instance holds -- the epoch its writes carry."""
        return self._epoch

    @property
    def is_active(self) -> bool:
        """Whether this instance is the current coordinator (the claim-plane
        writer) as of the last :meth:`tick`."""
        return self._active

    def fencing_token(self) -> int:
        """The epoch a directive from this instance must carry to clear the
        :class:`FencingGuard`. Only meaningful while :attr:`is_active`."""
        return self._epoch

    # -- the state machine ---------------------------------------------------

    def tick(self) -> LeaseState:
        """Advance the lease one step against the current directory state."""
        coord = self._rv.discover_coordinator()
        if coord is not None:
            self._observe(int(coord["epoch"]))
            if coord["instance"] == self._instance:
                return self._renew()
            if float(coord.get("age", 0.0)) < self._lease_ttl:
                return self._stand_by()
            # A different coordinator holds the role but is stale -> fall through
            # and take over, fencing it with the next epoch.
        return self._take_over()

    def resign(self) -> None:
        """Voluntarily give up the role (graceful shutdown / gate close): remove
        our directory entry so a standby takes over promptly."""
        try:
            self._rv.deregister(self._instance)
        finally:
            self._advertised_coordinator = False
            self._active = False

    # -- internals -----------------------------------------------------------

    def _observe(self, epoch: int) -> None:
        if epoch > self._observed_epoch:
            self._observed_epoch = epoch

    def _register_coordinator(self, epoch: int) -> None:
        self._rv.register(
            self._instance,
            role=ROLE_COORDINATOR,
            epoch=epoch,
            machine=self._machine,
            capabilities=self._capabilities,
        )
        self._advertised_coordinator = True

    def _renew(self) -> LeaseState:
        try:
            self._rv.heartbeat(
                self._instance, role=ROLE_COORDINATOR, epoch=self._epoch
            )
            self._advertised_coordinator = True
        except Exception:
            # Tight reap race: our entry expired between beats. Re-assert it at the
            # same epoch (self-heal without a needless epoch bump). A genuine
            # transport error re-raises from the register call below.
            self._register_coordinator(self._epoch)
        self._active = True
        return LeaseState(self._instance, self._epoch, ROLE_COORDINATOR, True)

    def _take_over(self) -> LeaseState:
        new_epoch = self._observed_epoch + 1
        self._register_coordinator(new_epoch)
        self._epoch = new_epoch
        self._observe(new_epoch)
        winner = self._rv.discover_coordinator()
        if winner is not None and winner["instance"] == self._instance:
            self._active = True
            return LeaseState(self._instance, self._epoch, ROLE_COORDINATOR, True)
        # Lost a simultaneous-takeover tie (the directory's ``(epoch, instance)``
        # order picked another instance at our epoch): adopt its epoch and stand down.
        if winner is not None:
            self._observe(int(winner["epoch"]))
        return self._stand_by()

    def _stand_by(self) -> LeaseState:
        if self._advertised_coordinator:
            # We advertised the coordinator role but are not the discovered winner
            # (deposed, or lost a tie). Downgrade our own entry to standby so the
            # directory shows a single coordinator; the fence guards any late write.
            self._rv.register(
                self._instance,
                role=ROLE_STANDBY,
                epoch=self._epoch,
                machine=self._machine,
                capabilities=self._capabilities,
            )
            self._advertised_coordinator = False
        self._active = False
        return LeaseState(self._instance, self._epoch, ROLE_STANDBY, False)


def current_fencing_epoch(rendezvous: Rendezvous) -> int:
    """The epoch the directory currently fences at -- the discovered coordinator's
    epoch, or ``0`` when no coordinator is live."""
    coord = rendezvous.discover_coordinator()
    return int(coord["epoch"]) if coord is not None else 0


class FencingGuard:
    """The authority-side fence over a :class:`Rendezvous` directory.

    A write/directive carries the epoch its issuer held when it acted
    (:meth:`CoordinatorLease.fencing_token`). :meth:`check` rejects it when that
    token is **below** the current coordinator epoch -- i.e. it came from a writer
    that has since been superseded (fenced). Backing-agnostic: it consults the
    directory, not any queue, so Phases 3-4 place the same guard at the concrete
    hosted-coordinator / Dev Tunnels transport unchanged.
    """

    def __init__(self, rendezvous: Rendezvous) -> None:
        self._rv = rendezvous

    def current_epoch(self) -> int:
        """The current fencing epoch (the discovered coordinator's, or ``0``)."""
        return current_fencing_epoch(self._rv)

    def is_valid(self, token_epoch: int) -> bool:
        """Whether ``token_epoch`` still authorizes a write (>= current epoch)."""
        return int(token_epoch) >= self.current_epoch()

    def check(self, token_epoch: int) -> None:
        """Raise :class:`FencedError` when ``token_epoch`` is stale (below the
        current coordinator epoch); return ``None`` when it is still valid."""
        current = self.current_epoch()
        if int(token_epoch) < current:
            raise FencedError(
                f"stale fencing token: epoch {token_epoch} < "
                f"current coordinator epoch {current}"
            )
