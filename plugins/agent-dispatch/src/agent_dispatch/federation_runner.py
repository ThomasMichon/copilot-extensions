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

The rendezvous the runner drives is built by a factory over the **shared/Gateway**
coordinator URL (:func:`agent_dispatch.config.shared_url`): the facility Gateway is
simply the stable URL the shared coordinator rides, so the "Gateway backend" needs
no new transport code -- it is the :class:`~agent_dispatch.federation.CoordinatorRendezvous`
pointed *through* the Gateway. Phase 4 adds a Dev Tunnels factory as a sibling; the
runner above the factory is unchanged.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING

from . import config
from .federation import CoordinatorRendezvous
from .lease import CoordinatorLease

if TYPE_CHECKING:
    from .federation import Rendezvous


# -- rendezvous factories ----------------------------------------------------


def build_rendezvous(url: str, *, token: str | None = None) -> CoordinatorRendezvous:
    """A coordinator-hosted rendezvous over the coordinator at ``url``."""
    from .client import DispatchClient

    return CoordinatorRendezvous(DispatchClient(url, token=token))


def gateway_rendezvous() -> CoordinatorRendezvous | None:
    """The rendezvous over the **shared/Gateway** coordinator, or ``None`` when no
    ``AGENT_DISPATCH_SHARED_URL`` is configured (federation has no directory to
    reach). The Gateway is just the stable URL this shared coordinator rides."""
    url = config.shared_url()
    if not url:
        return None
    return build_rendezvous(url, token=config.shared_token())


def local_rendezvous(url: str | None = None, *, token: str | None = None) -> CoordinatorRendezvous:
    """The rendezvous over the **local** coordinator (same-host federation / tests)."""
    return build_rendezvous(url or config.client_url(), token=token or config.client_token())


# -- the runner --------------------------------------------------------------


class FederationRunner:
    """Drives federation for one instance against a :class:`Rendezvous` directory.

    Call :meth:`tick` periodically (or :meth:`start` a background loop). Each tick:

    * **lease-eligible** node (role ``coordinator`` / ``standby``) -- advance a
      :class:`~agent_dispatch.lease.CoordinatorLease`; the lease decides whether we
      are the active coordinator or a standby (discovery, not election), so the
      *reported* role is the lease outcome, not the static config hint.
    * **presence-only** node (role ``peer`` / ``satellite``) -- register once, then
      heartbeat; if our entry was TTL-reaped between beats, re-register.

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
        # Presence-only: register once, then heartbeat (re-register if reaped).
        if not self._registered:
            self._register()
        else:
            try:
                self._rv.heartbeat(self._instance, role=self._role)
            except Exception:
                # Entry expired between beats -> re-assert it.
                self._register()
        return {
            "instance": self._instance,
            "role": self._role,
            "epoch": 0,
            "is_active": False,
        }

    def _register(self) -> None:
        self._rv.register(
            self._instance,
            role=self._role,
            machine=self._machine,
            capabilities=self._capabilities,
        )
        self._registered = True

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

    Uses the Gateway rendezvous (:func:`gateway_rendezvous`) unless one is passed
    in; raises :class:`RuntimeError` if federation is enabled but no directory URL
    is reachable, so a misconfiguration fails loud rather than silently idling."""
    role = config.federation_role()
    if role is None:
        return None
    instance = config.federation_instance()
    if not instance:
        raise RuntimeError("federation enabled but no instance id could be resolved")
    rv = rendezvous if rendezvous is not None else gateway_rendezvous()
    if rv is None:
        raise RuntimeError(
            "federation enabled but no AGENT_DISPATCH_SHARED_URL (Gateway) configured"
        )
    return FederationRunner(rv, instance, role=role, machine=instance)
