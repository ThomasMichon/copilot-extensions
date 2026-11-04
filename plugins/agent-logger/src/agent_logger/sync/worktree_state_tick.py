"""A bounded, fair typed collection leg within the existing serialized sync tick."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import uuid4

import yaml
from agent_procutil.owned_budget import OwnedExecutionBudget
from agent_procutil.owned_process import OwnedCleanupError

from agent_logger.aggregate import (
    Admission,
    FileSystemAggregateInputProvider,
    canonical_repository_identity,
    compile_aggregate,
)
from agent_logger.aggregate_machine import resolve_machine_identity
from agent_logger.config import Config
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.sync.targets.base import Target
from agent_logger.sync.worktree_state import (
    WorktreeStateError,
    admit_repository_collection,
    canonical_bytes,
    decode_object,
)
from agent_logger.sync.worktree_state_pipeline import collect_managed_worktree_state
from agent_logger.sync.worktree_state_providers import ManagedPlacement, read_project_inventory

CURSOR_NAME = "worktree-state-sync-cursor.json"
MAX_REPOSITORIES_PER_PASS = 8
PASS_SECONDS = 120
MAX_CURSOR_BYTES = 65536


@dataclass(frozen=True)
class WorkItem:
    """A selected store, or an explicitly unevaluated repository inventory."""

    repository: str
    store: str | None = None
    detail: str = ""


@dataclass
class CollectionPass:
    """Local readbacks, known/unevaluated deferrals and actual failed attempts."""

    completed: list[WorkItem] = field(default_factory=list)
    deferred: list[WorkItem] = field(default_factory=list)
    failed: list[WorkItem] = field(default_factory=list)


@dataclass
class _Cursor:
    after_repository: str = ""
    after_store: dict[str, str] = field(default_factory=dict)


def _read_cursor(cfg: Config) -> _Cursor:
    try:
        with publication_directory(cfg.home, create=False) as directory:
            with directory.open(CURSOR_NAME, protected=True) as stream:
                raw = stream.read(MAX_CURSOR_BYTES + 1)
    except FileNotFoundError:
        return _Cursor()
    if len(raw) > MAX_CURSOR_BYTES:
        raise WorktreeStateError("typed collection cursor exceeds its byte budget")
    value = decode_object(raw)
    if (
        set(value) != {"schema", "version", "after_repository", "after_store"}
        or value["schema"] != "agent-logger.worktree-state-sync-cursor"
        or type(value["version"]) is not int
        or value["version"] != 1
        or not isinstance(value["after_repository"], str)
        or not isinstance(value["after_store"], dict)
        or len(value["after_store"]) > 128
    ):
        raise WorktreeStateError("typed collection cursor is incomplete or unsupported")
    for repository in (value["after_repository"], *value["after_store"]):
        if repository and (
            len(repository) > 512 or canonical_repository_identity(repository) != repository
        ):
            raise WorktreeStateError("typed collection cursor has an invalid repository")
    for store in value["after_store"].values():
        if not isinstance(store, str) or re.fullmatch("[0-9a-f]{64}", store) is None:
            raise WorktreeStateError("typed collection cursor has an invalid store key")
    return _Cursor(value["after_repository"], value["after_store"])


def _save_cursor(cfg: Config, cursor: _Cursor) -> None:
    raw = canonical_bytes(
        {
            "schema": "agent-logger.worktree-state-sync-cursor",
            "version": 1,
            "after_repository": cursor.after_repository,
            "after_store": cursor.after_store,
        },
    )
    if len(raw) > MAX_CURSOR_BYTES:
        raise WorktreeStateError("typed collection cursor exceeds its byte budget")
    with publication_directory(cfg.home) as directory:
        name = f".{CURSOR_NAME}.{uuid4().hex}.tmp"
        try:
            with directory.create(name, mode=0o600) as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            directory.replace(name, CURSOR_NAME)
        except BaseException:
            print(
                f"session-sync: typed cursor publication uncertain; reconcile {cfg.home / name}",
                file=sys.stderr,
            )
            raise


def _store_key(placement: ManagedPlacement) -> str:
    return sha256(
        canonical_bytes(
            {
                "worktree": placement.worktree,
                "creation_nonce": placement.creation_nonce,
                "store_id": placement.store_id,
                "placement_generation": placement.placement_generation,
            },
        ),
    ).hexdigest()


def _after(keys: list[str], previous: str) -> list[str]:
    # A removed cursor entry still has a stable successor in the sorted ring.
    return [key for key in keys if key > previous] + [key for key in keys if key <= previous]


def collect_pass(cfg: Config, target: Target) -> CollectionPass:
    """Visit at most eight repositories and one store per repository per tick.

    The private cursor is scheduling state, never a success/authority receipt.
    Advancing before owner reads and exports prevents failing or slow early
    stores from starving independent repositories or later retained stores.
    """
    result = CollectionPass()
    budget = OwnedExecutionBudget.from_timeout(PASS_SECONDS, cleanup_reserve=5)
    try:
        inputs = FileSystemAggregateInputProvider(
            machine=resolve_machine_identity(cfg),
            home=cfg.home,
            budget=budget,
        ).load()
        plan = compile_aggregate(inputs)
        eligible: dict[str, Admission] = {}
        for admission in inputs.admissions:
            if not admission.enabled or admission.quarantine_reason:
                continue
            try:
                admit_repository_collection(cfg, target, plan, admission.repository)
            except (OSError, ValueError) as error:
                result.failed.append(WorkItem(admission.repository, detail=str(error)[:1024]))
            else:
                eligible[admission.repository] = admission
        if not eligible:
            return result
        cursor = _read_cursor(cfg)
        cursor.after_store = {
            repository: key
            for repository, key in cursor.after_store.items()
            if repository in eligible
        }
    except OwnedCleanupError:
        raise
    except (OSError, ValueError, subprocess.SubprocessError, yaml.YAMLError) as error:
        result.failed.append(WorkItem("aggregate", detail=str(error)[:1024]))
        return result

    for index, repository in enumerate(_after(sorted(eligible), cursor.after_repository)):
        if index >= MAX_REPOSITORIES_PER_PASS or time.monotonic() >= budget.execution_deadline:
            result.deferred.append(
                WorkItem(repository, detail="inventory not evaluated this pass")
            )
            continue
        admission = eligible[repository]
        selected = None
        try:
            admit_repository_collection(cfg, target, plan, repository)
            cursor.after_repository = repository
            _save_cursor(cfg, cursor)
            inventory = read_project_inventory(admission, cfg.home, budget=budget)
            placements = {_store_key(placement): placement for placement in inventory.placements}
            ordered = _after(sorted(placements), cursor.after_store.get(repository, ""))
            if not ordered:
                continue
            key, *deferred = ordered
            selected = placements[key]
            result.deferred.extend(
                WorkItem(repository, placements[other].store_id, "store not attempted this pass")
                for other in deferred
            )
            if time.monotonic() >= budget.execution_deadline:
                result.deferred.append(
                    WorkItem(repository, selected.store_id, "shared execution budget exhausted"),
                )
                continue
            cursor.after_store[repository] = key
            _save_cursor(cfg, cursor)
            collect_managed_worktree_state(
                cfg,
                target,
                plan,
                admission,
                inventory,
                selected,
                budget,
            )
            result.completed.append(WorkItem(repository, selected.store_id))
        except OwnedCleanupError:
            raise
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            result.failed.append(
                WorkItem(repository, selected.store_id if selected else None, str(error)[:1024]),
            )
    return result


def sync_worktree_state(cfg: Config, target: Target) -> int:
    """Report the typed leg without promoting local readback to remote acknowledgement."""
    result = collect_pass(cfg, target)
    print(
        "session-sync: worktree-state "
        f"local-readbacks={len(result.completed)} deferred={len(result.deferred)} "
        f"failed={len(result.failed)} remote-archive-acknowledged=False",
    )
    for item in result.failed:
        print(
            f"session-sync: worktree-state failed {item.repository}"
            f"{':' + item.store if item.store else ''}: {item.detail}",
            file=sys.stderr,
        )
    return int(bool(result.failed))
