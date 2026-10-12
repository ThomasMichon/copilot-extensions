"""In-memory **fleet directory** -- the relay-rendezvous awareness plane for
agent-dispatch federation.

The directory is the *awareness plane*: every agent-dispatch instance that
federates **registers** here, **heartbeats** to keep its entry live, and can
**enumerate** the fleet -- so "who is live, in what role, and what are they
working on" is answerable from any seat without an N x N peer mesh. A
**coordinator** (the claim plane's single writer) advertises itself here too, so
peers **discover** it rather than electing one (see the
``agent-dispatch-federation`` effort and ``visions/agent-fabric/agent-dispatch``
§Concepts/*instance discovery & federation* + §Behaviors/*peers-discover-and-
federate* + *coordinator-is-discovered-not-elected*).

Each entry carries a **role** -- ``peer`` (an ordinary federating instance),
``coordinator`` / ``standby`` (the claim-plane writer and its failover), or
``satellite`` (the most-constrained leaf: a one-way, outbound-only field machine
that opens no inbound listener and is reached only through work it pulls; see the
``satellite-agent-exposure`` effort). Satellites are therefore just directory
entries with ``role="satellite"``; the shipped ``/satellites`` endpoints are a
thin façade over this same store.

An entry also carries an **epoch** (monotonic, per-instance) -- *carried* by the
directory now and *enforced* by the Phase-2 fenced-coordinator lease (a standby
takes over on staleness with the next epoch, which fences the deposed writer).

This module is deliberately transport-free (a pure library the coordinator wraps
behind HTTP) and **presence-only**: the data is ephemeral and TTL-reaped, so it
need not survive a coordinator restart -- a live instance simply re-registers on
its next heartbeat. That is why it is an in-memory, lock-guarded store rather than
a row in the durable task DB: a task must outlive a crash; a heartbeat must not.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

#: Default presence lifetime: an entry not re-heartbeated within this many
#: seconds is considered gone and reaped. An instance heartbeats well inside it.
DEFAULT_TTL_SECONDS = 90.0

#: The directory roles. ``peer`` is the default ordinary federating instance;
#: ``coordinator``/``standby`` are the claim-plane writer and its failover;
#: ``satellite`` is the constrained outbound-only leaf.
ROLE_PEER = "peer"
ROLE_COORDINATOR = "coordinator"
ROLE_STANDBY = "standby"
ROLE_SATELLITE = "satellite"


class UnknownInstance(KeyError):
    """Raised when a heartbeat/de-register names an instance that is not
    currently registered (or has expired). The HTTP layer maps this to 404 so
    the client re-registers explicitly rather than silently resurrecting a
    reaped entry."""


#: Back-compat alias: the satellite endpoints historically raised this name.
UnknownSatellite = UnknownInstance


@dataclass
class DirectoryEntry:
    """One federating instance's live presence record.

    Keyed by ``instance`` -- a stable id, the machine (``machine``) for a
    single-coordinator host, or ``machine/worktree`` where several coordinators
    share a host. ``role`` places it on the awareness/claim planes;
    ``epoch`` is the monotonic fencing token (carried now, enforced in Phase 2).

    ``status`` is the instance's **pushed** embodiment/progress payload (opaque
    to the directory) -- typically keyed by worktree handle -- which the union
    view / embodiment overlay reads *in lieu of* an SSH-back resolve that cannot
    reach a no-inbound (satellite) machine.
    """

    instance: str
    role: str = ROLE_PEER
    epoch: int = 0
    machine: str = ""
    worktrees: list[str] = field(default_factory=list)
    capabilities: list[str] = field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = field(default_factory=dict)
    status: dict = field(default_factory=dict)
    registered_at: float = 0.0
    last_seen: float = 0.0

    def to_dict(self, *, now: float, ttl: float) -> dict:
        expires_at = self.last_seen + ttl
        return {
            "instance": self.instance,
            "role": self.role,
            "epoch": self.epoch,
            # ``machine`` defaults to the instance id (satellites register by
            # machine); kept as a distinct field for hosts that key by
            # ``machine/worktree``.
            "machine": self.machine or self.instance,
            "worktrees": list(self.worktrees),
            "capabilities": list(self.capabilities),
            "gate_state": self.gate_state,
            "agent_versions": dict(self.agent_versions),
            "status": copy.deepcopy(self.status),
            "registered_at": self.registered_at,
            "last_seen": self.last_seen,
            "expires_at": expires_at,
            "age": max(0.0, now - self.last_seen),
        }


#: Back-compat alias for the pre-generalization dataclass name.
SatelliteEntry = DirectoryEntry



def _as_list(v: Sequence[str] | None) -> list[str]:
    return [str(x) for x in v] if v else []


class FleetDirectory:
    """Thread-safe, TTL-reaped presence store keyed by ``instance`` id.

    All reads return only **live** entries (``last_seen + ttl > now``); expired
    entries are lazily reaped on access. A clock injection point (``clock``)
    makes TTL behavior deterministically testable.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, DirectoryEntry] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    # -- mutations -------------------------------------------------------------

    def register(
        self,
        instance: str,
        *,
        role: str = ROLE_PEER,
        epoch: int = 0,
        machine: str | None = None,
        worktrees: Sequence[str] | None = None,
        capabilities: Sequence[str] | None = None,
        gate_state: str = "open",
        agent_versions: Mapping[str, str] | None = None,
        status: Mapping | None = None,
    ) -> dict:
        """Upsert an instance's registration and stamp it live.

        Idempotent: a re-register for an existing instance refreshes its fields
        and ``last_seen`` while preserving the original ``registered_at``.
        """
        if not instance:
            raise ValueError("instance is required")
        now = self._clock()
        with self._lock:
            existing = self._entries.get(instance)
            registered_at = existing.registered_at if existing is not None else now
            entry = DirectoryEntry(
                instance=instance,
                role=str(role),
                epoch=int(epoch),
                machine=str(machine) if machine else instance,
                worktrees=_as_list(worktrees),
                capabilities=_as_list(capabilities),
                gate_state=str(gate_state),
                agent_versions=dict(agent_versions or {}),
                status=copy.deepcopy(dict(status or {})),
                registered_at=registered_at,
                last_seen=now,
            )
            self._entries[instance] = entry
            return entry.to_dict(now=now, ttl=self._ttl)

    def heartbeat(
        self,
        instance: str,
        *,
        status: Mapping | None = None,
        worktrees: Sequence[str] | None = None,
        gate_state: str | None = None,
        role: str | None = None,
        epoch: int | None = None,
    ) -> dict:
        """Refresh a live instance's ``last_seen`` and optionally its pushed
        ``status`` / ``worktrees`` / ``gate_state`` / ``role`` / ``epoch``.

        Raises :class:`UnknownInstance` when the instance is not currently live
        (never registered, or already expired) so the caller re-registers rather
        than resurrecting a reaped entry.
        """
        now = self._clock()
        with self._lock:
            entry = self._entries.get(instance)
            if entry is None or not self._is_live(entry, now):
                # Drop a stale record if present, then signal "re-register".
                self._entries.pop(instance, None)
                raise UnknownInstance(instance)
            entry.last_seen = now
            if status is not None:
                entry.status = copy.deepcopy(dict(status))
            if worktrees is not None:
                entry.worktrees = _as_list(worktrees)
            if gate_state is not None:
                entry.gate_state = str(gate_state)
            if role is not None:
                entry.role = str(role)
            if epoch is not None:
                entry.epoch = int(epoch)
            return entry.to_dict(now=now, ttl=self._ttl)

    def deregister(self, instance: str) -> bool:
        """Explicitly remove an instance (operator sign-out / shutdown /
        gate-close). Returns True if an entry was present, False otherwise."""
        with self._lock:
            return self._entries.pop(instance, None) is not None

    # -- reads (live-only) -----------------------------------------------------

    def get(self, instance: str) -> dict | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(instance)
            if entry is None or not self._is_live(entry, now):
                return None
            return entry.to_dict(now=now, ttl=self._ttl)

    def list(self, *, role: str | None = None) -> list[dict]:
        """All live entries (optionally filtered by ``role``), sorted by id."""
        now = self._clock()
        with self._lock:
            self._reap(now)
            entries = sorted(self._entries.values(), key=lambda e: e.instance)
            return [
                e.to_dict(now=now, ttl=self._ttl)
                for e in entries
                if role is None or e.role == role
            ]

    def discover_peers(self, *, role: str | None = None) -> list[dict]:
        """The awareness-plane read: every live instance (optional ``role``
        filter). Alias of :meth:`list` in the fleet-directory vocabulary."""
        return self.list(role=role)

    def discover_coordinator(self) -> dict | None:
        """The claim-plane read: the live ``coordinator`` entry with the highest
        ``epoch`` (fencing token), or ``None`` if no coordinator is live.

        Discovery-not-election: peers pull work through whichever coordinator the
        directory advertises at the highest epoch; Phase 2 wires the fenced
        failover that increments that epoch on a standby takeover.
        """
        now = self._clock()
        with self._lock:
            self._reap(now)
            coordinators = [
                e for e in self._entries.values() if e.role == ROLE_COORDINATOR
            ]
            if not coordinators:
                return None
            best = max(coordinators, key=lambda e: (e.epoch, e.instance))
            return best.to_dict(now=now, ttl=self._ttl)

    def is_registered(self, instance: str) -> bool:
        """Whether ``instance`` is currently live -- the predicate the embodiment
        overlay uses to choose the pushed-status path over an SSH-back resolve."""
        return self.get(instance) is not None

    def reap(self) -> int:
        """Drop all expired entries; return how many were removed."""
        now = self._clock()
        with self._lock:
            return self._reap(now)

    # -- internals -------------------------------------------------------------

    def _is_live(self, entry: DirectoryEntry, now: float) -> bool:
        return (entry.last_seen + self._ttl) > now

    def _reap(self, now: float) -> int:
        dead = [i for i, e in self._entries.items() if not self._is_live(e, now)]
        for i in dead:
            del self._entries[i]
        return len(dead)


#: Back-compat alias for the pre-generalization class name. The satellite
#: endpoints construct this; a satellite is a directory entry with
#: ``role="satellite"``.
SatelliteRegistry = FleetDirectory

