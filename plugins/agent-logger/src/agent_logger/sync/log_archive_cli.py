"""Command outcomes for non-retiring process-log ZIP snapshots."""

from __future__ import annotations

import sys

from agent_logger.config import Config
from agent_logger.sync.automation import automation_disabled
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.log_archives import archive_settled_process_logs


def snapshot_process_logs(cfg: Config, *, dry_run: bool) -> int:
    """Run additive snapshots while the caller owns the existing sync lock."""
    try:
        result = archive_settled_process_logs(
            cfg.process_logs_source,
            min_quiet_seconds=cfg.process_log_archives.min_quiet_seconds,
            dry_run=dry_run,
        )
    except (OSError, ValueError) as error:
        print(f"session-sync: process-log ZIP snapshot failed: {error}", file=sys.stderr)
        return 1
    action = "would archive" if dry_run else "archived"
    print(
        f"session-sync: process-log ZIP {action} {result.archived}; "
        f"{result.unchanged} unchanged, {result.skipped_recent} recent, "
        f"{result.skipped_active} active, {result.skipped_unclassified} unclassified; "
        "originals retained"
    )
    if result.failed:
        print(
            f"session-sync: {len(result.failed)} process-log ZIP failure(s): "
            f"{'; '.join(result.failed)}",
            file=sys.stderr,
        )
        return 1
    return 0


def run_compact_logs(cfg: Config, *, dry_run: bool) -> int:
    """Manually snapshot local logs without running publication or retirement."""
    if automation_disabled():
        print("session-sync: disabled via AGENT_LOGGER_SYNC_DISABLED")
        return 0
    if not cfg.process_log_archives.enabled:
        print("session-sync: process-log ZIP snapshots disabled")
        return 0
    with sync_lock(
        cfg.home / cfg.sync_lock_name, timeout=cfg.sync_lock_timeout,
    ) as acquired:
        if not acquired:
            print("session-sync: another sync holds the lock; skipping", file=sys.stderr)
            return 1
        return snapshot_process_logs(cfg, dry_run=dry_run)
