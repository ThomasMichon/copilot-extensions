"""Folder trust at admitted, receiving-side worktree entry boundaries."""

from __future__ import annotations

from . import config as cfg, config_authority, permissions, tracking
from .config import Config
from .execution_spaces import ExecutionSpaceError, require_owner_identity, require_record_mutation


def ensure_record_trusted(record: tracking.WorktreeRecord, config: Config | None = None) -> bool:
    """Trust only an admitted native checkout, never a path being inspected."""
    if config is None:
        config = config_authority.registration_record_config(record)
    else:
        require_record_mutation(record, config)
    if record.kind in tracking.MANAGED_KINDS and record.status in {
        "finalizing", "complete", "completed", "finalized", "archived",
    }:
        raise permissions.FolderTrustError("Refusing folder trust for a terminal managed worktree")
    actual = cfg.detect_platform()
    if record.platform != actual or config.platform != actual:
        raise ExecutionSpaceError("Folder trust requires the current native execution platform")
    try:
        local = require_owner_identity(record.execution_machine, config)
    except ExecutionSpaceError:
        if record.machine_identity is not None:
            raise
        local = False
    if not local:
        from .legacy_wsl_record_locality import resolve_legacy_wsl_owner_ref

        ref = tracking.parse_claim_ref(
            tracking.format_claim_ref(record.execution_machine, record.repo, record.worktree_id),
        )
        if resolve_legacy_wsl_owner_ref(ref, config) is None:
            raise ExecutionSpaceError("Folder trust requires a local execution-space record")
    return permissions.ensure_trusted_folder(record.worktree_path)


def prepare_for_launch(
    record: tracking.WorktreeRecord, config: Config, *, defer_projections: bool,
) -> None:
    """Establish trust before guidance without coupling it to cache deferral."""
    ensure_record_trusted(record, config)
    if not defer_projections:
        from . import local_cache_refresh

        local_cache_refresh.prepare_for_launch(
            record.worktree_path, compose_pair=True, config=config,
        )
