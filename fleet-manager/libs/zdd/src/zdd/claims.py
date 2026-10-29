"""Generation-scoped ownership claims for children a daemon hands off across
an update (effort ``agent-bridge-unified-zdd-cutover``, Phase 2).

The existing cutover (:mod:`zdd.cutover`) only drains the daemon's own HTTP
endpoint -- it has no protocol for handing off long-lived children the daemon
owns (agent-bridge's per-session ``session-host`` subprocesses are the
motivating case, but any of :mod:`zdd`'s 8 consumers that supervise
long-lived children across an update has the same shape of problem). A
session survives a daemon restart today only by accident of timing, not by
design.

This module is deliberately **storage-agnostic**: it does not define its own
durable manifest, because a durable manifest for the children in question
usually already exists (agent-bridge's own
``agent_bridge.session_host.host_index.HostIndex`` is exactly that). Instead
it defines the **decision logic** -- given a record's current claim fields,
should a requesting generation be allowed to take ownership -- as pure
functions over a small protocol, so any consumer's own record type can adopt
generation-scoped claims by adding two fields (an owning generation id and
the pid to check for liveness) and delegating the decision here.

Concepts:

* **generation** -- an opaque, caller-chosen id naming one daemon instance
  across its lifetime (a version string, a pid, and a start timestamp joined
  together is a reasonable default -- see :func:`generation_id`). Two
  processes must never share a generation id.
* **claim** -- a record's ``(generation, owner_pid)`` pair. The record is
  "claimed" by whichever generation last acquired it; ``owner_pid`` is the
  pid to probe for that generation's liveness (typically the daemon
  process's own pid, not a child's).
* **recoverable** -- a claim whose owning generation is *provably dead* (its
  ``owner_pid`` is no longer alive). A recoverable claim needs no live
  handshake with the dead process; whichever generation next queries it may
  simply take it over.

Nothing here talks to a real process table or does I/O -- ``pid_alive`` and
persistence are always injected by the caller, so this module stays pure and
trivially testable, matching :mod:`zdd`'s "no runtime dependencies beyond
stdlib" contract.
"""

from __future__ import annotations

import os
import time
from typing import Callable, Protocol, runtime_checkable


@runtime_checkable
class Claimable(Protocol):
    """The two fields a record needs for :mod:`zdd.claims` to reason about it."""

    owner_generation: str
    owner_pid: int


class ClaimConflict(Exception):
    """Raised when a live generation already holds a claim a caller wants.

    Not raised for a stale (recoverable) claim -- that path succeeds and
    hands the claim to the requesting generation instead.
    """

    def __init__(self, key: str, held_by_generation: str, held_by_pid: int) -> None:
        self.key = key
        self.held_by_generation = held_by_generation
        self.held_by_pid = held_by_pid
        super().__init__(
            f"claim {key!r} is held by live generation {held_by_generation!r} "
            f"(pid {held_by_pid})"
        )


def generation_id(
    *, version: str = "", pid: int | None = None, started_at: float | None = None
) -> str:
    """A reasonable default opaque generation id: ``version-pid-started_at``.

    Callers are free to mint their own id (any string works, as long as one
    daemon instance never reuses another's), but this covers the common case
    without every consumer reinventing the format.
    """
    pid = pid if pid is not None else os.getpid()
    started_at = started_at if started_at is not None else time.time()
    ver = version or "unversioned"
    return f"{ver}-{pid}-{started_at:.6f}"


def is_recoverable(
    record: Claimable | None, *, pid_alive: Callable[[int], bool]
) -> bool:
    """True when ``record``'s owning generation is provably dead.

    ``None`` (never claimed) and an empty/zero owner are both trivially
    "recoverable" -- there is nothing live to conflict with.
    """
    if record is None:
        return True
    if not record.owner_generation or not record.owner_pid:
        return True
    return not pid_alive(record.owner_pid)


def decide_acquire(
    record: Claimable | None,
    *,
    key: str,
    generation: str,
    owner_pid: int,
    pid_alive: Callable[[int], bool],
    force: bool = False,
) -> None:
    """Decide whether ``generation`` may acquire ``key``. Raises on conflict.

    Returns ``None`` (a decision, not a mutated record) so the caller applies
    it to its own storage however it already persists records -- this keeps
    :mod:`zdd.claims` free of any particular manifest's schema.

    * ``record is None`` -- free; acquire.
    * ``record.owner_generation == generation`` -- idempotent re-claim by the
      same generation (e.g. a retried acquire); always allowed.
    * the current owner is alive and is a *different* generation -- raises
      :class:`ClaimConflict` unless ``force=True``.
    * the current owner is dead (or ``force=True``) -- the claim is stale;
      acquire (this is the "recover" half of claim/release/recover -- no live
      handshake with the dead generation is needed or attempted).
    """
    if record is None:
        return
    if record.owner_generation == generation:
        return
    if not force and not is_recoverable(record, pid_alive=pid_alive):
        raise ClaimConflict(key, record.owner_generation, record.owner_pid)
