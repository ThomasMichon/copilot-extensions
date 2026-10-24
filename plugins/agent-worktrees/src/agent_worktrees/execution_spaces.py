"""Registered execution-space fences for local ownership and lifecycle writes."""

from __future__ import annotations

import threading
from dataclasses import replace
from pathlib import Path
from typing import Any

import yaml
from machine_transport.registry import MachineEntry, find_machine_entry, machine_name, resolve_machine_selection
from machine_transport import split_machine_environment


class ExecutionSpaceError(RuntimeError):
    """An ownership mutation has no unambiguous local execution-space identity."""


# Serializes ``require_project_record_mutation``'s resolution of a NAMED
# project's config: ``cfg.load_project_config`` temporarily mutates the
# process-global active-project state (set, load, restore). Concurrent
# resident-daemon verb handlers calling this for DIFFERENT projects could
# otherwise interleave their set/restore and leave the daemon bound to the
# wrong project, contaminating later ambient config/ledger lookups.
_project_config_lock = threading.Lock()


def detect_legacy_machine(entries: dict[str, MachineEntry], hostname: str) -> str:
    if any(entry.execution_platform for entry in entries.values()):
        from .config import detect_platform

        entry = find_machine_entry(
            entries, hostname, reject_ambiguous=True, execution_platform=detect_platform(),
        )
        if entry is not None and entry.legacy_key:
            return entry.key
        if not any(item.execution_platform and not item.legacy_key for item in entries.values()):
            return machine_name(entry) if entry is not None else hostname
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


def canonicalize_legacy_config(config: Any, *, configured: bool = False) -> Any:
    from . import config as cfg

    repo = config.repos.get(config.repo_name)
    if repo is None or not repo.anchor:
        return config
    try:
        entries = cfg.load_machines_yaml(repo.anchor, config=config)
    except FileNotFoundError:
        return config
    selected = resolve_machine_selection(entries, config.machine, cfg.detect_platform())
    entry = selected.entry
    if (
        configured and entry is not None and not entry.execution_platform
        and cfg.detect_platform() != "wsl"
        and not split_machine_environment(config.machine).environment
    ):
        return config
    if entry is not None and (configured or entry.legacy_key):
        return replace(config, machine=entry.key)
    if configured and not entries and selected.target.environment:
        return replace(config, machine=selected.target.logical_machine)
    return config


def resolve_owner_record_path(owner_ref: str, config: Any) -> tuple[Path | None, str, str | None]:
    from . import config as cfg, legacy_wsl_record_locality, tracking

    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None or not parsed.is_qualified:
        return (
            None, "",
            f"--owner-ref must be a qualified machine/project/worktree_id ref (got {owner_ref!r})",
        )
    legacy = legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(parsed, config)
    if legacy is not None:
        return legacy, parsed.worktree_id, None
    try:
        local = require_owner_identity(parsed.machine, config)
    except ExecutionSpaceError as exc:
        return None, parsed.worktree_id, str(exc)
    if not local:
        error = (
            "cross-space owner ledger mutation is unsupported"
            if _requires_scoped_identity(registry_for_config(config), config.machine, parsed.machine)
            else None
        )
        return None, parsed.worktree_id, error
    return (
        cfg.project_dir(parsed.project) / "worktrees" / f"{parsed.worktree_id}.yaml",
        parsed.worktree_id, None,
    )


