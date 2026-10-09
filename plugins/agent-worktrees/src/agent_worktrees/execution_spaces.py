"""Registered execution-space fences for local ownership and lifecycle writes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from machine_transport.registry import MachineEntry, find_machine_entry, machine_name


class ExecutionSpaceError(RuntimeError):
    """An ownership mutation has no unambiguous local execution-space identity."""


def detect_legacy_machine(entries: dict[str, MachineEntry], hostname: str) -> str:
    if any(entry.execution_platform for entry in entries.values()):
        raise ExecutionSpaceError(
            "registered execution spaces require explicit machine configuration; "
            "hostname does not select a local registry"
        )
    for key, entry in entries.items():
        if hostname == key.lower():
            return machine_name(entry)
    for entry in entries.values():
        if entry.hostname and hostname == entry.hostname.lower():
            return machine_name(entry)
    for entry in entries.values():
        if entry.alias and hostname == entry.alias.lower():
            return machine_name(entry)
    return hostname


def resolve_owner_record_path(owner_ref: str, config: Any) -> tuple[Path | None, str, str | None]:
    from . import config as cfg, tracking

    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None or not parsed.is_qualified:
        return (
            None, "",
            f"--owner-ref must be a qualified machine/project/worktree_id ref (got {owner_ref!r})",
        )
    try:
        local = require_owner_identity(parsed.machine, config)
    except ExecutionSpaceError as exc:
        return None, parsed.worktree_id, str(exc)
    if not local:
        error = (
            "cross-space owner ledger mutation is unsupported"
            if any(entry.execution_platform for entry in registry_for_config(config).values())
            else None
        )
        return None, parsed.worktree_id, error
    return (
        cfg.project_dir(parsed.project) / "worktrees" / f"{parsed.worktree_id}.yaml",
        parsed.worktree_id, None,
    )


def settle_parent_obligation(record: Any, config: Any, worktree_id: str, *, output: Any) -> None:
    from . import config as cfg, obligations, tracking

    require_record_mutation(record, config)
    try:
        owner = record.owner_claim_ref
        if owner is None or not owner.is_qualified or owner.machine != config.machine:
            return
        parent_path = cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml"
        if not parent_path.exists():
            return
        with tracking._RecordLock(parent_path, require_sidecar=True):
            parent = tracking.load_record(parent_path)
            require_record_mutation(parent, config)
            child_ref = tracking.format_claim_ref(config.machine, config.repo_name, worktree_id)
            settled = tracking.settle_resource_claim(
                parent, child_ref, obligations.AT_REST, save=False,
            )
            if settled is not None:
                tracking.save_record(parent, parent_path)
        if settled is not None:
            output.ok(
                f"Settled parent {owner.worktree_id}'s claim on this worktree (-> at-rest)"
            )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        output.warn(
            f"Cannot confirm parent-obligation settlement for {worktree_id}: "
            f"{type(exc).__name__}. Inspect the owning ledger before retrying."
        )


def registry_for_config(config: Any) -> dict[str, MachineEntry]:
    from . import config as cfg

    try:
        anchor = config.default_repo.anchor
    except (AttributeError, KeyError):
        return {}
    try:
        return cfg.load_machines_yaml(anchor)
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise ExecutionSpaceError(f"execution-space registry is invalid: {exc}") from exc


def require_owner_identity(owner: str, config: Any, *, canonical_ref: bool = True) -> bool:
    """Return whether an owner is local; reject ambiguous legacy scope names."""
    entries = registry_for_config(config)
    try:
        current = find_machine_entry(entries, config.machine)
        target = find_machine_entry(entries, owner)
    except ValueError as exc:
        raise ExecutionSpaceError(str(exc)) from exc
    explicit = any(entry.execution_platform for entry in entries.values())
    if explicit:
        if current is None or not current.execution_platform or config.machine != current.key:
            raise ExecutionSpaceError(
                "owned mutation requires the current registered execution-space key "
                "in machine configuration"
            )
        from .config import detect_platform

        if current.execution_platform != detect_platform():
            raise ExecutionSpaceError(
                "configured execution-space key does not match the current execution platform"
            )
        if target is None or not target.execution_platform or (canonical_ref and owner != target.key):
            raise ExecutionSpaceError(
                f"legacy owner identity {owner!r} is not an explicit execution-space key"
            )
        return target.key == current.key
    if current is not None and target is not None:
        return target is current
    return owner == config.machine


def require_current_execution_space(config: Any) -> None:
    if any(entry.execution_platform for entry in registry_for_config(config).values()):
        require_owner_identity(config.machine, config)


def require_record_mutation(record: Any, config: Any) -> None:
    from . import tracking

    if record is None:
        return
    if not any(entry.execution_platform for entry in registry_for_config(config).values()):
        return
    if not require_owner_identity(record.machine, config):
        raise ExecutionSpaceError(
            f"record {record.worktree_id!r} belongs to a different execution space"
        )
    if record.owner_ref:
        owner = tracking.parse_claim_ref(record.owner_ref)
        if owner is None or not owner.is_qualified:
            raise ExecutionSpaceError("owned record has an unqualified owner reference")
        if not require_owner_identity(owner.machine, config):
            raise ExecutionSpaceError(
                "cross-space parent ownership remains unsupported; settlement is blocked"
            )


def require_project_record_mutation(record: Any) -> None:
    """Resolve receiving-side project authority, never caller-supplied identity."""
    from . import config as cfg

    try:
        config = cfg.load_project_config(record.repo)
    except (OSError, ValueError) as exc:
        raise ExecutionSpaceError(
            f"cannot establish receiving-side authority for project {record.repo!r}: {exc}"
        ) from exc
    require_record_mutation(record, config)


def require_cleanup_identity(record: Any, anchor: str | Path) -> None:
    """Cleanup has only repo metadata; require config only for scoped registries."""
    from . import config as cfg

    try:
        entries = cfg.load_machines_yaml(anchor)
    except FileNotFoundError:
        return
    if not any(entry.execution_platform for entry in entries.values()):
        return
    require_record_mutation(record, cfg.load_config())
