"""Ordinary initial startup consumes, but never invents, an allocator claim."""

from __future__ import annotations

from .queue_records import TaskError


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

    machine = resolve_machine()
    if (
        not isinstance(machine, str) or not machine.strip()
        or normalize_host(machine) != normalize_host(placed["machine"])
        or reservation.get("execution_machine") is not None
        and normalize_host(reservation["execution_machine"]) != normalize_host(machine)
    ):
        raise TaskError("ordinary pool startup requires its actual assigned execution machine")
    return machine