def settle_parent_obligation(record: Any, config: Any, worktree_id: str, *, output: Any) -> None:
    from . import config as cfg, legacy_wsl_record_locality, obligations, paired_claims, tracking

    try:
        owner = record.owner_claim_ref
        if (
            owner is None and getattr(record, "pair_role", None) == "knowledge"
            and getattr(record, "pair_kind", None) == "worktree"
        ):
            owner = record.pair_claim_ref
        if owner is None or not owner.is_qualified:
            return
        require_record_mutation(record, config)
        if owner.machine == config.machine:
            parent_path = cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml"
            legacy_parent = False
        else:
            parent_path = legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(owner, config)
            legacy_parent = parent_path is not None
            if parent_path is None:
                return
        if not parent_path.exists():
            return
        with tracking._RecordLock(parent_path, require_sidecar=True):
            parent = tracking.load_record(parent_path)
            if legacy_parent and legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(
                owner, config,
            ) != parent_path:
                return
            receiving_config = project_record_config(parent)
            require_record_mutation(parent, receiving_config)
            caller = resolve_machine_selection(
                registry_for_config(config), config.machine, cfg.detect_platform(),
            )
            caller_key = caller.entry.key if caller.entry is not None else config.machine
            if not require_owner_identity(caller_key, receiving_config):
                raise ExecutionSpaceError("caller is not local to the parent receiving execution space")
            child_ref = tracking.format_claim_ref(config.machine, config.repo_name, worktree_id)
            if child_ref == paired_claims.paired_ref(parent) and any(
                c.ref == child_ref and c.kind == "worktree" and c.state == obligations.RELEASED
                for c in parent.resources
            ):
                return
            settled = tracking.settle_resource_claim(
                parent, child_ref, obligations.AT_REST, save=False,
            )
            if settled is None:
                current_child = tracking.parse_claim_ref(child_ref)
                for claim in parent.resources:
                    if claim.kind == "worktree" and legacy_wsl_record_locality.claim_refs_equivalent(
                        tracking.parse_claim_ref(claim.ref), current_child, config=config,
                    ):
                        settled = tracking.settle_resource_claim(
                            parent, claim.ref, obligations.AT_REST, save=False,
                        )
                        break
            if settled is not None:
                tracking.save_record(parent, parent_path)
        if settled is not None:
            output.ok(
                f"Settled parent {owner.worktree_id}'s claim on this worktree (-> at-rest)"
            )
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
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
        return cfg.load_machines_yaml(anchor, config=config)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ExecutionSpaceError(f"execution-space registry is invalid: {exc}") from exc


def _requires_scoped_identity(entries: dict[str, MachineEntry], *names: str) -> bool:
    if any(entry.execution_platform and not entry.legacy_key for entry in entries.values()):
        return True
    try:
        for name in names:
            entry = find_machine_entry(entries, name, reject_ambiguous=True)
            if entry is not None and entry.execution_platform:
                return True
        return False
    except ValueError as exc:
        raise ExecutionSpaceError(str(exc)) from exc


def _require_registered_current_space(config: Any, current: MachineEntry | None) -> str:
    if current is None or not current.execution_platform or config.machine != current.key:
        raise ExecutionSpaceError(
            "owned mutation requires the current registered execution-space key "
            "in machine configuration"
        )
    from .config import detect_platform

    platform = detect_platform()
    if current.execution_platform != platform or getattr(config, "platform", None) != platform:
        raise ExecutionSpaceError(
            "configured execution-space key and platform must match the current execution platform"
        )
    return platform


def require_owner_identity(owner: str, config: Any, *, canonical_ref: bool = True) -> bool:
    """Return whether an owner is local; reject ambiguous legacy scope names."""
    entries = registry_for_config(config)
    try:
        current = find_machine_entry(entries, config.machine)
        target = find_machine_entry(entries, owner)
    except ValueError as exc:
        raise ExecutionSpaceError(str(exc)) from exc
    explicit = _requires_scoped_identity(entries, config.machine, owner)
    if explicit:
        _require_registered_current_space(config, current)
        if target is None or not target.execution_platform or (canonical_ref and owner != target.key):
            raise ExecutionSpaceError(
                f"legacy owner identity {owner!r} is not an explicit execution-space key"
            )
        return target.key == current.key
    if current is not None and target is not None:
        return target is current
    return owner == config.machine


def require_current_execution_space(config: Any) -> None:
    if _requires_scoped_identity(registry_for_config(config), config.machine):
        require_owner_identity(config.machine, config)


