"""Native owner binding and restricted grant maintenance on the existing loop."""

from __future__ import annotations

import logging

import httpx

from .bridge import BridgeUnavailable
from .client import DispatchError
from .queue import SpawnState, Status
from .spawn_factories import _parse_local_body_handle

log = logging.getLogger("agent-dispatch.supervisor")


def bind_owner_sessions(supervisor) -> int:
    bound = 0
    for res in supervisor._pool_reservations(state=SpawnState.SPAWNED):
        if res.get("driver") == "restricted-bridge":
            continue
        local_sid = _parse_local_body_handle(res.get("session_handle"))
        if local_sid is None:
            continue
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue
        if (
            not supervisor._matches_pool(task)
            or task.get("status") not in {Status.CLAIMED, Status.STARTED}
            or not task.get("owner") or task.get("owner_session_id")
        ):
            continue
        try:
            acp_session_id = supervisor.local_acp_session_fn(local_sid)
        except Exception:
            log.exception("failed to resolve ACP session id for bridge session %s", local_sid)
            continue
        if not acp_session_id:
            continue
        try:
            supervisor.client.bind_owner_session(
                task["id"], task["owner"], acp_session_id,
                expected_generation=task.get("generation"),
            )
            bound += 1
        except DispatchError:
            log.exception("failed to bind headless owner session for task %s", task.get("id"))
    return bound


def renew_restricted_grants(supervisor) -> int:
    renew = getattr(supervisor.spawn_fn, "renew_restricted", None)
    if not callable(renew):
        return 0
    from .activity_snapshots import capture
    from .tracking import LIVE

    renewed = 0
    for reservation, task in capture(
        supervisor.client, supervisor._pool_reservations(state=SpawnState.SPAWNED),
    ):
        if (
            reservation.get("driver") != "restricted-bridge"
            or task.get("status") not in {Status.CLAIMED, Status.STARTED}
        ):
            continue
        info = reservation.get("agent_info") or {}
        session_id = info.get("session_id")
        try:
            if not session_id or supervisor.local_body_verdict_fn(session_id) != LIVE:
                continue
            native = supervisor.local_acp_session_fn(session_id)
            renew(supervisor.client, reservation, task, native, heartbeat=supervisor.heartbeat)
        except (DispatchError, BridgeUnavailable, OSError, httpx.HTTPError) as exc:
            log.error("restricted grant renewal %s refused: %s", reservation["key"], exc)
            continue
        renewed += 1
    return renewed
