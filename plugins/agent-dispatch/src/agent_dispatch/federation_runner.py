"""The federation **runtime** -- the loop that actually *runs* the rendezvous and
the fenced-epoch lease.

Phases 1-2 shipped the pieces as libraries: the fleet directory + rendezvous
interface (:mod:`agent_dispatch.federation`, :mod:`agent_dispatch.satellites`) and
the fenced-epoch lease (:mod:`agent_dispatch.lease`). Nothing *drove* them. This
module is that driver: a :class:`FederationRunner` that, on an interval, keeps this
instance present in the directory and -- for a lease-eligible node -- advances the
:class:`~agent_dispatch.lease.CoordinatorLease` so the coordinator role pins to one
instance and fails over safely (see the ``agent-dispatch-federation`` effort,
Phase 3).

The rendezvous the runner drives is built by a factory over the **shared/hosted**
coordinator URL (:func:`agent_dispatch.config.shared_url`): the hosted coordinator is
simply the stable URL the shared coordinator rides, so the "hosted backend" needs
no new transport code -- it is the :class:`~agent_dispatch.federation.CoordinatorRendezvous`
pointed *through* the hosted coordinator. Phase 4 adds a Dev Tunnels factory as a sibling; the
runner above the factory is unchanged.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from . import config
from .federation import CoordinatorRendezvous
from .lease import CoordinatorLease
from .satellites import ROLE_SATELLITE

if TYPE_CHECKING:
    from .federation import Rendezvous


# -- rendezvous factories ----------------------------------------------------


def build_rendezvous(url: str, *, token: str | None = None) -> CoordinatorRendezvous:
    """A coordinator-hosted rendezvous over the coordinator at ``url``."""
    from .client import DispatchClient

    return CoordinatorRendezvous(DispatchClient(url, token=token))


def hosted_rendezvous() -> CoordinatorRendezvous | None:
    """The rendezvous over the **shared/hosted** coordinator, or ``None`` when no
    ``AGENT_DISPATCH_SHARED_URL`` is configured (federation has no directory to
    reach). The hosted coordinator is just the stable URL this shared coordinator rides."""
    url = config.shared_url()
    if not url:
        return None
    return build_rendezvous(url, token=config.shared_token())


def local_rendezvous(url: str | None = None, *, token: str | None = None) -> CoordinatorRendezvous:
    """The rendezvous over the **local** coordinator (same-host federation / tests)."""
    return build_rendezvous(url or config.client_url(), token=token or config.client_token())


def rendezvous_from_config() -> Rendezvous | None:
    """The rendezvous for whichever backend ``AGENT_DISPATCH_FEDERATION_BACKEND``
    selects (``gateway`` default -> :func:`hosted_rendezvous`; ``devtunnels`` ->
    the Phase-4 Dev Tunnels backend), or ``None`` when that backend isn't
    configured/reachable. Kept separate from :func:`hosted_rendezvous` so a
    caller that specifically wants the Gateway backend (e.g. a test) is
    unaffected by the backend selector."""
    if config.federation_backend() == "devtunnels":
        from .devtunnel_rendezvous import devtunnel_rendezvous

        return devtunnel_rendezvous()
    return hosted_rendezvous()


# -- the runner --------------------------------------------------------------


class FederationRunner:
    """Drives federation for one instance against a :class:`Rendezvous` directory.

    Call :meth:`tick` periodically (or :meth:`start` a background loop). Each tick:

    * **lease-eligible** node (role ``coordinator`` / ``standby``) -- advance a
      :class:`~agent_dispatch.lease.CoordinatorLease`; the lease decides whether we
      are the active coordinator or a standby (discovery, not election), so the
      *reported* role is the lease outcome, not the static config hint.
    * **presence-only** node (role ``peer`` / ``satellite``) -- register once, then
      heartbeat; if our entry was TTL-reaped between beats, re-register. A
      ``satellite`` additionally pushes its own live embodiment status (worktrees
      + per-worktree activity, sourced only from this machine's local bridge
      sessions -- see :func:`agent_dispatch.tracking.satellite_status_snapshot`)
      on every register/heartbeat, and is gated: while
      :func:`agent_dispatch.config.satellite_gate_open` is ``False`` (the
      default), it never registers at all, and withdraws immediately if the
      gate closes mid-session (see the ``satellite-agent-exposure`` effort).

    :meth:`discover_coordinator` / :meth:`discover_peers` expose the directory reads
    peers use to route claims through the pinned coordinator.
    """

    def __init__(
        self,
        rendezvous: Rendezvous,
        instance: str,
        *,
        role: str = "peer",
        machine: str | None = None,
        capabilities: list[str] | None = None,
        lease_ttl: float | None = None,
        clock=time.time,
    ) -> None:
        if not instance:
            raise ValueError("instance is required")
        if role not in config.FEDERATION_ROLES:
            raise ValueError(f"unknown federation role: {role!r}")
        self._rv = rendezvous
        self._instance = instance
        self._role = role
        self._machine = machine
        self._capabilities = list(capabilities) if capabilities else None
        self._clock = clock
        self._registered = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lease: CoordinatorLease | None = None
        if role in config.FEDERATION_LEASE_ROLES:
            lease_kwargs = {} if lease_ttl is None else {"lease_ttl": lease_ttl}
            self._lease = CoordinatorLease(
                rendezvous,
                instance,
                machine=machine,
                capabilities=self._capabilities,
                **lease_kwargs,
            )

    @property
    def instance(self) -> str:
        return self._instance

    @property
    def lease_eligible(self) -> bool:
        return self._lease is not None

    def tick(self) -> dict:
        """Advance federation one step; return a small status dict."""
        if self._lease is not None:
            state = self._lease.tick()
            return {
                "instance": self._instance,
                "role": state.role,
                "epoch": state.epoch,
                "is_active": state.is_active,
            }
        if self._role == ROLE_SATELLITE and not config.satellite_gate_open():
            # Outbound exposure gate closed (default): never register or
            # heartbeat, and withdraw promptly if a registration exists --
            # an operator closing it mid-session must take effect on the
            # very next tick, not linger until the directory's own TTL reap
            # (see the satellite-agent-exposure effort's security steer:
            # exposure is opt-in, never ambient).
            #
            # Always ATTEMPT deregister here, never gate it on self._registered:
            # this in-process flag only reflects what THIS runner instance did.
            # A process restart (crash, redeploy, a fresh runner for the same
            # stable instance id) starts with _registered=False even though a
            # PRIOR process's registration can still be live in the directory --
            # gating on the local flag would skip cleanup entirely in that case,
            # leaving a stale entry exposed until TTL expiry. deregister() is
            # idempotent (a no-op, returning False, when nothing is registered),
            # so attempting it unconditionally is always safe.
            self._rv.deregister(self._instance)
            self._registered = False
            return {
                "instance": self._instance,
                "role": self._role,
                "epoch": 0,
                "is_active": False,
                "gate_state": "closed",
            }
        # Presence-only: register once, then heartbeat (re-register if reaped).
        if not self._registered:
            self._register()
        else:
            try:
                self._heartbeat()
            except Exception:
                # Entry expired between beats -> re-assert it.
                self._register()
        return {
            "instance": self._instance,
            "role": self._role,
            "epoch": 0,
            "is_active": False,
        }

    def _satellite_kwargs(self) -> dict:
        """Extra register/heartbeat kwargs for a satellite role -- its live
        embodiment status, sourced only from this machine's own local bridge
        sessions (see :func:`agent_dispatch.tracking.satellite_status_snapshot`).
        Empty for every other role: peers/coordinator/standby push no status."""
        if self._role != ROLE_SATELLITE:
            return {}
        from .tracking import satellite_status_snapshot

        worktrees, status = satellite_status_snapshot()
        return {"worktrees": worktrees, "status": status}

    def _register(self) -> None:
        self._rv.register(
            self._instance,
            role=self._role,
            machine=self._machine,
            capabilities=self._capabilities,
            **self._satellite_kwargs(),
        )
        self._registered = True

    def _heartbeat(self) -> None:
        self._rv.heartbeat(self._instance, role=self._role, **self._satellite_kwargs())

    def discover_coordinator(self) -> dict | None:
        return self._rv.discover_coordinator()

    def discover_peers(self, *, role: str | None = None) -> list[dict]:
        return self._rv.discover_peers(role=role)

    def status(self) -> dict:
        """A read-only snapshot for the CLI: this node's view of the fleet."""
        coord = self._rv.discover_coordinator()
        return {
            "instance": self._instance,
            "role": self._role,
            "lease_eligible": self.lease_eligible,
            "coordinator": coord,
            "peers": self._rv.discover_peers(),
        }

    # -- background loop -----------------------------------------------------

    def run(self, *, interval: float, stop_event: threading.Event | None = None) -> None:
        """Drive :meth:`tick` every ``interval`` seconds until stopped (blocking)."""
        stop = stop_event or self._stop
        while not stop.is_set():
            try:
                self.tick()
            except Exception:
                # A transient directory error must not kill the loop; the next tick
                # re-attempts (and re-registers / re-takes the lease as needed).
                pass
            stop.wait(interval)

    def start(self, *, interval: float) -> None:
        """Run the loop in a daemon thread."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self.run, kwargs={"interval": interval}, daemon=True
        )
        self._thread.start()

    def stop(self, *, resign: bool = True, timeout: float = 5.0) -> None:
        """Stop the background loop and (by default) give up our directory entry."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            self._thread = None
        if resign:
            self.resign()

    def resign(self) -> None:
        """Give up this node's standing: release the lease (eligible) or deregister
        the presence entry."""
        if self._lease is not None:
            self._lease.resign()
        else:
            try:
                self._rv.deregister(self._instance)
            finally:
                self._registered = False


def runner_from_config(rendezvous: Rendezvous | None = None) -> FederationRunner | None:
    """Build a :class:`FederationRunner` from the environment, or ``None`` when
    federation is not enabled (no valid ``AGENT_DISPATCH_FEDERATION_ROLE``).

    Uses whichever backend ``AGENT_DISPATCH_FEDERATION_BACKEND`` selects
    (:func:`rendezvous_from_config`) unless a rendezvous is passed in directly;
    raises :class:`RuntimeError` if federation is enabled but the selected
    backend's directory isn't reachable/configured, so a misconfiguration fails
    loud rather than silently idling."""
    role = config.federation_role()
    if role is None:
        return None
    instance = config.federation_instance()
    if not instance:
        raise RuntimeError("federation enabled but no instance id could be resolved")
    rv = rendezvous if rendezvous is not None else rendezvous_from_config()
    if rv is None:
        backend = config.federation_backend()
        reason = (
            "no AGENT_DISPATCH_SHARED_URL (hosted coordinator) configured"
            if backend == "gateway"
            else f"backend {backend!r} could not be constructed"
        )
        raise RuntimeError(f"federation enabled but {reason}")
    return FederationRunner(rv, instance, role=role, machine=instance)

