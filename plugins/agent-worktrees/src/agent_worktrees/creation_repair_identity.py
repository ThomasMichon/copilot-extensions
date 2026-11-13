"""Naming checks inside independently verified, retained creation-tail repair."""

from __future__ import annotations

from pathlib import Path

from machine_transport import resolve_local_execution_context

from . import config as cfg, creation_identity, execution_spaces, state_root, tracking
from . import repos
from .tracking_claims import ANCHOR_ID
from .worktree_machine_identity import WorktreeMachineIdentity


def _repair_namespace_matches(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    """Preserve equivalent stored path spelling inside the exact receiving root."""
    repo = config.repos.get(record.repo)
    if repo is None:
        return False
    checkout = Path(record.worktree_path)
    anchor = record.worktree_id == ANCHOR_ID
    root = Path(repo.anchor if anchor else repo.worktree_root).expanduser()
    if not root.is_absolute() or not checkout.is_absolute() or checkout.is_symlink():
        return False
    expected = root.resolve() if anchor else root.resolve() / record.worktree_id
    current_namespace = (
        checkout.resolve() == expected
        and (not checkout.exists() or checkout.is_dir())
    )
    if current_namespace or anchor:
        return current_namespace
    from .retained_checkout import matches

    return matches(record, config)


def _observed_original_owner(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    """Recognize only reconstructible OS-derived names, not former custom aliases."""
    entries = execution_spaces.registry_for_config(config)
    context = execution_spaces.current_execution_context(config)
    original = resolve_local_execution_context(
        {}, hostname=context.hostname, environment=context.environment,
    )
    observed = resolve_local_execution_context(
        entries, hostname=context.hostname, environment=context.environment,
        configured_machine=config.configured_machine,
    )
    original_identity = WorktreeMachineIdentity(
        original.machine, original.environment, original.execution_machine,
    )
    derived_record = (
        record.machine_identity == original_identity if record.machine_identity is not None
        else record.machine in {original.hostname.casefold(), original.execution_machine}
    )
    if not (
        derived_record and config.configured_machine is None and context == observed
        and config.platform == record.platform == cfg.detect_platform()
        and config.machine.casefold() in {
            observed.hostname.casefold(), observed.execution_machine.casefold(),
        }
    ):
        return False
    for entry in entries.values():
        if entry.key.casefold() == record.execution_machine.casefold() and (
            entry.execution_platform and entry.execution_platform != record.platform
            or observed.customization_key != entry.key
        ):
            raise ValueError("retained creation owner now selects a different execution space")
    return True


def retained_owner_is_current(owner: str, config: cfg.Config) -> bool:
    """Keep reconstructible observed references usable after a naming customization."""
    try:
        if execution_spaces.require_owner_identity(owner, config, canonical_ref=False):
            return True
    except execution_spaces.ExecutionSpaceError:
        pass
    context = execution_spaces.current_execution_context(config)
    observed = resolve_local_execution_context(
        {}, hostname=context.hostname, environment=context.environment,
    )
    entries = execution_spaces.registry_for_config(config)
    return (
        owner.casefold() == observed.execution_machine.casefold()
        and not any(
            key.casefold() == owner.casefold() and key != context.customization_key
            for key in entries
        )
    )


def require_retained_record(record: tracking.WorktreeRecord, config: cfg.Config) -> None:
    """Called after actual ledger, Git, stopped-session and unclaimed New proof.

    This licenses only the creation tail, never a new tuple or a generic writer.
    The receiving project and qualified parent remain independent authorities.
    """
    if record._reporting_context is not None:
        from .checkout_relocation_state import require_idle

        require_idle(record.yaml_path)
        receiving = execution_spaces.project_record_config(record)
        if (
            not record.creation_nonce
            or not _repair_namespace_matches(record, config)
            or not _repair_namespace_matches(record, receiving)
            or Path(receiving.default_repo.anchor).resolve() != Path(config.default_repo.anchor).resolve()
        ):
            raise ValueError("retained creation has no receiving-project allocation proof")
        for authority in (config, receiving):
            creation_identity.require_same_machine_identity(authority, record.machine_identity)
        if record.owner_ref:
            owner = tracking.parse_claim_ref(record.owner_ref)
            if owner is None or not owner.is_qualified:
                raise ValueError("retained creation has an unqualified parent owner")
            parent_path = cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml"
            if (
                not parent_path.is_file() or parent_path.is_symlink()
                or parent_path.resolve().parent != parent_path.parent.resolve()
            ):
                raise ValueError("retained creation parent owner is not local")
            parent = tracking.load_record(parent_path)
            parent_config = execution_spaces.project_record_config(parent)
            if (
                parent.worktree_id != owner.worktree_id or parent.repo != owner.project
                or parent_config.platform != cfg.detect_platform()
                or not _repair_namespace_matches(parent, parent_config)
                or not retained_owner_is_current(owner.machine, parent_config)
            ):
                raise ValueError("retained creation parent conflicts with its receiving authority")
            creation_identity.require_same_machine_identity(parent_config, parent.machine_identity)
        return
    if not _observed_original_owner(record, config):
        execution_spaces.require_record_mutation(record, config)
        if not execution_spaces.require_owner_identity(record.execution_machine, config, canonical_ref=False):
            raise ValueError("creation repair requires this project's active local session worktree")
        return
    receiving = execution_spaces.project_record_config(record)
    if (
        not record.creation_nonce
        or not _repair_namespace_matches(record, config)
        or not _repair_namespace_matches(record, receiving)
        or Path(receiving.default_repo.anchor).resolve() != Path(config.default_repo.anchor).resolve()
        or not _observed_original_owner(record, receiving)
    ):
        raise ValueError("retained creation has no receiving-project allocation proof")
    if record.owner_ref:
        owner = tracking.parse_claim_ref(record.owner_ref)
        if owner is None or not owner.is_qualified:
            raise ValueError("retained creation has an unqualified parent owner")
        parent_path = cfg.project_dir(owner.project) / "worktrees" / f"{owner.worktree_id}.yaml"
        if (
            not parent_path.is_file() or parent_path.is_symlink()
            or parent_path.resolve().parent != parent_path.parent.resolve()
        ):
            raise ValueError("retained creation parent owner is not local")
        parent = tracking.load_record(parent_path)
        if (
            parent.worktree_id != owner.worktree_id or parent.repo != owner.project
            or parent.execution_machine != owner.machine
        ):
            raise ValueError("retained creation parent owner does not match its ledger")
        parent_config = execution_spaces.project_record_config(parent)
        if (
            parent.platform != cfg.detect_platform() or parent.platform != parent_config.platform
            or not _repair_namespace_matches(parent, parent_config)
        ):
            raise ValueError("retained creation parent conflicts with its receiving platform or namespace")
        observed_parent = _observed_original_owner(parent, parent_config)
        if not (observed_parent and parent.creation_nonce):
            if not execution_spaces.require_owner_identity(owner.machine, parent_config, canonical_ref=False):
                raise ValueError("retained creation parent has a different receiving execution owner")
            if not observed_parent:
                caller_path, _, error = execution_spaces.resolve_owner_record_path(record.owner_ref, receiving)
                if error or caller_path is None or caller_path.resolve() != parent_path.resolve():
                    raise ValueError(error or "retained creation parent owner is not local")
            resolved, _, error = execution_spaces.resolve_owner_record_path(record.owner_ref, parent_config)
            if error or resolved is None or resolved.resolve() != parent_path.resolve():
                raise ValueError(error or "retained creation parent owner is not local")
            execution_spaces.require_record_mutation(parent, parent_config)


def require_companion_receiver(
    record: tracking.WorktreeRecord, root: state_root.StateRoot,
) -> None:
    """Prove the original companion's receiving namespace and local binding anew."""
    receiving = cfg.load_project_config(root.repo, include_control_plane_related_pr=False)
    if (
        receiving.repo_name != root.repo or root.repo not in receiving.repos
        or Path(receiving.default_repo.anchor).resolve() != Path(root.path).resolve()
        or receiving.platform != record.platform or record.platform != cfg.detect_platform()
    ):
        raise ValueError("retained companion conflicts with the receiving project namespace")
    entry = repos.find_repo(root.repo)
    if entry is None:
        raise ValueError("retained companion has no repository registration")
    if repos.normalize_class(entry.repo_class) in {"worktree", "knowledge"}:
        allocation_root = Path(receiving.default_repo.worktree_root).expanduser()
        if not allocation_root.is_absolute() or allocation_root.is_symlink():
            raise ValueError("retained companion has an invalid receiving allocation root")
    if record._reporting_context is not None:
        from .record_reporting import current_context

        context = current_context(root.repo)
        creation_identity.require_same_machine_identity(receiving, WorktreeMachineIdentity(
            context.machine, context.environment, context.execution_machine,
        ))
        return
    if not _observed_original_owner(record, receiving):
        creation_identity.require_same_machine_identity(receiving, record.machine_identity)
        if not execution_spaces.require_owner_identity(record.execution_machine, receiving, canonical_ref=False):
            raise ValueError("retained companion has a different receiving execution owner")
