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
    writer_identity: dict | None = None, worktree_writer: dict | None = None,
) -> dict:
    """Create through the existing Dispatch-aware owner before Bridge embodiment."""
    from .workspace_binding import hold, record_prepared
    from .ordinary_writer_admission import REQUIREMENT, writer_requirement

    if reservation.get("agent_info"):
        return reservation
    if reservation.get("worktree"):
        if reservation.get("task_relationship_required") is True and reservation.get(REQUIREMENT) is not None:
            record_prepared(
                client, reservation, {
                    "worktree": reservation["worktree"], "path": reservation.get("worktree_path"),
                    "project": reservation.get("allocation_project"),
                    "ownership": reservation.get("worktree_ownership"),
                    "creation_nonce": reservation.get("creation_nonce"),
                }, driver="ordinary-bridge", creating_host=machine,
                worktree_writer_requirement=writer_requirement(reservation[REQUIREMENT]),
                timeout=timeout,
            )
            return client.get_reservation(reservation["key"])
        return reservation
    from . import embody

    prepared = embody.prepare_reusable_worktree(
        task, reservation, project=project, interface="acp", driver="ordinary-bridge",
        supervisor=reservation["reserved_by"], timeout=timeout, no_pair=no_pair,
        allocation_client=client,
    )
    requirement = None
    if writer_identity is not None:
        requirement = writer_requirement({
            "version": 1, "identity": writer_identity,
            "creation_nonce": prepared.get("creation_nonce"),
        })
        if worktree_writer is not None and writer_requirement(worktree_writer) != requirement:
            hold("ordinary prepared workspace differs from its expected writer requirement")
    record_prepared(
        client, reservation, prepared, driver="ordinary-bridge", creating_host=machine,
        **({"worktree_writer_requirement": requirement} if requirement is not None else {}),
        timeout=timeout,
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
        hold("ordinary owned-workspace readback is unconfirmed; retain the original creation")
    return current


def created_workspace_record(task: dict, reservation: dict, info: dict) -> dict:
    """Recover the original sessionless admission from the same-key Bridge owner."""
    from .ordinary_agent_facts import identity, owner_record, sessionless
    from .bridge_remote import normalize_host

    creation = info.get("creation")
    actual = identity(info)
    if (
        not isinstance(creation, dict) or type(creation.get("version")) is not int
        or creation["version"] != 1 or creation.get("status") != "complete"
        or creation.get("native_session_id") != actual.native
        or creation.get("turn_index") is not None
    ):
        raise TaskError("owned ordinary creation lacks its original native admission receipt")
    record = owner_record(creation.get("worktree"), reservation, task["id"])
    expected = {
        "task_id": task["id"], "reservation_key": reservation["key"],
        "attempt": reservation["attempt"], "driver": "ordinary-bridge",
        "supervisor": reservation["reserved_by"],
        "creator_machine": reservation["execution_machine"], "ownership": "created",
    }
    allocation = record.get("dispatch_attempt")
    if isinstance(allocation, dict) and (
        "cohort" in allocation or reservation.get("allocation_cohort_transport") == "cohort-v1"
    ):
        original = reservation.get("allocation_cohort")
        if not isinstance(original, dict):
            raise TaskError("owned ordinary creation lacks its original cohort evidence")
        expected["cohort"] = original
    if (
        not sessionless(record) or not isinstance(allocation, dict)
        or not isinstance(allocation.get("creator_machine"), str)
        or normalize_host(allocation["creator_machine"]) != normalize_host(expected["creator_machine"])
        or {**allocation, "creator_machine": expected["creator_machine"]} != expected
    ):
        raise TaskError("owned ordinary creation changed its original sessionless allocation")
    return record
