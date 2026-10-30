"""Select existing-owner wake delivery from retained execution facts."""

from __future__ import annotations

from dataclasses import asdict

from . import bridge
from .bridge_command_delivery import native_prompt, steering_prompt
from .ordinary_agent_facts import identity, native_conflict
from .ordinary_native_bindings import native_bindings
from .queue import TaskQueue
from .queue_records import SpawnState, Status, TaskError
from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle


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
    if (
        task is None or task.owner != owner or task.owner_session_id != owner_session_id
        or task.status not in Status.OWNED or not owner_session_id
        or reservation.state not in {SpawnState.SPAWNED, SpawnState.COLD}
        or reservation.conversation_retired or reservation.release_requested
        or reservation.exclusive_key is not None and reservation.exclusive_released
        or reservation.conclusion_state == "pending"
        or not isinstance(reservation.agent_info, dict)
    ):
        raise TaskError("hosted wake owner or retained execution facts are unavailable")
    actual = identity(reservation.agent_info)
    fleet = _parse_fleet_body_handle(handle)
    local = _parse_local_body_handle(handle)
    machine, separator, worktree = owner.partition("/")
    if (
        actual.native != owner_session_id or actual.worktree != reservation.worktree
        or actual.project != reservation.allocation_project
        or (separator and (not machine or worktree != actual.worktree))
        or (fleet is None and local is None)
        or actual.bridge != (fleet[1] if fleet else local)
    ):
        raise TaskError("hosted wake does not match captured native/worktree identity")
    with queue._connect() as conn:
        known, retired = native_bindings(conn, asdict(reservation), task)
    if native_conflict(actual.native, known, retired):
        raise TaskError("hosted wake conflicts with known or retired native identity")
    host = fleet[0] if fleet else reservation.creating_host
    if separator:
        from .bridge_remote import normalize_host
        if host is not None and normalize_host(machine) != normalize_host(host):
            raise TaskError("hosted wake owner machine differs from retained execution host")
        host = machine
    return native_prompt(
        actual.bridge, actual.native, steering_prompt(task_id, message),
        idempotency_key=idempotency_key,
        machine=host, launch_prefix=bridge._agent_bridge_launch_prefix,
        run_ssh=bridge.run_ssh_command,
    )
