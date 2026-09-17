"""Reviewer-loop CLI command implementations.

Split out of ``loop_commands.py`` (which now holds repository-issue-loop
commands only) purely to keep both modules under the line cap as the repo
grows -- ``__main__.py`` re-exports every name here for backward
compatibility, same as before the split. See ``loop_commands.py``'s own
docstring for the shared ``_client``/``_emit``/``_registration_scope``/
``_reject_worktree_checkout_as_repo_root``/``_read_supervisor_runtime_status``
proxy story; this module imports them from there rather than redefining them.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .client import DispatchError
from .loop_commands import (
    _client,
    _emit,
    _read_supervisor_runtime_status,
    _registration_scope,
    _reject_worktree_checkout_as_repo_root,
)
from .registrations import RegistrationKind
from .spawn_attempt_projection import spawn_attempt_projection as _spawn_attempt_projection
from . import supervisor_health as _sh

if TYPE_CHECKING:
    from .registrar import ProfileDeclaration


def _reviewer_loop_declarations(
    args: argparse.Namespace,
) -> tuple[Path, tuple[ProfileDeclaration, ...], str]:
    from . import repo_config as dispatch_repo_config
    from .registrar_discovery import (
        load_pointers,
        read_declaration_file_set,
    )

    path = Path(args.declaration).expanduser().resolve()
    declarations = read_declaration_file_set(path)
    expected = {"emitter", "evaluator", "supervised-lane"}
    if len(declarations) != 3 or {declaration.kind for declaration in declarations} != expected:
        raise ValueError(
            f"{path}: expected one reviewer-loop declaration expanding to "
            "emitter, evaluator, and supervised-lane units"
        )
    owner = getattr(args, "owner", None)
    declared_owners = {declaration.owner for declaration in declarations}
    if owner is None and len(declared_owners) == 1:
        owner = next(iter(declared_owners))
    repo_root = dispatch_repo_config.repo_root_from_surface_path(path, "registrar")
    if repo_root is not None:
        # Guard unconditionally, even when `owner` is already explicit: the
        # pointer this flow persists (see _reviewer_loop_setup) records
        # repo_root itself as its `location`, and a worktree checkout path is
        # never a valid thing to persist there regardless of what owner
        # string ends up attached to it.
        _reject_worktree_checkout_as_repo_root(repo_root)
    selected_dir = (
        dispatch_repo_config.selected_repo_surface_dir(repo_root, "registrar").resolve()
        if repo_root is not None
        else None
    )
    if owner is None:
        matching = [
            pointer.effective_owner()
            for pointer in load_pointers()
            if pointer.resolved_location().resolve() == (selected_dir or path.parent)
        ]
        if len(matching) == 1:
            owner = matching[0]
    if owner is None and repo_root is not None:
        owner = f"repo:{repo_root.name}"
    if owner is None:
        raise ValueError(
            f"{path}: declaration owner is ambiguous; register its containing "
            "directory or pass --owner"
        )
    declarations = tuple(declaration.with_owner(owner) for declaration in declarations)
    return path, declarations, owner


def _reviewer_loop_registrations(args: argparse.Namespace) -> list[dict]:
    from .registrar_reconcile import declaration_to_registration

    _path, declarations, _owner = _reviewer_loop_declarations(args)
    machine, env = _registration_scope(args)
    return [
        declaration_to_registration(declaration, machine=machine, env=env)
        for declaration in declarations
    ]


def _reviewer_loop_setup(args: argparse.Namespace) -> int:
    from . import registrar_discovery as rd
    from . import repo_config as dispatch_repo_config

    path = Path(args.declaration).expanduser().resolve()
    repo_root = dispatch_repo_config.repo_root_from_surface_path(path, "registrar")
    if repo_root is None:
        raise ValueError(
            f"{path}: setup requires a declaration under "
            "<repo>/.copilot-extensions/agent-dispatch/registrar/ "
            "(legacy <repo>/.agent-dispatch/registrar/ also accepted)"
        )
    _path, declarations, owner = _reviewer_loop_declarations(args)
    name = args.name or repo_root.name
    existing = next((item for item in rd.load_pointers() if item.name == name), None)
    selected_dir = dispatch_repo_config.selected_repo_surface_dir(repo_root, "registrar")
    if existing is not None and existing.resolved_location().resolve() != selected_dir.resolve():
        raise ValueError(
            f"registrar pointer {name!r} already targets "
            f"{existing.resolved_location()}; pass a unique --name"
        )
    pointer = rd.add_pointer(
        name,
        repo_root,
        kind="repo",
        owner=args.owner
        or (owner if any(declaration.owner for declaration in declarations) else None),
    )
    return _emit(
        {
            "declaration": str(path),
            "repo_root": str(repo_root),
            "pointer": pointer.to_dict(),
            "changed": existing != pointer,
        }
    )


def _reviewer_loop_status(
    args: argparse.Namespace,
    registrations: list[dict],
    logical_aliases: dict[str, set[str]],
) -> tuple[dict, bool]:
    from . import registrar_discovery as rd
    from .config import overrides_path, run_dir
    from .overrides import load_overrides
    from .single_instance import is_locked, lock_path_for
    from .supervisor_daemon import supervisor_lease_scope

    path, declarations, owner = _reviewer_loop_declarations(args)
    machine, env = _registration_scope(args)
    scope = supervisor_lease_scope(machine, env)
    pool = next(
        declaration
        for declaration in declarations
        if declaration.kind == RegistrationKind.SUPERVISED_LANE
    )
    from .identity import canonicalize_remote

    pool_repo = None if pool.repos == "all" else canonicalize_remote(pool.repos)
    path_pointers = [
        pointer
        for pointer in rd.load_pointers()
        if pointer.resolved_location().resolve() == path.parent
    ]
    pointers = [
        pointer.to_dict() for pointer in path_pointers if pointer.effective_owner() == owner
    ]
    overrides = load_overrides(overrides_path())
    coordinator_error = None
    direct: list[dict] = []
    tasks: list[dict] = []
    task_scan_truncated = False
    failed_counts: dict[str, int] = {}
    try:
        with _client(args, ensure=False) as client:
            direct = client.list_registrations(
                machine=machine,
                env=env,
                include_paused=True,
            )
            evaluator_ref = next(
                registration["spec"]["evaluator_ref"]
                for registration in registrations
                if registration["kind"] == RegistrationKind.EMITTER
            )
            tasks = client.list(
                repo=pool_repo,
                evaluator_ref=evaluator_ref,
                status="queued,claimed,started,suspended",
                limit=args.limit + 1,
            )
            task_scan_truncated = len(tasks) > args.limit
            tasks = tasks[: args.limit]
            for task in tasks:
                if task.get("status") != "queued" or task.get("owner"):
                    continue
                failed_counts[task["id"]] = len(
                    client.list_reservations(
                        task_id=task["id"],
                        state="failed",
                        limit=10000,
                    )
                )
    except (DispatchError, httpx.TransportError) as exc:
        coordinator_error = str(exc)

    from .supervisor_daemon import merge_registration_sources

    replacements = merge_registration_sources(direct, registrations).replacements
    aliases = {
        registration["id"]: {
            direct_id
            for direct_id, declared_id in replacements.items()
            if declared_id == registration["id"]
        }
        | logical_aliases[registration["id"]]
        for registration in registrations
    }
    from .registrar_reconcile import runs_on_machine

    running = is_locked(lock_path_for(run_dir(), scope))
    runtime_status, runtime_status_error = _read_supervisor_runtime_status(scope)
    runtime_fresh = bool(
        runtime_status
        and isinstance(runtime_status.get("updated_at"), (int, float))
        and runtime_status["updated_at"] >= time.time() - 120
    )
    runtime_running = set(runtime_status.get("running") or []) if runtime_fresh else set()
    runtime_backing_off = set(runtime_status.get("backing_off") or []) if runtime_fresh else set()
    runtime_dead = set(runtime_status.get("dead") or []) if runtime_fresh else set()
    direct_ids = {registration["id"] for registration in direct}
    units = []
    for registration, declaration in zip(registrations, declarations, strict=True):
        ids = {registration["id"], *aliases[registration["id"]]}
        served_ids = sorted(ids & direct_ids)
        override_ids = sorted(
            override_id
            for override_id in ids
            if (overrides.get(override_id) or {}).get("disabled")
        )
        active_by_filter = runs_on_machine(declaration, machine)
        runtime_state = (
            "running"
            if registration["id"] in runtime_running
            else "backing-off"
            if registration["id"] in runtime_backing_off
            else "dead"
            if registration["id"] in runtime_dead
            else "not-running"
        )
        served = running and runtime_state == "running"
        units.append(
            {
                **registration,
                "active_by_filter": active_by_filter,
                "served": served,
                "runtime_state": runtime_state,
                "served_ids": served_ids,
                "overridden_off": bool(override_ids),
                "override_ids": override_ids,
            }
        )

    task_items = []
    for task in tasks:
        spawn = _spawn_attempt_projection(
            task,
            failures=failed_counts.get(task["id"], 0),
            default_max_attempts=pool.max_attempts,
            label_max_attempts=pool.label_max_attempts,
        )
        blocked = bool(task.get("awaiting_steer"))
        matches_repo = pool_repo is None or task.get("repo") == pool_repo
        matches_labels = not pool.labels or bool(set(pool.labels) & set(task.get("labels") or []))
        inactive_by_filter = not (matches_repo and matches_labels)
        item = {
            "id": task["id"],
            "status": task.get("status"),
            "owner": task.get("owner"),
            "awaiting_steer": blocked,
            "inactive_by_filter": inactive_by_filter,
            **spawn,
        }
        task_items.append(item)

    diagnoses = []
    actions = []
    if not pointers:
        diagnoses.append("missing-pointer")
        actions.append(f"agent-dispatch reviewer-loop setup {path}")
    stall_seconds = _sh.supervisor_stall_seconds(runtime_status)
    if stall_seconds is not None:
        diagnoses.append("supervisor-stalled")
        actions.append(_sh.supervisor_stall_action(stall_seconds))
    elif any(unit["active_by_filter"] and not unit["served"] for unit in units):
        diagnoses.append("declared-but-unserved")
    if coordinator_error:
        diagnoses.append("coordinator-unavailable")
    if any(unit["overridden_off"] for unit in units):
        diagnoses.append("overridden-off")
        actions.append(f"agent-dispatch reviewer-loop enable {path}")
    if any(not unit["active_by_filter"] for unit in units) or any(
        item["inactive_by_filter"] for item in task_items
    ):
        diagnoses.append("inactive-by-filter")
    if any(item["awaiting_steer"] for item in task_items):
        diagnoses.append("blocked")
    dead_lettered = [item for item in task_items if item["dead_lettered"]]
    if dead_lettered:
        diagnoses.append("dead-lettered")
        actions.extend(item["rearm"] for item in dead_lettered if "rearm" in item)
    if task_scan_truncated:
        diagnoses.append("task-scan-truncated")
    healthy = not diagnoses
    if healthy:
        diagnoses.append("healthy")

    payload = {
        "declaration": str(path),
        "owner": owner,
        "pointer": {
            "registered": bool(pointers),
            "matches": pointers,
            "owner_mismatches": [
                pointer.to_dict()
                for pointer in path_pointers
                if pointer.effective_owner() != owner
            ],
        },
        "service": {
            "scope": scope,
            "machine": machine,
            "env": env,
            "running": running,
            "coordinator_error": coordinator_error,
            "runtime_status": runtime_status,
            "runtime_status_error": runtime_status_error,
            "runtime_status_fresh": runtime_fresh,
            "runtime_stall_seconds": stall_seconds,
        },
        "units": units,
        "tasks": {
            "count": len(task_items),
            "truncated": task_scan_truncated,
            "items": task_items,
        },
        "diagnoses": diagnoses,
        "healthy": healthy,
        "actions": actions,
    }
    return payload, healthy


def _cmd_reviewer_loop(args: argparse.Namespace) -> int:
    from .config import overrides_path
    from .overrides import (
        load_overrides,
        mutate_overrides,
    )
    from .producers import emitter
    from .supervisor_daemon import (
        merge_registration_sources,
        registration_override_ids,
    )

    try:
        if args.reviewer_loop_command == "setup":
            return _reviewer_loop_setup(args)
        registrations = _reviewer_loop_registrations(args)
        machine, env = _registration_scope(args)
        command = args.reviewer_loop_command
        logical_aliases = {
            registration["id"]: registration_override_ids(registration) - {registration["id"]}
            for registration in registrations
        }
        if command in {"status", "doctor"}:
            payload, healthy = _reviewer_loop_status(
                args,
                registrations,
                logical_aliases,
            )
            _emit(payload)
            return 0 if command == "status" or healthy else 1
        if command == "disable":
            now = time.time()

            def disable(current: dict[str, dict]) -> list[str]:
                changed = []
                for registration in registrations:
                    for override_id in {
                        registration["id"],
                        *logical_aliases[registration["id"]],
                    }:
                        current[override_id] = {
                            "disabled": True,
                            "reason": args.reason,
                            "at": now,
                        }
                        changed.append(override_id)
                return changed

            changed = mutate_overrides(overrides_path(), disable)
            return _emit(
                {
                    "enabled": False,
                    "changed": changed,
                    "units": [registration["id"] for registration in registrations],
                }
            )

        with _client(args) as client:
            direct = client.list_registrations(
                machine=machine,
                env=env,
                include_paused=True,
            )
        replacements = merge_registration_sources(direct, registrations).replacements
        aliases = {
            registration["id"]: {
                direct_id
                for direct_id, declared_id in replacements.items()
                if declared_id == registration["id"]
            }
            | logical_aliases[registration["id"]]
            for registration in registrations
        }
        if command == "inspect":
            overrides = load_overrides(overrides_path())
            return _emit(
                {
                    "declaration": str(Path(args.declaration).expanduser().resolve()),
                    "units": [
                        {
                            **registration,
                            "override_ids": sorted(
                                {registration["id"], *aliases[registration["id"]]}
                            ),
                            "overridden_off": any(
                                (overrides.get(override_id) or {}).get("disabled")
                                for override_id in {
                                    registration["id"],
                                    *aliases[registration["id"]],
                                }
                            ),
                        }
                        for registration in registrations
                    ],
                }
            )
        if command == "enable":

            def mutate(current: dict[str, dict]) -> list[str]:
                changed = []
                for registration in registrations:
                    registration_id = registration["id"]
                    override_ids = {
                        registration_id,
                        *aliases[registration_id],
                    }
                    for override_id in override_ids:
                        if override_id in current:
                            del current[override_id]
                            changed.append(override_id)
                return changed

            changed = mutate_overrides(overrides_path(), mutate)
            return _emit(
                {
                    "enabled": True,
                    "changed": changed,
                    "units": [registration["id"] for registration in registrations],
                }
            )
        source = next(
            registration
            for registration in registrations
            if registration["kind"] == RegistrationKind.EMITTER
        )
        source_override_ids = {source["id"], *aliases[source["id"]]}
        current = load_overrides(overrides_path())
        if any(
            (current.get(override_id) or {}).get("disabled") for override_id in source_override_ids
        ):
            raise ValueError(f"reviewer loop is disabled by override on {source['id']!r}")
        from .registrar_reconcile import runs_on_machine

        _path, declarations, _owner = _reviewer_loop_declarations(args)
        source_declaration = next(
            declaration
            for declaration in declarations
            if declaration.kind == RegistrationKind.EMITTER
        )
        if not runs_on_machine(source_declaration, machine):
            raise ValueError(f"reviewer loop source is inactive on machine {machine!r}")
        with _client(args) as side_load_client:
            return _emit(
                emitter.run_side_load(
                    side_load_client,
                    source,
                    args.change_ref,
                    current_machine=machine,
                    current_env=env,
                )
            )
    except (DispatchError, OSError, ValueError, emitter.EmitterError) as exc:
        print(f"agent-dispatch reviewer-loop: {exc}", file=sys.stderr)
        return 2
