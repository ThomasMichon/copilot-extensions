"""Keep a resumed session's CLI-mode claim -- and its venue -- across a restart.

A launch that resumes a named conversation (``--resume=<id>``) can first
register under a placeholder id, which claims the launch's reservation. When
the resume stays in the same process, the bridge folds the placeholder into
the resumed id itself. When the Copilot CLI restarts its extension host to load
the resumed conversation, the placeholder's process exits (deregistering it)
and the resumed id registers from a new process: the bridge cannot prove the
two are one session, so the reservation stays claimed by a row that no longer
exists, and the resumed session runs without its CLI mode, venue or supervisor
(a Harness Board then can't tell which worktree it works for).

The launcher can prove it -- it named the id -- so once the resumed session is
live and the placeholder is gone, it reserves again with the same venue, and
the resumed session claims that on its next heartbeat.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

_RESUME_FLAGS = ("--resume", "-r")
_POLL_SECONDS = 3.0


def resume_target(copilot_args: list[str]) -> str | None:
    """The conversation id these Copilot args explicitly resume, else ``None``
    (``--continue`` or a bare ``--resume`` name none)."""
    for i, arg in enumerate(copilot_args):
        flag, eq, value = arg.partition("=")
        if flag not in _RESUME_FLAGS:
            continue
        if not eq:
            value = copilot_args[i + 1] if i + 1 < len(copilot_args) else ""
            if value.startswith("-"):
                value = ""
        return value.strip() or None
    return None


def settle_resumed_claim(
    worktree_id: str,
    reservation: dict[str, Any],
    venue: dict[str, Any] | None,
    *,
    expected: str | None,
    claimed: str,
    timeout: float,
    ttl_seconds: float,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> tuple[str, dict[str, Any]]:
    """``(session_id, reservation)`` once a resume of *expected* settles.

    Unchanged when nothing was resumed by id, or *expected* claimed directly.
    When the bridge folds the placeholder *claimed* into *expected*, that is
    the session. When *expected* is live and *claimed* is gone, the
    reservation is renewed with *venue* so *expected* claims it; the returned
    reservation is the one the caller must release -- ``{}`` when a renewal's
    claim isn't confirmed yet, so the caller leaves it for the resumed
    session's next heartbeat (it expires on its own TTL). A failed bridge read
    only means "not known yet". Bounded by *timeout*: otherwise (a resume that
    started a new conversation instead) the placeholder is the session, as
    before."""
    if not expected or claimed == expected:
        return claimed, reservation
    import venue_copilot as vc  # late: callers' test seams patch the package

    deadline = clock() + timeout
    unknown = (vc.VenueCopilotError, OSError)  # a bridge call that failed, or couldn't even start
    while True:
        try:
            row = vc.get_cli_mode_reservation(worktree_id) or {}
        except unknown:
            row = None
        if (row is not None and row.get("reservation_id") == reservation.get("reservation_id")
                and row.get("claimed_by_session_id") == expected):
            return expected, reservation  # the bridge folded the placeholder in
        resumed, placeholder = vc.live_session_for(expected), vc.live_session_for(claimed)
        if (row is not None and resumed.get("session_id") == expected
                and resumed.get("status", "live") == "live"
                # Gone, or a dead row (an unclean exit leaves it to expire).
                and (not placeholder or placeholder.get("status") in ("expired", "taken-over"))):
            try:
                vc.release_cli_mode(worktree_id, reservation_id=reservation.get("reservation_id"))
                renewed = vc.reserve_cli_mode(worktree_id, ttl_seconds=ttl_seconds, venue=venue)
                # At least one heartbeat: the renewal can only be claimed by the next one.
                claimant = vc.await_claim(worktree_id, str(renewed.get("reservation_id") or ""),
                                          max(deadline - clock(), 2 * _POLL_SECONDS + 30.0))
            except unknown:
                return expected, {}  # live, maybe without CLI mode; nothing for the caller to release
            return expected, (renewed if claimant == expected else {})
        if clock() >= deadline:
            return claimed, reservation
        sleep(_POLL_SECONDS)
