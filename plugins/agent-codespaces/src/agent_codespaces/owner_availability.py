"""Keep the Connection Owner off CodeSpaces that GitHub has stopped.

A held CodeSpace can stop under the Owner (an idle timeout or a maximum
runtime). Every forward into it then dies, and the Owner's reconcile would
rebuild them: a fresh ``gh codespace ssh --config`` (built to wait out a cold
start) and a new ``ssh`` whose ``gh cs ssh --stdio`` transport boots the box
back up. So the Owner both wakes a box nobody asked to restart and spends minutes
on one CodeSpace each cycle.

:class:`AvailabilityGate` answers "which held CodeSpaces are listed, but not
``Available``?" so the Owner tears their forwards down and leaves them alone
until a launcher starts the box again. The listing runs off the event loop, and
an unknown state (a failed listing, or a CodeSpace absent from a partial
per-account one) never gates a box, so a listing problem can only leave the
Owner behaving as it did before this gate existed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Any

log = logging.getLogger("agent-codespaces")

#: How long an all-``Available`` listing is reused. A listing that shows any held
#: CodeSpace down is refreshed on every check instead, so a box a launcher
#: restarts gets its forwards back within one reconcile.
LISTING_TTL_SECONDS = 60.0


#: States that say nothing about the box (GitHub's own ``Unknown``, or a row
#: without one): an indeterminate answer never gates a CodeSpace.
_INDETERMINATE = frozenset({"", "unknown"})


def _is_down(state: str | None) -> bool:
    if state is None:
        return False
    s = state.strip().lower()
    return s not in _INDETERMINATE and s != "available"


class AvailabilityGate:
    """Which held CodeSpaces the latest listing shows are not ``Available``."""

    def __init__(
        self,
        list_codespaces: Callable[[], Iterable[Any]] | None = None,
        *,
        ttl: float = LISTING_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if list_codespaces is None:
            from .lifecycle import list_codespaces as _list

            list_codespaces = _list
        self._list = list_codespaces
        self._ttl = ttl
        self._clock = clock
        self._states: dict[str, str] | None = None
        self._listed_at: float | None = None
        self._gated: set[str] = set()

    def _stale(self, wanted: set[str]) -> bool:
        if self._states is None or self._listed_at is None:
            return True
        if self._clock() - self._listed_at >= self._ttl:
            return True
        return any(_is_down(self._states.get(cs)) for cs in wanted)

    async def stopped(self, codespaces: Iterable[str], *, refresh: bool = False) -> set[str]:
        """The subset of ``codespaces`` that is listed but not ``Available``.

        ``refresh`` re-lists regardless of the cache: the Owner asks for it when
        a held CodeSpace lost a forward, which is when a box has just stopped and
        a rebuild from a cached ``Available`` would boot it back up."""
        wanted = set(codespaces)
        if not wanted:
            return set()
        if refresh or self._stale(wanted):
            try:
                rows = await asyncio.to_thread(lambda: list(self._list()))
            except Exception as exc:
                log.debug("Connection Owner: listing CodeSpaces failed: %s", exc)
                return set()
            self._states = {str(cs.name): str(getattr(cs, "state", None) or "") for cs in rows}
            self._listed_at = self._clock()
        states = self._states or {}
        down = {cs for cs in wanted if _is_down(states.get(cs))}
        for cs in sorted(down - self._gated):
            log.info(
                "Connection Owner: %s is %s; holding its forwards down until it is"
                " Available again (the Owner never wakes a stopped CodeSpace)",
                cs, states[cs],
            )
        for cs in sorted((self._gated & wanted) - down):
            log.info("Connection Owner: %s is Available again; restoring its forwards", cs)
        self._gated = down
        return down
