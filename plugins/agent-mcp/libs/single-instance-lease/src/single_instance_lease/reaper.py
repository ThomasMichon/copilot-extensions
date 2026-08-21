"""Reconcile-set reaper -- retire a service's strays down to one active daemon.

The cutover orchestrator retires only the **single predecessor** a cutover
replaces. That is not enough to guarantee one daemon per host across *repeated*
cutovers and plain restarts: a passive promoted by cutover N and later displaced
by a process that is not its direct successor (e.g. a plain restart that
re-published the routing table over the top) is never anyone's "old" and leaks.
The supersession self-retire loop handles this from the *inside* (each demoted
daemon exits itself); this reaper is the *outside* backstop -- after a successful
cutover (and on start) the promoted daemon retires every one of the service's
own processes that is not the routing table's ``active`` (nor itself).

**Identity is the caller's responsibility -- and the guard against pid reuse.**
This module never *discovers* processes: killing a pid read from an old record is
unsafe if that pid has been recycled by an unrelated process. The caller passes
only pids it has **positively identified as this service's own** (e.g. drawn from
its own routing-table lineage and, ideally, re-verified against the process image
before terminating). :func:`reconcile_set_reap` then applies the *policy* --
never touch ``active`` or ``self``, terminate the rest -- and is **fail-soft**: a
stray that cannot be reaped is recorded and skipped, never raised, so it can
never fail a cutover.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from .supersession import pid_alive


@dataclass
class ReapResult:
    """Outcome of a :func:`reconcile_set_reap` pass."""

    reaped: list[int] = field(default_factory=list)
    """Pids a terminate call was successfully issued for."""
    skipped: list[int] = field(default_factory=list)
    """Pids left alone (active, self, dead, or failing an identity check)."""
    failed: list[int] = field(default_factory=list)
    """Pids whose terminate call raised (recorded, never re-raised)."""

    @property
    def ok(self) -> bool:
        """True when no terminate attempt raised."""
        return not self.failed


def reconcile_set_reap(
    own_pids: Iterable[int],
    *,
    active_pid: int | None,
    terminate: Callable[[int], None],
    self_pid: int | None = None,
    verify: Callable[[int], bool] | None = None,
    alive: Callable[[int | None], bool] = pid_alive,
) -> ReapResult:
    """Retire every *own* pid that is not ``active`` and not ``self``.

    :param own_pids: pids the caller has positively identified as this service's
        own. Deduped internally; order-insensitive.
    :param active_pid: the pid that must survive -- the daemon the cutover just
        promoted (anchored so the fresh daemon is never reaped). ``None`` means
        "no known active", in which case nothing is spared on that axis.
    :param terminate: called once per pid to retire. Any exception is caught and
        the pid recorded in ``failed`` (fail-soft; a stray never fails a cutover).
    :param self_pid: the caller's own pid to spare (defaults to ``os.getpid()``).
    :param verify: optional positive identity re-check called just before
        terminating; a pid for which it returns falsy is skipped. Use it to
        defeat pid reuse (confirm the live process is still this service).
    :param alive: liveness predicate; a pid that is not alive is skipped (already
        gone -- nothing to do). Injectable for testing.
    :returns: a :class:`ReapResult` tallying reaped / skipped / failed pids.
    """
    if self_pid is None:
        self_pid = os.getpid()
    result = ReapResult()
    seen: set[int] = set()
    for pid in own_pids:
        if not isinstance(pid, int) or pid <= 0 or pid in seen:
            continue
        seen.add(pid)
        if pid == active_pid or pid == self_pid:
            result.skipped.append(pid)
            continue
        if not alive(pid):
            result.skipped.append(pid)
            continue
        if verify is not None and not verify(pid):
            result.skipped.append(pid)
            continue
        try:
            terminate(pid)
        except Exception:  # fail-soft: a stray must never fail a cutover
            result.failed.append(pid)
        else:
            result.reaped.append(pid)
    return result


def superseded_pids_from_table(
    table: dict | None,
    *,
    active_pid: int | None = None,
) -> set[int]:
    """Collect candidate stray pids recorded in a routing-table ``dict``.

    Reads the ``zdd.routing`` table shape and returns the set of pids recorded
    under any entry **other than the live ``active``** -- the ``previous`` slot,
    plus a stale ``active`` whose pid differs from ``active_pid`` when the caller
    knows who is genuinely live. These are *candidates* only: the caller must
    still confirm identity/liveness (see :func:`reconcile_set_reap`'s ``verify``
    and ``alive``) before terminating, because a recorded pid may have been
    recycled since it was written.

    ``active_pid``, when given, is never included in the result.
    """
    candidates: set[int] = set()
    if not isinstance(table, dict):
        return candidates
    for key, raw in table.items():
        if not isinstance(raw, dict):
            continue
        pid = raw.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        if key == "active" and (active_pid is None or pid == active_pid):
            continue
        if pid == active_pid:
            continue
        candidates.add(pid)
    return candidates
