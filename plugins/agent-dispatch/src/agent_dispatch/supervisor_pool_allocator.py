"""Managed-lane instance custody over the existing all-pool placement owner."""

from __future__ import annotations

import argparse
import os
from typing import TYPE_CHECKING

from .client_errors import DispatchError, DispatchUpgradeRequired

if TYPE_CHECKING:
    from .client import DispatchClient
    from .supervisor import Supervisor


def allocation_enabled() -> bool:
    """Source integration is opt-in independently of retained cohort publication."""
    return os.environ.get("AGENT_DISPATCH_POOL_ALLOCATION", "").strip().lower() in {"1", "true", "yes", "on"}


def validate_instance(
    instance: tuple[str, str, str] | None, machine: str | None, supervisor_id: str,
) -> None:
    if instance is None:
        return
    from .bridge_remote import normalize_host
    from .pool_instances import pool_instance_identity
    if len(instance) != 3:
        raise ValueError("managed allocator requires an exact launch instance")
    pool_instance_identity(dict(zip(("registration_id", "machine", "env"), instance)))
    if instance[0] != supervisor_id or not machine or normalize_host(instance[1]) != normalize_host(machine):
        raise ValueError("managed allocator instance conflicts with its supervisor or machine")


def configured_catalog(client: DispatchClient) -> dict | None:
    health = client.health()
    observation = health.get("pool_cohort") if isinstance(health, dict) else None
    if not isinstance(observation, dict) or (
        type(observation.get("version")) is not int or observation["version"] != 1
        or type(observation.get("revision")) is not int or observation["revision"] < 0
    ):
        raise DispatchUpgradeRequired("running coordinator lacks a current pool cohort observation")
    if observation["revision"] == 0:
        return None
    catalog = client.pool_catalog_metadata()
    cohort = catalog.get("configured_cohort")
    if not isinstance(cohort, dict) or type(cohort.get("revision")) is not int:
        raise DispatchUpgradeRequired("running coordinator lacks configured pool cohort observations")
    if cohort["revision"] < observation["revision"]:
        raise DispatchUpgradeRequired("configured pool cohort observation moved backwards")
    return catalog


def begin_cycle(supervisor: Supervisor) -> bool:
    """Only transactional instance capacity governs a configured ordinary lane."""
    supervisor._configured_pool_mode = False
    if supervisor.pool_instance is None or configured_catalog(supervisor.client) is None:
        return False
    contract = supervisor.client.pool_placement_contract()
    for feature in ("instance_bound_placement_versions", "instance_scoped_reservation_versions"):
        versions = contract.get(feature)
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            raise DispatchUpgradeRequired("running coordinator lacks managed pool instance custody")
    supervisor._configured_pool_mode = True
    return True


def matches_task(supervisor: Supervisor, task: dict) -> bool:
    """Keep legacy/manual custody while excluding another current pool owner."""
    try:
        if supervisor.client.list_reservations(
            task_id=task["id"], latest_only=True, limit=1, pool_instance=supervisor.pool_instance,
        ):
            return True
        return not supervisor.client.list_reservations(task_id=task["id"], latest_only=True, limit=1)
    except DispatchError:
        return False


def launch_scope(args: argparse.Namespace) -> tuple[str, str] | None:
    """Resolve explicit child custody, never the child's ambient environment."""
    machine = getattr(args, "registration_machine", None)
    env = getattr(args, "registration_env", None)
    if machine is None and env is None:
        return None
    registration = getattr(args, "supervisor_id", None)
    if (
        not isinstance(machine, str) or not machine.strip()
        or not isinstance(env, str) or not env.strip()
        or not isinstance(registration, str) or not registration.strip()
    ):
        raise ValueError("managed lane requires registration, machine and environment")
    from .bridge_remote import normalize_host
    from .identity import resolve_machine
    from .remote_dispatch import configured_service_machine

    try:
        actual = configured_service_machine(strict=True)
    except (OSError, UnicodeError) as exc:
        raise ValueError("managed lane installed machine binding is unreadable") from exc
    actual = actual or resolve_machine()
    if not actual or normalize_host(actual) != normalize_host(machine):
        raise ValueError("managed lane registration machine differs from its executing host")
    return machine, env


