"""Ordinary initial startup consumes, but never invents, an allocator claim."""

from __future__ import annotations

from .queue_records import TaskError


def require_cli_initial_admission(reservation: dict) -> None:
    """Consume the coordinator's current deadline decision, not a local clock."""
    if "cli_initial_start_error" not in reservation or reservation["cli_initial_start_error"] is not None:
        raise TaskError(
            reservation.get("cli_initial_start_error") or "CLI initial-start admission is unavailable",
        )


def assignment(task: dict, reservation: dict, *, driver: str = "ordinary-bridge") -> dict | None:
    """Keep placed claims separate from the legacy queued/evaluation contract."""
    bound = reservation.get("reservation_pool_bound")
    value = reservation.get("reservation_pool_assignment")
    if bound is not True:
        if value is not None or task.get("status") == "claimed" and task.get("owner") is None:
            raise TaskError("ordinary allocator claim has no proven pool assignment")
        return None
    if not isinstance(value, dict) or (
        driver not in {"cli", "ordinary-bridge"} or reservation.get("driver") != driver
        or type(reservation.get("startup_revision")) is not int or reservation["startup_revision"] != 1
        or value.get("task_id") != task.get("id")
        or value.get("reservation_key") != reservation.get("key")
        or type(value.get("task_generation")) is not int
        or type(task.get("generation")) is not int or task["generation"] < 1
        or value["task_generation"] != task.get("generation")
        or any(not isinstance(value.get(name), str) or not value[name].strip()
               for name in ("registration_id", "machine", "env"))
    ):
        raise TaskError("ordinary pool assignment incarnation or driver changed")
    return value


def initial_machine(task: dict, reservation: dict) -> str | None:
    """Qualify the existing local Bridge launch before it can create anything."""
    placed = assignment(task, reservation)
    if placed is None:
        return None
    from .bridge_remote import normalize_host
    from .identity import resolve_machine
    from .remote_dispatch import configured_service_machine

    try:
        machine = configured_service_machine(strict=True)
    except (OSError, UnicodeError) as exc:
        raise TaskError("ordinary pool startup installed machine binding is unreadable") from exc
    machine = machine or resolve_machine()
    if (
        not isinstance(machine, str) or not machine.strip()
        or normalize_host(machine) != normalize_host(placed["machine"])
        or reservation.get("execution_machine") is not None
        and normalize_host(reservation["execution_machine"]) != normalize_host(machine)
    ):
        raise TaskError("ordinary pool startup requires its actual assigned execution machine")
    return machine


def prepare_owned_workspace(
    task: dict, reservation: dict, *, client, project: str | None,
    machine: str, no_pair: bool, timeout: float,
) -> dict:
    """Create through the existing Dispatch-aware owner before Bridge embodiment."""
    if reservation.get("worktree") or reservation.get("agent_info"):
        return reservation
    from . import embody
    from .workspace_binding import record_prepared

    prepared = embody.prepare_reusable_worktree(
        task, reservation, project=project, interface="acp", driver="ordinary-bridge",
        supervisor=reservation["reserved_by"], timeout=timeout, no_pair=no_pair,
        allocation_client=client,
    )
    record_prepared(
        client, reservation, prepared, driver="ordinary-bridge", creating_host=machine,
    )
    current = client.get_reservation(reservation["key"])
    if (
        not isinstance(current, dict)
        or current.get("worktree") != prepared["worktree"]
        or current.get("worktree_ownership") != "created"
        or current.get("allocation_project") != prepared["project"]
        or current.get("worktree_path") != prepared["path"]
        or current.get("creation_key") != reservation["creation_key"]
    ):
        from .workspace_binding import hold
        hold("ordinary owned-workspace readback is unconfirmed; retain the original creation")
    return current
