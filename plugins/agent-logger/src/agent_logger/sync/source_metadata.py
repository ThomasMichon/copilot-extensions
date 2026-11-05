"""Origin and worktree sidecars prepared by the existing session sync pass."""

from __future__ import annotations

from pathlib import Path

from agent_logger.sync.origin import mark_all
from agent_logger.sync.worktree_binding import mark_all_worktrees


def mark_source(
    source: Path,
    machine: str,
    effective: list[str],
    *,
    dry_run: bool,
    verbose: bool,
) -> None:
    """Mark origins first, then resolve each session's worktree from that sidecar."""
    origin_summary = mark_all(source, machine, effective, dry_run=dry_run)
    if verbose:
        print(
            f"origin:    marked {origin_summary['marked']}/"
            f"{origin_summary['total']} session(s) {origin_summary['by_repo']}"
        )

    worktree_summary = mark_all_worktrees(source, dry_run=dry_run)
    if verbose:
        print(
            f"worktree:  marked {worktree_summary['marked']}/"
            f"{worktree_summary['total']} session(s) "
            f"({worktree_summary['unresolved']} unresolved)"
        )
