"""The shared companion-completion tail for new and recovered creations."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import codename_tracking, local_cache_refresh, permissions
from .launch_progress import step

if TYPE_CHECKING:
    from . import config as cfg, tracking


def complete_created_pair(
    config: cfg.Config, record: tracking.WorktreeRecord, *,
    timestamp: str, suffix: str, no_pair: bool,
    carve: Callable[..., dict | None], stamp: Callable[..., dict | None],
) -> bool:
    required_pair = False
    if record.receiving_allocation_present:
        from .receiving_creation_recovery import requires_pair

        required_pair = requires_pair(record, config)
    if record.kind != "session" or no_pair and not required_pair:
        return True
    composition_ready = True
    try:
        with step("Creating paired worktree", worktree_id=record.worktree_id):
            pair_stamp = carve(
                config, harness_id=record.worktree_id, timestamp=timestamp,
                suffix=suffix, plat=record.platform,
                plat_short="win" if record.platform == "windows" else record.platform,
                **({"machine_identity": record.machine_identity} if record.machine_identity is not None else {}),
            )
    except (codename_tracking.CodenameAttributionPolicyError, permissions.FolderTrustError):
        raise
    except Exception as exc:  # pragma: no cover - existing fail-safe pairing policy
        print(f"paired-knowledge carve failed (non-fatal): {exc}", file=sys.stderr)
        pair_stamp = None
        composition_ready = False
    composition = stamp(config, record, record.worktree_path, pair_stamp)
    if required_pair and (not pair_stamp or not composition):
        composition_ready = False
    if not composition_ready or composition and (
        composition.get("action") == "error" or composition.get("pair_error")
    ):
        local_cache_refresh.mark_pair_pending(record.worktree_path)
        if record.receiving_allocation_present:
            raise RuntimeError("receiving allocation pairing remains incomplete; recover the original child")
        return False
    return True
