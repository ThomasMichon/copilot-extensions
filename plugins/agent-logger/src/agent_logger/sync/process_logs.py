"""The ordinary active-target log publication followed by optional retirement."""

from __future__ import annotations

import sys

from agent_logger.config import Config
from agent_logger.sync.log_archive_cli import snapshot_process_logs
from agent_logger.sync.log_reclamation_runtime import LogReclamationPass, report_reclamation
from agent_logger.sync.targets.base import Target


def sync_process_logs(
    cfg: Config, target: Target, machine: str, include: set[str] | None,
    reclamation: LogReclamationPass | None,
) -> bool:
    """Called only inside the existing run_sync lock after session publication."""
    if not cfg.process_logs_enabled:
        if cfg.process_log_archives.enabled:
            print(
                "session-sync: process-log ZIP snapshots skipped "
                "(process-log publication disabled)"
            )
        return False
    if include is not None:
        print("session-sync: process-log sync skipped (repo-scoped sync is not yet supported)")
        return False
    failed = bool(
        cfg.process_log_archives.enabled and snapshot_process_logs(cfg, dry_run=False)
    )
    result = target.push_process_logs(cfg.process_logs_source, machine)
    if not result.ok:
        failed = True
        print(f"session-sync: process-log push failed: {result.detail}", file=sys.stderr)
    else:
        print(f"session-sync: process-logs {result.detail} ({result.file_count} files)")
    if reclamation is not None:
        if failed or not result.process_logs_complete:
            print(
                "session-sync: process-log reclamation skipped "
                "(snapshot/publication incomplete; originals retained)",
                file=sys.stderr,
            )
            return True
        failed = bool(report_reclamation(reclamation, dry_run=False))
    return failed
