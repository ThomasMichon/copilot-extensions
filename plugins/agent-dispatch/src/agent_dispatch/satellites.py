"""In-memory satellite presence registry -- the Gateway-side rendezvous for
one-way (outbound-only) *satellite* machines.

A **satellite** is a field/roaming machine (e.g. a roaming field laptop) that
reaches the facility only *outbound*: it opens no inbound listener -- no SSH
server, no dial-in bridge or coordinator -- so the SSH-mesh federation path
(``ssh <machine> agent-dispatch ...``) can never *target* it. Instead it
participates by an outbound channel it initiates and controls: it **registers**
with, **heartbeats** to, and **pushes its embodiment status** to the
shared/elected coordinator that fronts the facility Gateway, and the facility
addresses it only through the **work it queues for the satellite to pull** --
never by reaching into it (see the ``satellite-agent-exposure`` effort and
``visions/agent-fabric/agent-bridge`` §Features/*satellite agent exposure --
outbound and domain-scoped* + §Behaviors/*satellite-offers-work-not-a-control-
surface*).

This module is deliberately transport-free (a pure library the coordinator wraps
behind HTTP) and **presence-only**: the data is ephemeral and TTL-reaped, so it
need not survive a coordinator restart -- a live satellite simply re-registers on
its next heartbeat. That is why it is an in-memory, lock-guarded store rather than
a row in the durable task DB: a task must outlive a crash; a heartbeat must not.

The registry answers exactly one question -- *"which satellites are live right
now, and what are they working on?"* -- so the fleet's **union view** can show a
satellite's pushed status alongside SSH-reachable peers without ever connecting
to it.
"""

from __future__ import annotations

import copy
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

#: Default presence lifetime: an entry not re-heartbeated within this many
#: seconds is considered gone and reaped. A satellite heartbeats well inside it.
DEFAULT_TTL_SECONDS = 90.0


class UnknownSatellite(KeyError):
    """Raised when a heartbeat/de-register names a satellite that is not
    currently registered (or has expired). The HTTP layer maps this to 404 so
    the satellite client re-registers explicitly rather than silently resurrecting
    a reaped entry."""


@dataclass
class SatelliteEntry:
    """One satellite's live presence record.

    ``status`` is the satellite's **pushed** embodiment/progress payload (opaque
    to the registry) -- typically keyed by worktree handle -- which the union
    view / embodiment overlay reads *in lieu of* an SSH-back resolve that cannot
    reach a no-inbound machine.
    """

    machine: str
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
            "machine": self.machine,
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


def _as_list(v: Sequence[str] | None) -> list[str]:
    return [str(x) for x in v] if v else []


class SatelliteRegistry:
    """Thread-safe, TTL-reaped presence store keyed by ``machine``.

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
        self._entries: dict[str, SatelliteEntry] = {}

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    # -- mutations -------------------------------------------------------------

    def register(
        self,
        machine: str,
        *,
        worktrees: Sequence[str] | None = None,
        capabilities: Sequence[str] | None = None,
        gate_state: str = "open",
        agent_versions: Mapping[str, str] | None = None,
        status: Mapping | None = None,
    ) -> dict:
        """Upsert a satellite's registration and stamp it live.

        Idempotent: a re-register for an existing machine refreshes its fields
        and ``last_seen`` while preserving the original ``registered_at``.
        """
        if not machine:
            raise ValueError("machine is required")
        now = self._clock()
        with self._lock:
            existing = self._entries.get(machine)
            registered_at = existing.registered_at if existing is not None else now
            entry = SatelliteEntry(
                machine=machine,
                worktrees=_as_list(worktrees),
                capabilities=_as_list(capabilities),
                gate_state=str(gate_state),
                agent_versions=dict(agent_versions or {}),
                status=copy.deepcopy(dict(status or {})),
                registered_at=registered_at,
                last_seen=now,
            )
            self._entries[machine] = entry
            return entry.to_dict(now=now, ttl=self._ttl)

    def heartbeat(
        self,
        machine: str,
        *,
        status: Mapping | None = None,
        worktrees: Sequence[str] | None = None,
        gate_state: str | None = None,
    ) -> dict:
        """Refresh a live satellite's ``last_seen`` and optionally its pushed
        ``status`` / ``worktrees`` / ``gate_state``.

        Raises :class:`UnknownSatellite` when the machine is not currently live
        (never registered, or already expired) so the caller re-registers rather
        than resurrecting a reaped entry.
        """
        now = self._clock()
        with self._lock:
            entry = self._entries.get(machine)
            if entry is None or not self._is_live(entry, now):
                # Drop a stale record if present, then signal "re-register".
                self._entries.pop(machine, None)
                raise UnknownSatellite(machine)
            entry.last_seen = now
            if status is not None:
                entry.status = copy.deepcopy(dict(status))
            if worktrees is not None:
                entry.worktrees = _as_list(worktrees)
            if gate_state is not None:
                entry.gate_state = str(gate_state)
            return entry.to_dict(now=now, ttl=self._ttl)

    def deregister(self, machine: str) -> bool:
        """Explicitly remove a satellite (operator sign-out / shutdown /
        gate-close). Returns True if an entry was present, False otherwise."""
        with self._lock:
            return self._entries.pop(machine, None) is not None

    # -- reads (live-only) -----------------------------------------------------

    def get(self, machine: str) -> dict | None:
        now = self._clock()
        with self._lock:
            entry = self._entries.get(machine)
            if entry is None or not self._is_live(entry, now):
                return None
            return entry.to_dict(now=now, ttl=self._ttl)

    def list(self) -> list[dict]:
        now = self._clock()
        with self._lock:
            self._reap(now)
            return [
                e.to_dict(now=now, ttl=self._ttl)
                for e in sorted(self._entries.values(), key=lambda e: e.machine)
            ]

    def is_registered(self, machine: str) -> bool:
        """Whether ``machine`` is a currently-live satellite -- the predicate the
        embodiment overlay uses to choose the pushed-status path over SSH-back."""
        return self.get(machine) is not None

    def reap(self) -> int:
        """Drop all expired entries; return how many were removed."""
        now = self._clock()
        with self._lock:
            return self._reap(now)

    # -- internals -------------------------------------------------------------

    def _is_live(self, entry: SatelliteEntry, now: float) -> bool:
        return (entry.last_seen + self._ttl) > now

    def _reap(self, now: float) -> int:
        dead = [m for m, e in self._entries.items() if not self._is_live(e, now)]
        for m in dead:
            del self._entries[m]
        return len(dead)