def reserve(
    client: DispatchClient, task: dict, supervisor_id: str, *,
    instance: tuple[str, str, str], ordinary: bool, restricted: bool, startup_timeout: float,
    driver: str | None = None,
) -> dict | None:
    """A different global winner waits without being claimed by this caller."""
    catalog = configured_catalog(client)
    if catalog is None:
        return None
    revision = catalog.get("revision")
    if (
        type(revision) is not int or revision < 0
        or catalog.get("configured_metadata_ready") is not True
    ):
        raise DispatchError(409, "managed pool allocation is waiting for the complete current catalog")
    driver = "ordinary-bridge" if ordinary else driver
    if restricted or driver not in {"cli", "ordinary-bridge"} or not ordinary and driver != "cli":
        raise DispatchUpgradeRequired("managed pool allocation requires a qualified ordinary Bridge or CLI backend")
    from .session_origin import reservation_options

    placed = client.place_pool_task(
        task["id"], reserved_by=supervisor_id, catalog_revision=revision,
        driver=driver, expected_instance=instance, startup_timeout=startup_timeout,
        **reservation_options(client, ordinary=ordinary),
    )
    return {
        **placed, "reservation": {
            **placed["reservation"], "reservation_pool_bound": True,
            "reservation_pool_assignment": placed["assignment"],
        },
    }


def prepare_task(
    client: DispatchClient, task: dict, reservation: dict, *,
    instance: tuple[str, str, str] | None, supervisor_id: str,
    driver: str = "ordinary-bridge",
) -> dict:
    """Refresh the allocator's generation before preparing any ordinary effects."""
    if reservation.get("reservation_pool_bound") is True and instance is None:
        from .spawn_factories import SpawnPreparationRetained
        raise SpawnPreparationRetained("allocator preparation requires its exact launch instance")
    if instance is not None:
        from .spawn_factories import SpawnPreparationRetained

        try:
            if reservation.get("reservation_pool_bound") is True and driver == "cli":
                from .client_pool_placement import _validate_contract
                _validate_contract(
                    client.pool_placement_contract(), driver=driver, explicit_start=True, initial_start=True,
                )
            current = client.get(task["id"])
            observed = client.get_reservation(reservation["key"])
            if reservation.get("reservation_pool_bound") is True and (
                observed.get("reservation_pool_bound") is not True
                or observed.get("reservation_pool_assignment") != reservation.get("reservation_pool_assignment")
            ):
                raise DispatchError(409, "ordinary allocator claim lost its original placement binding")
            if observed.get("reservation_pool_bound") is not True and current.get("status") != "queued":
                raise DispatchError(409, "ordinary allocator claim lost its pool observation")
            if observed.get("reservation_pool_bound") is True:
                assignment = observed.get("reservation_pool_assignment")
                if (
                    not isinstance(assignment, dict)
                    or tuple(assignment.get(name) for name in ("registration_id", "machine", "env")) != instance
                    or assignment.get("task_id") != task["id"]
                    or assignment.get("reservation_key") != reservation["key"]
                    or assignment.get("task_generation") != current.get("generation")
                    or current.get("status") != "claimed" or current.get("owner") is not None
                    or current.get("owner_session_id") is not None or current.get("hold_reason")
                    or current.get("awaiting_steer")
                    or observed.get("reserved_by") != supervisor_id
                    or observed.get("driver") != driver
                    or observed.get("state") != "reserving"
                    or observed.get("release_requested") or observed.get("startup_fenced")
                ):
                    raise DispatchError(409, "ordinary allocator claim changed before preparation")
                if driver == "cli":
                    from .ordinary_pool_startup import require_cli_initial_admission
                    from .queue_records import TaskError
                    try:
                        require_cli_initial_admission(observed)
                    except TaskError as exc:
                        raise DispatchError(409, str(exc)) from exc
                task = current
        except DispatchError as exc:
            raise SpawnPreparationRetained(
                f"ordinary allocator preparation retained its claim: {exc}",
            ) from exc
    return {
        **task, "spawn_reservation_key": reservation["key"], "_spawn_client": client,
        "spawn_pool_bound": reservation.get("reservation_pool_bound") is True,
    }