def require_record_mutation(record: Any, config: Any) -> None:
    from . import record_machine_identity, tracking

    if record is None:
        return
    require_current_execution_space(config)
    entries = registry_for_config(config)
    from .legacy_wsl_record_locality import resolve_legacy_wsl_owner_ref

    identity = record_machine_identity.machine_identity(record)
    if identity is not None:
        from .config import detect_platform, project_dir

        try:
            selected = resolve_machine_selection(entries, identity.execution_machine, identity.environment)
        except ValueError as exc:
            raise ExecutionSpaceError(str(exc)) from exc
        if (
            selected.entry is None
            or selected.target.machine != identity.machine
            or selected.target.environment != identity.environment
            or selected.entry.key != identity.execution_machine
        ):
            raise ExecutionSpaceError("record tuple conflicts with its registered execution identity")
        if (
            identity.environment != detect_platform()
            or identity.environment != config.platform
            or not require_owner_identity(identity.execution_machine, config)
        ):
            raise ExecutionSpaceError("record belongs to a different execution space/platform")
        if record.repo != config.repo_name:
            raise ExecutionSpaceError("record does not belong to the receiving project")
        path = project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
        if path.exists():
            proven = tracking.load_record(path)
            if (
                record_machine_identity.identity_fence(record) != record_machine_identity.identity_fence(proven)
                or any(
                    getattr(record, field) != getattr(proven, field)
                    for field in ("repo", "worktree_id", "worktree_path")
                )
            ):
                raise ExecutionSpaceError("record snapshot identity/platform does not match its proven ledger")
        if record.owner_ref:
            owner = tracking.parse_claim_ref(record.owner_ref)
            if owner is None or not owner.is_qualified:
                raise ExecutionSpaceError("owned record has an unqualified owner reference")
            if not require_owner_identity(owner.machine, config):
                raise ExecutionSpaceError(
                    "cross-space parent ownership remains unsupported; settlement is blocked"
                )
        return
    legacy_path = None
    if (
        getattr(record, "repo", "") and getattr(record, "worktree_id", "")
        and getattr(record, "platform", "") in ("wsl", "windows", "linux")
    ):
        legacy_ref = tracking.parse_claim_ref(
            tracking.format_claim_ref(record.machine, record.repo, record.worktree_id),
        )
        legacy_path = resolve_legacy_wsl_owner_ref(legacy_ref, config)
    if legacy_path is not None:
        proven = tracking.load_record(legacy_path)
        if any(
            getattr(record, field, None) != getattr(proven, field, None)
            for field in ("repo", "worktree_id", "machine", "platform", "worktree_path")
        ):
            raise ExecutionSpaceError("record snapshot identity/platform does not match its proven ledger")
        if record.owner_ref and any(entry.execution_platform for entry in entries.values()):
            owner = tracking.parse_claim_ref(record.owner_ref)
            if owner is None or not owner.is_qualified:
                raise ExecutionSpaceError("owned record has an unqualified owner reference")
            if (
                resolve_legacy_wsl_owner_ref(owner, config) is None
                and not require_owner_identity(owner.machine, config)
            ):
                raise ExecutionSpaceError(
                    "cross-space parent ownership remains unsupported; settlement is blocked"
                )
        return
    if not _requires_scoped_identity(entries, config.machine, record.machine):
        return
    if not require_owner_identity(record.machine, config):
        raise ExecutionSpaceError(
            f"record {record.worktree_id!r} belongs to a different execution space"
        )
    if getattr(record, "platform", None) != config.platform:
        raise ExecutionSpaceError(
            f"record {record.worktree_id!r} has a different or unproven execution platform"
        )
    if record.owner_ref:
        owner = tracking.parse_claim_ref(record.owner_ref)
        if owner is None or not owner.is_qualified:
            raise ExecutionSpaceError("owned record has an unqualified owner reference")
        if not require_owner_identity(owner.machine, config):
            raise ExecutionSpaceError(
                "cross-space parent ownership remains unsupported; settlement is blocked"
            )


def project_record_config(record: Any) -> Any:
    """Resolve registered receiving-side configuration under the shared fence."""
    from . import config as cfg

    try:
        with _project_config_lock:
            config = cfg.load_project_config(
                record.repo, include_control_plane_related_pr=False,
            )
        if record.repo not in config.repos:
            raise ValueError("requested project is not registered in receiving-side configuration")
        config = replace(config, repo_name=record.repo)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ExecutionSpaceError(
            f"cannot establish receiving-side authority for project {record.repo!r}: {exc}"
        ) from exc
    return config


def require_project_record_mutation(record: Any) -> None:
    """Resolve receiving-side project authority, never caller-supplied identity."""
    require_record_mutation(record, project_record_config(record))


def require_cleanup_identity(record: Any, anchor: str | Path) -> None:
    """Require tuple authority before considering legacy registry fallbacks.

    Resolves authority for ``record.repo`` (receiving-side), never ambient
    ``cfg.load_config()``: the caller's own active project can differ from
    the record being cleaned, and an ambient config pointing at an
    unscoped/legacy registry would make ``require_record_mutation``
    silently no-op, letting a foreign record be reaped.
    """
    from . import config as cfg, record_machine_identity

    if record_machine_identity.machine_identity(record) is not None:
        require_project_record_mutation(record)
        return

    try:
        entries = cfg.load_machines_yaml(anchor)
    except FileNotFoundError:
        return
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ExecutionSpaceError(f"execution-space registry is invalid: {exc}") from exc
    if not any(entry.execution_platform for entry in entries.values()):
        return
    require_project_record_mutation(record)
