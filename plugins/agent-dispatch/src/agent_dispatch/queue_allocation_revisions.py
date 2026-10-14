"""Positive disposal evidence for an explicit allocation revision."""

from __future__ import annotations

import json
import sqlite3

from .queue_records import SpawnState


def _allocation_disposed(row: sqlite3.Row) -> bool:
    if row["state"] not in SpawnState.RELEASABLE or row["conclusion_state"] != "complete":
        return False
    try:
        detail = json.loads(row["conclusion_detail"] or "{}")
    except (ValueError, TypeError):
        return False
    if not isinstance(detail, dict):
        return False
    session_end = detail.get("session_end")
    if session_end is not None and (
        not isinstance(session_end, dict) or session_end.get("state") != "complete"
    ):
        return False
    cleanup = detail.get("worktree_cleanup")
    if cleanup is not None:
        if not isinstance(cleanup, dict) or cleanup.get("state") != "complete":
            return False
        cleanup = cleanup.get("outcome", cleanup)
    else:
        cleanup = detail
    return (
        bool(row["worktree"]) and isinstance(cleanup, dict)
        and cleanup.get("action") in {"removed", "already-removed"}
    )


def revision_disposed(rows: list[sqlite3.Row]) -> bool:
    disposed = [row for row in rows if _allocation_disposed(row)]
    identities = {
        (row["worktree"], row["allocation_project"], row["worktree_path"]) for row in disposed
    }
    keys = {row["creation_key"] for row in disposed if row["creation_key"]}
    return bool(disposed) and all(
        row["state"] not in SpawnState.ACTIVE and (
            (row["worktree"], row["allocation_project"], row["worktree_path"]) in identities
            if row["worktree"] else row["creation_key"] in keys
        )
        for row in rows
    )
