"""Recover aged handleless releases without losing receipt-backed native custody."""

from __future__ import annotations

import logging
import time
from typing import Any

from .client import DispatchError
from .ordinary_native_bindings import RESERVATION_NATIVE_IDENTITY_PROTOCOL
from .queue import SpawnState

log = logging.getLogger("agent-dispatch.supervisor")

#: How long a handle-less RELEASING reservation may sit before this sweep
#: treats it as stranded. Mirrors :data:`agent_dispatch.supervisor
#: ._MIN_RESERVING_TIMEOUT_SECONDS` -- the same conservative bound already
#: trusted for the sibling "stuck mid-launch" gap (#3354), not a new
#: judgment call.
STRANDED_RELEASING_TIMEOUT_SECONDS = 600.0


def qualified_handleless_identity(supervisor: Any, reservation: dict) -> dict | None:
    """Refresh exact identity; legacy missing fields are not proof of absence."""
    key = reservation["key"]
    try:
        current = supervisor.client.get_reservation(key)
    except DispatchError as exc:
        log.warning("release %s identity remains unavailable: %s", key, exc)
        return None
    if (
        current.get("reservation_native_identity_protocol") != RESERVATION_NATIVE_IDENTITY_PROTOCOL
        or "reservation_native_session_id" not in current
        or "reservation_native_identity_error" not in current
        or current["reservation_native_identity_error"]
    ):
        log.warning("release %s lacks qualified native identity evidence", key)
        return None
    native = current["reservation_native_session_id"]
    if native is not None and (not isinstance(native, str) or not native):
        log.warning("release %s has invalid native identity evidence", key)
        return None
    return current


def retire_historical_native(
    supervisor: Any, reservation: dict, task: dict, *, native_session_id: str | None = None,
) -> int:
    """Observe the original native, never conclude its successor's workspace."""
    if (
        reservation.get("startup_complete") is not True
        and reservation.get("startup_fenced") is not True
        or reservation.get("cooling_token")
    ):
        supervisor._retain_terminal_startup(reservation, task, "historical startup or cooling unresolved")
        return 0
    from . import tracking
    from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle
    handle = reservation.get("session_handle")
    if not handle and reservation.get("driver") == "cli":
        handle = native_session_id
    if not isinstance(handle, str) or not handle:
        return 0
    fleet = _parse_fleet_body_handle(handle)
    local = _parse_local_body_handle(handle)
    try:
        if fleet is not None:
            verdict = supervisor.fleet_verdict_fn(*fleet)
        elif local is not None:
            verdict = supervisor.local_body_verdict_fn(local)
        else:
            machine = supervisor._retained_cli_machine(reservation, task)
            worktree = reservation.get("worktree")
            if not machine or not worktree:
                return 0
            verdict = supervisor.verdict_fn(worktree, machine, handle)
        if verdict != tracking.GONE:
            return 0
        supervisor.client.retire_spawn(reservation["key"], exact_absence=True)
    except (DispatchError, OSError) as exc:
        log.warning("historical native retirement %s remains pending: %s", reservation["key"], exc)
        return 0
    return 1


def recover_stranded_releasing_reservations(
    supervisor: Any, *, now: float | None = None
) -> int:
    """Reconcile identity before applying the bounded empty-launch recovery.

    A missing handle can still have a durable acquisition receipt. Such a native
    uses the existing startup fence and exact-absence retirement path; LIVE,
    UNKNOWN and unavailable identity evidence retain custody. Only a genuinely
    empty attempt may use age-based force-fail, whose write transaction checks
    identity again against concurrent publication (copilot-extensions#3179).
    """
    now = time.time() if now is None else now
    recovered = 0
    for res in supervisor._pool_reservations(state=SpawnState.RELEASING):
        key = str(res.get("key") or "")
        if not key:
            continue
        if res.get("session_handle"):
            continue  # a real handle exists -- the liveness-checked path owns this
        reserved_at = res.get("reserved_at")
        updated_at = res.get("updated_at")
        anchor = updated_at if isinstance(updated_at, (int, float)) else reserved_at
        if not isinstance(anchor, (int, float)):
            continue  # no timestamp to bound against -- never guess
        if (now - anchor) < STRANDED_RELEASING_TIMEOUT_SECONDS:
            continue  # still within the ordinary release window
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue  # task vanished; leave the reservation for a human
        if not supervisor._matches_pool(task):
            continue
        current = qualified_handleless_identity(supervisor, res)
        if current is None:
            continue
        if current.get("state") != SpawnState.RELEASING or current.get("session_handle"):
            continue
        native = current["reservation_native_session_id"]
        if native is not None:
            recovered += retire_historical_native(
                supervisor, current, task, native_session_id=native,
            )
            continue
        try:
            supervisor.client.fail_spawn(
                key,
                force=True,
                detail=(
                    "auto-recovered by recover_stranded_releasing_reservations: "
                    f"RELEASING with no session_handle for "
                    f"{now - anchor:.0f}s (>= "
                    f"{STRANDED_RELEASING_TIMEOUT_SECONDS:.0f}s bound) -- "
                    "nothing an exact-absence proof could ever confirm, so "
                    "this would otherwise block its exclusive_key forever "
                    "(copilot-extensions#3179)"
                ),
            )
        except DispatchError:
            log.exception(
                "failed to auto-recover stranded releasing reservation %s", key
            )
            continue
        recovered += 1
        log.warning(
            "auto-recovered stranded releasing reservation %s for task %s "
            "(handle-less, stuck %.0fs -- copilot-extensions#3179)",
            key,
            task.get("id"),
            now - anchor,
        )
    return recovered
