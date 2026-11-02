"""Native owner binding, restricted startup and grant maintenance."""

from __future__ import annotations

import logging
import time

import httpx

from .bridge import BridgeUnavailable
from .action_controls import action_inhibited
from .client import DispatchError
from .queue import SpawnState, Status
from .spawn_factories import _parse_local_body_handle

log = logging.getLogger("agent-dispatch.supervisor")


class RestrictedStartupMixin:
    def _reconcile_restricted_start(self, reservation: dict, task: dict) -> int:
        selected = getattr(self.spawn_fn, "restricted_bridge_for", None)
        if (
            reservation.get("reserved_by") != self.supervisor_id
            or reservation.get("release_requested")
            or not callable(selected) or not selected(task)
        ):
            return 0
        if action_inhibited("worker-admission", logger=log):
            return 0
        prepared = self._prepare_spawn_task(task, reservation)
        ok, handle = self.spawn_fn(prepared)
        if not ok:
            if handle.get("deferred"):
                try:
                    self.client.defer_spawn(
                        reservation["key"], detail=handle.get("error"),
                        expected_generation=handle.get("expected_generation"),
                    )
                except DispatchError:
                    log.exception("could not defer unclaimed restricted startup %s", reservation["key"])
                    return 0
                return 1
            log.error("restricted startup %s retained: %s", reservation["key"], handle.get("error"))
            return 0
        try:
            self.client.record_spawn(
                reservation["key"], session_handle=handle["session"], startup_complete=True,
                startup_revision=reservation.get("startup_revision"),
            )
        except DispatchError:
            log.exception("could not publish retained restricted startup %s", reservation["key"])
            return 0
        return 1


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


def publish_restricted_activity(supervisor, reservation: dict, task: dict) -> None:
    if not supervisor.publish_activity or task.get("status") not in {Status.CLAIMED, Status.STARTED}:
        return
    info = reservation.get("agent_info") or {}
    session_id, native = info.get("session_id"), info.get("native_session_id")
    if (
        not session_id or not native
        or task.get("owner") != info.get("worker_id")
        or task.get("generation") != info.get("task_generation")
        or task.get("owner_session_id") != native
        or _parse_local_body_handle(reservation.get("session_handle")) != session_id
    ):
        return
    try:
        if supervisor.local_acp_session_fn(session_id) != native:
            return
        observed_at = time.time()
        activity = supervisor.local_body_activity_fn(session_id)
        if supervisor.local_acp_session_fn(session_id) != native:
            return
        supervisor.client.set_activity(
            task["id"], activity, reservation_key=reservation["key"], observed_at=observed_at,
            admission_fence={
                key: task.get(key)
                for key in ("generation", "status", "started_at", "owner_session_id")
            },
        )
    except (DispatchError, BridgeUnavailable, OSError, httpx.HTTPError) as exc:
        log.error("restricted activity publication %s refused: %s", reservation["key"], exc)
