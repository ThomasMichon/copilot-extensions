"""Folder trust at admitted, receiving-side worktree entry boundaries."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import cast

from . import config as cfg, config_authority, permissions, tracking, tracking_claims
from .config import Config
from .execution_spaces import ExecutionSpaceError, require_owner_identity, require_record_mutation


def ensure_record_trusted(
    record: tracking.WorktreeRecord, config: Config | None = None,
    *, environment: Mapping[str, str] | None = None,
) -> bool:
    """Trust only an admitted native checkout, never a path being inspected."""
    if config is None:
        config = config_authority.registration_record_config(record)
    else:
        require_record_mutation(record, config)
    if record.kind in tracking.MANAGED_KINDS and record.status in {
        "finalizing", "complete", "completed", "finalized", "archived",
    }:
        raise permissions.FolderTrustError("Refusing folder trust for a terminal managed worktree")
    from . import retained_checkout

    launch_environment = os.environ if environment is None else environment
    home = launch_environment.get("COPILOT_HOME", os.environ.get("COPILOT_HOME"))
    copilot_home = Path(home) if home else None
    if copilot_home is not None and not copilot_home.is_absolute():
        copilot_home = Path(record.worktree_path) / copilot_home

    def trust_folder() -> bool:
        if copilot_home is None:
            return permissions.ensure_trusted_folder(record.worktree_path)
        return permissions.ensure_trusted_folder(record.worktree_path, copilot_home=copilot_home)

    if retained_checkout.is_local(record, config):
        return trust_folder()
    actual = cast(Callable[[], str], getattr(cfg, "detect_platform"))()
    if record.platform != actual or config.platform != actual:
        raise ExecutionSpaceError("Folder trust requires the current native execution platform")
    try:
        local = require_owner_identity(record.execution_machine, config)
    except ExecutionSpaceError:
        if record.machine_identity is not None and record._reporting_context is None:
            raise
        local = False
    if not local:
        from .legacy_wsl_record_locality import resolve_legacy_wsl_owner_ref

        parse_ref = cast(Callable[[str], tracking_claims.ClaimRef | None], getattr(tracking, "parse_claim_ref"))
        format_ref = cast(Callable[[str, str, str], str], getattr(tracking, "format_claim_ref"))
        ref = parse_ref(format_ref(record.execution_machine, record.repo, record.worktree_id))
        if ref is None:
            raise ExecutionSpaceError("Folder trust requires a valid local claim identity")
        if resolve_legacy_wsl_owner_ref(ref, config) is None:
            raise ExecutionSpaceError("Folder trust requires a local execution-space record")
    return trust_folder()


def prepare_for_launch(
    record: tracking.WorktreeRecord, config: Config, *, defer_projections: bool,
) -> None:
    """Admit the existing setup without recomposing its paired plugin settings."""
    ensure_record_trusted(record, config)
    from . import local_cache_refresh

    local_cache_refresh.prepare_tool_roster(record.worktree_path, config=config)
