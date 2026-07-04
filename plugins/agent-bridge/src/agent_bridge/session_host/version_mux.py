"""Phase 4 -- the narrow host version-mux.

The Session Host speaks **1:1 ACP** and is deliberately a stable kernel, so a
host-*layer* wire change is rare (see the effort ``agent-bridge-version-mux``).
But when it does happen -- a **breaking envelope bump**
(:data:`protocol.PROTOCOL_VERSION`) -- a freshly deployed frontend cannot drive a
still-running old-version host with its new client code. You also cannot hand a
live pipe-connected child to a new host. This module holds the policy that lets
both generations coexist safely without violating goal 1 (never reap a session
mid-turn):

* **New sessions** always launch a host from the *current* install, so they
  speak the current protocol -- no routing needed (handled by
  ``launch_session_host``).
* **An existing session** is reattached **only if** its host's protocol version
  is one this frontend still speaks (:func:`is_compatible`).
* **An incompatible ("stranded") host** whose child is still alive is **left
  running** so it keeps its child until the child reaches its own stop -- never
  killed mid-turn.
* **Sprawl is bounded.** A stranded host whose child has *already stopped* has
  nothing left to protect, so it is reaped immediately (freeing the old on-disk
  install it pins). A last-resort **age bound** can force-reap a stranded host
  that never idles (a PR watcher, an oracle) so it cannot pin an old version
  forever; that bound is opt-in (Phase-4 follow-up wires the config).

The routing decision is a **pure function** (:func:`plan_host`) so it is fully
unit-testable in isolation from the async reattach machinery.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .protocol import PROTOCOL_VERSION

# The wire-envelope versions this frontend build can drive. Today it speaks
# exactly the current version; if a backward-compatible envelope shim is ever
# added, older versions can be listed here too (and then they stop being
# "incompatible" and reattach normally).
SUPPORTED_PROTOCOL_VERSIONS: frozenset[int] = frozenset({PROTOCOL_VERSION})


def is_compatible(protocol_version: int) -> bool:
    """True if this frontend can speak a host advertising ``protocol_version``."""
    return protocol_version in SUPPORTED_PROTOCOL_VERSIONS


class HostDisposition(enum.Enum):
    """What the frontend should do with one surviving host on reattach/sweep."""

    REATTACH = "reattach"      # compatible wire -> drive it (drains a dead child too)
    STRAND = "strand"          # incompatible + child alive -> leave running (goal 1)
    REAP_STOPPED = "reap_stopped"    # incompatible + child already stopped -> reap
    FORCE_REAP = "force_reap"  # incompatible + child alive + past age bound -> reap


@dataclass(frozen=True)
class HostPlan:
    disposition: HostDisposition
    reason: str


def plan_host(
    *,
    protocol_version: int,
    child_alive: bool,
    age_seconds: float | None = None,
    stale_reap_seconds: float | None = None,
) -> HostPlan:
    """Decide the disposition of one surviving Session Host.

    Assumes the **host process itself is alive** (dead hosts are pruned by
    ``HostIndex.prune_dead`` before planning). Pure and deterministic.

    * A **compatible** host is always reattached: this frontend speaks its wire,
      so it can drive an active child *and* drain a child that has already exited
      (the reattach receives the buffered tail + ``LIVENESS(dead)``).
    * An **incompatible** host cannot be driven by this frontend at all:
      - if its child is **already stopped**, nothing is at risk -- reap it so it
        stops pinning its old on-disk install;
      - else if a positive ``stale_reap_seconds`` bound is set and the host has
        outlived it, **force-reap** (the sprawl escape valve -- accepting that a
        truly immortal session is bounded);
      - otherwise **strand** it: leave it running so its child reaches its own
        stop (goal 1: never reap mid-turn).
    """
    if is_compatible(protocol_version):
        return HostPlan(HostDisposition.REATTACH, "compatible protocol")
    if not child_alive:
        return HostPlan(HostDisposition.REAP_STOPPED,
                        "incompatible host, child already stopped")
    if (stale_reap_seconds is not None and stale_reap_seconds > 0
            and age_seconds is not None and age_seconds >= stale_reap_seconds):
        return HostPlan(
            HostDisposition.FORCE_REAP,
            f"incompatible host exceeded sprawl bound "
            f"({age_seconds:.0f}s >= {stale_reap_seconds:.0f}s)",
        )
    return HostPlan(HostDisposition.STRAND,
                    "incompatible host, child alive -- keep until its own stop")
