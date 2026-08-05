"""The federation **rendezvous** interface -- the pluggable seam agent-dispatch
federation uses to *find peers and the coordinator*, independent of the transport
that carries it.

.. note::
   Distinct from :mod:`agent_dispatch.rendezvous`, which is the *endpoint*
   (port-mapping) rendezvous a service uses to advertise its own loopback
   address. This module is the *federation directory* seam: how instances find
   **each other**.

A *rendezvous* here is a **directory** an instance registers with and discovers
the others through. Federation splits into two planes over it:

* **awareness** -- ``register`` / ``heartbeat`` / ``deregister`` + ``discover_peers``:
  who is live, in what role, doing what.
* **claim** -- ``discover_coordinator``: the single writer peers pull work
  through, *discovered* (highest live epoch) rather than elected.

The point of the interface is **substrate-independence**: the same federation
logic runs whether the directory is served by the facility **bespoke Gateway**,
a single-user **Dev Tunnels** management directory, or -- the first
implementation here -- a coordinator's own in-process
:class:`~agent_dispatch.satellites.FleetDirectory` reached over HTTP. Phases 3-4
add the Gateway and Dev Tunnels backends as sibling implementations of this same
Protocol; nothing above the interface changes when the backend swaps (see the
``agent-dispatch-federation`` effort and
``visions/agent-fabric/agent-dispatch`` §Behaviors/*peers-discover-and-federate*
+ *coordinator-is-discovered-not-elected*).

This module deliberately holds **no failover logic** -- a standby taking over an
epoch is the Phase-2 fenced-coordinator lease, layered *on top of* this
interface, not baked into it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from .client import DispatchClient


@runtime_checkable
class Rendezvous(Protocol):
    """The pluggable federation directory seam.

    Every backend (coordinator-hosted, Gateway, Dev Tunnels) implements these
    five operations; callers depend only on this Protocol.
    """

    def register(
        self,
        instance: str,
        *,
        role: str = ...,
        epoch: int = ...,
        machine: str | None = ...,
        worktrees: list[str] | None = ...,
        capabilities: list[str] | None = ...,
        gate_state: str = ...,
        agent_versions: dict[str, str] | None = ...,
        status: dict | None = ...,
    ) -> dict:
        """Register (or refresh) ``instance``; returns the stored entry."""
        ...

    def heartbeat(
        self,
        instance: str,
        *,
        status: dict | None = ...,
        worktrees: list[str] | None = ...,
        gate_state: str | None = ...,
        role: str | None = ...,
        epoch: int | None = ...,
    ) -> dict:
        """Keep ``instance`` live (+ optionally update pushed fields)."""
        ...

    def deregister(self, instance: str) -> bool:
        """Remove ``instance``; returns whether an entry was present."""
        ...

    def discover_peers(self, *, role: str | None = ...) -> list[dict]:
        """All live instances (optional ``role`` filter) -- the awareness read."""
        ...

    def discover_coordinator(self) -> dict | None:
        """The live coordinator with the highest epoch, or ``None`` -- the claim
        read."""
        ...


class CoordinatorRendezvous:
    """The **coordinator-hosted / local** rendezvous backend.

    Wraps a :class:`~agent_dispatch.client.DispatchClient` pointed at a
    coordinator (the local loopback one for same-machine federation, or the
    shared/Gateway one) and speaks to its ``/directory`` endpoints. This is the
    default backend and the reference for the Gateway / Dev Tunnels backends that
    follow: they implement the same :class:`Rendezvous` Protocol over their own
    transports.

    ``deregister`` normalizes the coordinator's ``{"deregistered": bool}`` reply
    to a plain ``bool`` so it matches the Protocol; every other call passes
    through to the identically-named directory client method.
    """

    def __init__(self, client: DispatchClient) -> None:
        self._client = client

    def register(
        self,
        instance: str,
        *,
        role: str = "peer",
        epoch: int = 0,
        machine: str | None = None,
        worktrees: list[str] | None = None,
        capabilities: list[str] | None = None,
        gate_state: str = "open",
        agent_versions: dict[str, str] | None = None,
        status: dict | None = None,
    ) -> dict:
        return self._client.directory_register(
            instance,
            role=role,
            epoch=epoch,
            machine=machine,
            worktrees=worktrees,
            capabilities=capabilities,
            gate_state=gate_state,
            agent_versions=agent_versions,
            status=status,
        )

    def heartbeat(
        self,
        instance: str,
        *,
        status: dict | None = None,
        worktrees: list[str] | None = None,
        gate_state: str | None = None,
        role: str | None = None,
        epoch: int | None = None,
    ) -> dict:
        return self._client.directory_heartbeat(
            instance,
            status=status,
            worktrees=worktrees,
            gate_state=gate_state,
            role=role,
            epoch=epoch,
        )

    def deregister(self, instance: str) -> bool:
        result = self._client.directory_deregister(instance)
        return bool(result.get("deregistered", False))

    def discover_peers(self, *, role: str | None = None) -> list[dict]:
        return self._client.directory_list(role=role)

    def discover_coordinator(self) -> dict | None:
        return self._client.directory_coordinator()
