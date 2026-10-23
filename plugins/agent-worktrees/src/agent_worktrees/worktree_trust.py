"""Folder trust at admitted, receiving-side worktree entry boundaries."""

from __future__ import annotations

from . import config_authority, permissions, tracking
from .config import Config
from .execution_spaces import require_record_mutation


def ensure_record_trusted(record: tracking.WorktreeRecord, config: Config | None = None) -> bool:
    """Trust only an admitted native checkout, never a path being inspected."""
    if config is None:
        config_authority.require_registration_record_mutation(record)
    else:
        require_record_mutation(record, config)
    if record.kind in tracking.MANAGED_KINDS and record.status in {
        "finalizing", "complete", "completed", "finalized", "archived",
    }:
        raise permissions.FolderTrustError("Refusing folder trust for a terminal managed worktree")
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
