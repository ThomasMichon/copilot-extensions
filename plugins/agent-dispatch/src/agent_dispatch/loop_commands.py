"""Reviewer-loop and repository-issue-loop CLI command implementations.

Split out of ``__main__.py`` (which re-exports every name here for backward
compatibility). Both loop kinds share a status/health-projection shape
(``_spawn_attempt_projection``) and identical command flows, hence one
module rather than two.

``_client``, ``_emit``, ``_registration_scope``,
``_reject_worktree_checkout_as_repo_root``, and
``_read_supervisor_runtime_status`` genuinely belong to ``__main__.py`` --
CLI-wide helpers other commands there use too, monkeypatched by tests via
their ``agent_dispatch.__main__`` attribute path. The proxies below resolve
the actually-running ``__main__`` module at call time (see
``_resolve_cli_module``) so a monkeypatched replacement still applies here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from .client import DispatchError
from .registrations import RegistrationKind
from .spawn_attempt_projection import spawn_attempt_projection

if TYPE_CHECKING:
    from .registrar import ProfileDeclaration


def _resolve_cli_module():
    """Return the actually-running ``agent_dispatch.__main__`` module.

    ``python -m agent_dispatch`` loads ``__main__.py`` as
    ``sys.modules["__main__"]``, never as
    ``sys.modules["agent_dispatch.__main__"]``. A naive ``from . import
    __main__`` would import and execute an independent second copy rather
    than the one actually running, silently diverging any patched state.
    ``runpy`` still sets the running module's ``__spec__.name`` to its real
    dotted name, so that recognizes the live copy; fall back to a normal
    dotted import (tests, or any other importer) only when neither is
    already loaded.
    """
    live = sys.modules.get("__main__")
    live_spec = getattr(live, "__spec__", None)
    if live_spec is not None and live_spec.name == "agent_dispatch.__main__":
        return live
    dotted = sys.modules.get("agent_dispatch.__main__")
    if dotted is not None:
        return dotted
    from . import __main__ as cli

    return cli


def _proxy(name: str):
    """Delegate to ``agent_dispatch.__main__.<name>`` via ``_resolve_cli_module``."""

    def _fn(*args, **kwargs):
        return getattr(_resolve_cli_module(), name)(*args, **kwargs)

    return _fn


_client = _proxy("_client")
_emit = _proxy("_emit")
_registration_scope = _proxy("_registration_scope")
_reject_worktree_checkout_as_repo_root = _proxy("_reject_worktree_checkout_as_repo_root")
_read_supervisor_runtime_status = _proxy("_read_supervisor_runtime_status")


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
    if any(unit["active_by_filter"] and not unit["served"] for unit in units):
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


def _repository_issue_loop_declarations(
    args: argparse.Namespace,
) -> tuple[Path, tuple[ProfileDeclaration, ...], str]:
    from . import repo_config as dispatch_repo_config
    from .registrar_discovery import load_pointers, read_declaration_file_set

    path = Path(args.declaration).expanduser().resolve()
    declarations = read_declaration_file_set(path)
    if len(declarations) != 2 or {declaration.kind for declaration in declarations} != {
        "emitter",
        "supervised-lane",
    }:
        raise ValueError(
            f"{path}: expected one repository-issue-loop declaration expanding "
            "to emitter and supervised-lane units"
        )
    owner = getattr(args, "owner", None)
    declared_owners = {declaration.owner for declaration in declarations}
    if owner is None and len(declared_owners) == 1:
        owner = next(iter(declared_owners))
    repo_root = dispatch_repo_config.repo_root_from_surface_path(path, "registrar")
    if repo_root is not None:
        # See the identical guard + rationale in _reviewer_loop_declarations.
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
    return (
        path,
        tuple(declaration.with_owner(owner) for declaration in declarations),
        owner,
    )


def _repository_issue_loop_registrations(args: argparse.Namespace) -> list[dict]:
    from .registrar_reconcile import declaration_to_registration

    _path, declarations, _owner = _repository_issue_loop_declarations(args)
    machine, env = _registration_scope(args)
    return [
        declaration_to_registration(declaration, machine=machine, env=env)
        for declaration in declarations
    ]


def _repository_issue_loop_setup(args: argparse.Namespace) -> int:
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
    _path, declarations, owner = _repository_issue_loop_declarations(args)
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


def _repository_issue_loop_health_path(
    registration_id: str, machine: str | None, env: str
) -> Path:
    from .config import run_dir
    from .supervisor_daemon import supervisor_lease_scope

    scope = supervisor_lease_scope(machine, env).replace(":", "-")
    safe = "".join(c if c.isalnum() or c in "._-" else "-" for c in registration_id)
    return Path(run_dir()) / "supervisor" / scope / f"{safe}.emitter.health.json"


# Re-exported under its historical private name: this is a pure projection
# with no __main__ dependency, factored into its own tested module
# (spawn_attempt_projection.py) since both loop kinds' status commands share it.
_spawn_attempt_projection = spawn_attempt_projection


def _repository_issue_loop_status(
    args: argparse.Namespace, registrations: list[dict]
) -> tuple[dict, bool]:
    from . import registrar_discovery as rd
    from .config import overrides_path, run_dir
    from .overrides import load_overrides
    from .queue import Status
    from .registrar_reconcile import runs_on_machine
    from .single_instance import is_locked, lock_path_for
    from .supervisor_daemon import (
        registration_override_ids,
        supervisor_lease_scope,
    )

    path, declarations, owner = _repository_issue_loop_declarations(args)
    machine, env = _registration_scope(args)
    scope = supervisor_lease_scope(machine, env)
    source = next(
        registration
        for registration in registrations
        if registration["kind"] == RegistrationKind.EMITTER
    )
    worker = next(
        registration
        for registration in registrations
        if registration["kind"] == RegistrationKind.SUPERVISED_LANE
    )
    source_config = source["spec"]["repository_issue_loop"]
    worker_config = worker["spec"]
    exclusive_key = f"repository-issue-loop:{source_config['name']}"
    path_pointers = [
        pointer
        for pointer in rd.load_pointers()
        if pointer.resolved_location().resolve() == path.parent
    ]
    pointers = [
        pointer.to_dict() for pointer in path_pointers if pointer.effective_owner() == owner
    ]
    overrides = load_overrides(overrides_path())
    running = is_locked(lock_path_for(run_dir(), scope))
    runtime_status, runtime_status_error = _read_supervisor_runtime_status(scope)
    runtime_fresh = bool(
        runtime_status
        and isinstance(runtime_status.get("updated_at"), (int, float))
        and runtime_status["updated_at"] >= time.time() - 120
    )
    runtime_running = set(runtime_status.get("running") or []) if runtime_fresh else set()
    units = []
    for registration, declaration in zip(registrations, declarations, strict=True):
        override_ids = registration_override_ids(registration)
        active_by_filter = runs_on_machine(declaration, machine)
        units.append(
            {
                **registration,
                "active_by_filter": active_by_filter,
                "served": bool(
                    running and active_by_filter and registration["id"] in runtime_running
                ),
                "overridden_off": any(
                    (overrides.get(override_id) or {}).get("disabled")
                    for override_id in override_ids
                ),
                "override_ids": sorted(override_ids),
            }
        )

    coordinator_error = None
    tasks = []
    failed_spawn_counts: dict[str, int] = {}
    try:
        with _client(args, ensure=False) as client:
            tasks = [
                task
                for task in client.list(
                    repo=source_config["repo"],
                    status=(
                        "proposed,queued,claimed,started,suspended,completed,abandoned,dead_letter"
                    ),
                    exclusive_key=exclusive_key,
                    limit=args.limit,
                )
            ]
            for task in tasks:
                if task.get("status") == Status.QUEUED and not task.get("owner"):
                    failed_spawn_counts[str(task["id"])] = len(
                        client.list_reservations(
                            task_id=str(task["id"]),
                            state="failed",
                            limit=10000,
                        )
                    )
    except (DispatchError, httpx.TransportError) as exc:
        coordinator_error = str(exc)

    forge_error = None
    reservations = []
    try:
        from .repository_issue_loops import GitHubProvider, _latest_reservations

        for issue in GitHubProvider(source_config["forge"]["producer_login"]).list_open_issues(
            source_config["repo"]
        ):
            for reservation in _latest_reservations(issue).values():
                if reservation.get("state") in {"reserved", "claimed"}:
                    reservations.append(
                        {
                            "issue": issue.number,
                            "url": issue.url,
                            **reservation,
                        }
                    )
    except Exception as exc:
        forge_error = str(exc)

    health_path = _repository_issue_loop_health_path(source["id"], machine, env)
    emitter_health = None
    emitter_health_error = None
    try:
        if health_path.exists():
            emitter_health = json.loads(health_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        emitter_health_error = str(exc)
    stale_after = float(source_config["cadence_seconds"]) + 2 * float(
        source_config.get("tick_interval_seconds") or 60
    )
    emitter_stale = bool(
        emitter_health
        and isinstance(emitter_health.get("updated_at"), (int, float))
        and time.time() - emitter_health["updated_at"] > stale_after
    )
    active = [task for task in tasks if task.get("status") not in Status.TERMINAL]
    default_spawn_attempts = int(worker_config.get("max_attempts", 3))
    label_spawn_attempts = {
        str(label): int(value)
        for label, value in (worker_config.get("label_max_attempts") or {}).items()
    }
    spawn_projections = {
        str(task["id"]): _spawn_attempt_projection(
            task,
            failures=failed_spawn_counts.get(str(task["id"]), 0),
            default_max_attempts=default_spawn_attempts,
            label_max_attempts=label_spawn_attempts,
        )
        for task in active
    }
    spawn_dead_letters = {
        task_id: projection
        for task_id, projection in spawn_projections.items()
        if projection["dead_lettered"]
    }
    diagnoses = []
    actions = []
    if not pointers:
        diagnoses.append("missing-pointer")
        actions.append(f"agent-dispatch repository-issue-loop setup {path}")
    if any(unit["active_by_filter"] and not unit["served"] for unit in units):
        diagnoses.append("declared-but-unserved")
    if any(unit["overridden_off"] for unit in units):
        diagnoses.append("overridden-off")
        actions.append(f"agent-dispatch repository-issue-loop enable {path}")
    if coordinator_error:
        diagnoses.append("coordinator-unavailable")
    if forge_error:
        diagnoses.append("forge-unavailable")
    if emitter_health and not emitter_health.get("ok", False):
        diagnoses.append("emitter-failure")
    if emitter_health_error:
        diagnoses.append("emitter-health-unreadable")
    if (
        emitter_health is None
        and not emitter_health_error
        and any(unit["served"] for unit in units if unit["kind"] == RegistrationKind.EMITTER)
    ):
        diagnoses.append("emitter-never-ran")
    if emitter_stale:
        diagnoses.append("emitter-stale")
    if any(task.get("awaiting_steer") for task in active):
        diagnoses.append("blocked")
    if spawn_dead_letters:
        diagnoses.append("spawn-dead-lettered")
        actions.extend(
            spawn_dead_letters[task_id]["rearm"]
            for task_id in sorted(spawn_dead_letters)
            if "rearm" in spawn_dead_letters[task_id]
        )
    healthy = not diagnoses
    if healthy:
        diagnoses.append("healthy")
    return (
        {
            "declaration": str(path),
            "owner": owner,
            "pointer": {
                "registered": bool(pointers),
                "matches": pointers,
            },
            "service": {
                "scope": scope,
                "machine": machine,
                "env": env,
                "running": running,
                "runtime_status": runtime_status,
                "runtime_status_error": runtime_status_error,
                "runtime_status_fresh": runtime_fresh,
                "coordinator_error": coordinator_error,
            },
            "units": units,
            "emitter": {
                "health_path": str(health_path),
                "last": emitter_health,
                "read_error": emitter_health_error,
                "stale": emitter_stale,
            },
            "active_occurrence": (
                {
                    "task_id": active[0].get("id"),
                    "origin_ref": active[0].get("origin_ref"),
                    "status": active[0].get("status"),
                    "awaiting_steer": active[0].get("awaiting_steer"),
                    "spawn_failures": spawn_projections[str(active[0]["id"])]["failed_spawns"],
                    "spawn_attempt_limit": spawn_projections[str(active[0]["id"])]["max_attempts"],
                    "spawn_dead_lettered": spawn_projections[str(active[0]["id"])][
                        "dead_lettered"
                    ],
                    "spawn_recovery": spawn_projections[str(active[0]["id"])].get("recovery"),
                }
                if active
                else None
            ),
            "reservations": reservations,
            "pool": {
                "concurrency": 1,
                "active_tasks": len(active),
                "served": next(
                    unit["served"]
                    for unit in units
                    if unit["kind"] == RegistrationKind.SUPERVISED_LANE
                ),
            },
            "kill_switch": {
                "disabled": any(unit["overridden_off"] for unit in units),
            },
            "forge_error": forge_error,
            "diagnoses": diagnoses,
            "healthy": healthy,
            "actions": actions,
        },
        healthy,
    )


def _cmd_repository_issue_loop(args: argparse.Namespace) -> int:
    from .config import overrides_path
    from .overrides import load_overrides, mutate_overrides
    from .repository_issue_loops import GitHubProvider, run_tick
    from .supervisor_daemon import registration_override_ids

    try:
        if args.repository_issue_loop_command == "setup":
            return _repository_issue_loop_setup(args)
        registrations = _repository_issue_loop_registrations(args)
        command = args.repository_issue_loop_command
        if command in {"status", "doctor"}:
            payload, healthy = _repository_issue_loop_status(args, registrations)
            _emit(payload)
            return 0 if command == "status" or healthy else 1
        all_ids = {
            override_id
            for registration in registrations
            for override_id in registration_override_ids(registration)
        }
        if command == "disable":
            now = time.time()

            def disable(current: dict[str, dict]) -> list[str]:
                for override_id in all_ids:
                    current[override_id] = {
                        "disabled": True,
                        "reason": args.reason,
                        "at": now,
                    }
                return sorted(all_ids)

            changed = mutate_overrides(overrides_path(), disable)
            return _emit({"enabled": False, "changed": changed})
        if command == "enable":

            def enable(current: dict[str, dict]) -> list[str]:
                changed = sorted(all_ids & set(current))
                for override_id in changed:
                    del current[override_id]
                return changed

            changed = mutate_overrides(overrides_path(), enable)
            return _emit({"enabled": True, "changed": changed})
        overrides = load_overrides(overrides_path())
        if command == "inspect":
            return _emit(
                {
                    "declaration": str(Path(args.declaration).expanduser().resolve()),
                    "units": [
                        {
                            **registration,
                            "override_ids": sorted(registration_override_ids(registration)),
                            "overridden_off": any(
                                (overrides.get(override_id) or {}).get("disabled")
                                for override_id in registration_override_ids(registration)
                            ),
                        }
                        for registration in registrations
                    ],
                }
            )
        source = next(
            registration
            for registration in registrations
            if registration["kind"] == RegistrationKind.EMITTER
        )
        if any(
            (overrides.get(override_id) or {}).get("disabled")
            for override_id in registration_override_ids(source)
        ):
            raise ValueError("repository issue loop is disabled")
        with _client(args) as client:
            return _emit(
                run_tick(
                    client,
                    source["spec"]["repository_issue_loop"],
                    provider=GitHubProvider(
                        source["spec"]["repository_issue_loop"]["forge"]["producer_login"]
                    ),
                    dry_run=True,
                )
            )
    except (DispatchError, OSError, ValueError) as exc:
        print(f"agent-dispatch repository-issue-loop: {exc}", file=sys.stderr)
        return 2
