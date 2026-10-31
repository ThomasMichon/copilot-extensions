"""Select existing-owner wake delivery from retained execution facts."""

from __future__ import annotations

from . import bridge
from .bridge_command_delivery import native_prompt, steering_prompt
from .ordinary_agent_facts import identity, native_conflict
from .ordinary_native_bindings import native_bindings
from .queue import TaskQueue
from .queue_records import SpawnReservation, SpawnState, Status, TaskError
from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle


def _require_retained_owner(task, reservation: SpawnReservation, owner, owner_session_id):
    if (
        task is None or task.owner != owner or task.owner_session_id != owner_session_id
        or task.status not in Status.OWNED or not owner_session_id
        or reservation.state not in {SpawnState.SPAWNED, SpawnState.COLD}
        or reservation.conversation_retired or reservation.release_requested
        or reservation.cooling_token is not None
        or reservation.exclusive_key is not None and reservation.exclusive_released
        or reservation.conclusion_state == "pending"
        or not isinstance(reservation.agent_info, dict)
    ):
        raise TaskError("hosted wake owner or retained execution facts are unavailable")


def _target_identity(reservation: SpawnReservation, task, owner: str) -> tuple[str, str]:
    if reservation.driver == "restricted-bridge":
        from .restricted_delivery import wake_identity
        return wake_identity(reservation, task)
    actual = identity(reservation.agent_info)
    if (
        actual.worktree != reservation.worktree
        or actual.project != reservation.allocation_project
        or "/" in owner and owner.partition("/")[2] != actual.worktree
    ):
        raise TaskError("hosted wake does not match captured native/worktree identity")
    return actual.bridge, actual.native


def deliver_owner_wake(
    queue: TaskQueue, owner: str, owner_session_id: str | None, task_id: str,
    message: str | None, idempotency_key: str,
) -> bool:
    reservation = queue.latest_reservation(task_id)
    handle = reservation.session_handle if reservation else None
    hosted = reservation is not None and (
        reservation.driver in {"ordinary-bridge", "restricted-bridge"}
        or reservation.agent_info is not None
        or bool(handle and handle.startswith(("local-body:", "fleet-body:")))
    )
    if not hosted:
        return bridge.resume_steered_owner(
            owner, task_id, message, owner_session_id=owner_session_id,
            idempotency_key=idempotency_key,
        )
    task = queue.get(task_id)
    _require_retained_owner(task, reservation, owner, owner_session_id)
    bridge_id, native_id = _target_identity(reservation, task, owner)
    fleet = _parse_fleet_body_handle(handle)
    local = _parse_local_body_handle(handle)
    machine, separator, worktree = owner.partition("/")
    if (
        native_id != owner_session_id
        or (separator and (not machine or not worktree))
        or (fleet is None and local is None)
        or bridge_id != (fleet[1] if fleet else local)
    ):
        raise TaskError("hosted wake does not match captured native/worktree identity")
    with queue._connect() as conn:
        row = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key = ?", (reservation.key,),
        ).fetchone()
        if row is None:
            raise TaskError("hosted wake reservation is unavailable")
        current = SpawnReservation._from_row(row)
        current_task = queue._fetch(conn, task_id)
        _require_retained_owner(current_task, current, owner, owner_session_id)
        if current_task.generation != task.generation or any(
            getattr(current, field) != getattr(reservation, field)
            for field in ("session_handle", "driver", "worktree", "allocation_project", "creating_host")
        ):
            raise TaskError("hosted wake retained target changed before transport")
        if _target_identity(current, current_task, owner) != (bridge_id, native_id):
            raise TaskError("hosted wake retained native identity changed before transport")
        known, retired = native_bindings(conn, row, current_task)
    if native_conflict(native_id, known, retired):
        raise TaskError("hosted wake conflicts with known or retired native identity")
    host = fleet[0] if fleet else reservation.creating_host
    if separator:
        from .bridge_remote import normalize_host
        if host is not None and normalize_host(machine) != normalize_host(host):
            raise TaskError("hosted wake owner machine differs from retained execution host")
        host = machine
    return native_prompt(
        bridge_id, native_id, steering_prompt(task_id, message),
        idempotency_key=idempotency_key,
        machine=host, launch_prefix=bridge._agent_bridge_launch_prefix,
        run_ssh=bridge.run_ssh_command,
    )
