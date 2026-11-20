"""Positive disposal evidence for an explicit allocation revision."""

from __future__ import annotations

import json
import sqlite3

from .queue_records import SpawnState, TaskError


def _allocation_disposed(row: sqlite3.Row) -> bool:
    if (
        row["state"] not in SpawnState.RELEASABLE
        or row["conclusion_state"] != "complete"
        or row["native_release_pending"]
    ):
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
        bool(row["worktree"])
        and isinstance(cleanup, dict)
        and cleanup.get("action") in {"removed", "already-removed"}
    )


def revision_disposed(rows: list[sqlite3.Row]) -> bool:
    disposed = [row for row in rows if _allocation_disposed(row)]
    identities = {
        (row["worktree"], row["allocation_project"], row["worktree_path"]) for row in disposed
    }
    keys = {row["creation_key"] for row in disposed if row["creation_key"]}
    return bool(disposed) and all(
        row["state"] not in SpawnState.ACTIVE
        and not row["native_release_pending"]
        and (
            (row["worktree"], row["allocation_project"], row["worktree_path"]) in identities
            if row["worktree"]
            else row["creation_key"] in keys
        )
        for row in rows
    )


# `s` is an ordinary reservation. Preparation binds its project *before* any
# external workspace effect. Merely lacking a receipt after that point is unknown.
RETIRED_BEFORE_ALLOCATION = (
    "COALESCE((s.driver IS 'ordinary-bridge' AND s.task_relationship_required=1"
    " AND s.task_relationship_generation IS NOT NULL AND s.conversation_retired=0"
    " AND EXISTS (SELECT 1 FROM pool_reservation_bindings p WHERE p.reservation_key=s.key)"
    " AND EXISTS (SELECT 1 FROM spawn_reservations o WHERE o.key=s.allocation_reservation_key"
    " AND o.key=o.allocation_reservation_key AND o.task_id=s.task_id AND o.creation_key=s.creation_key"
    " AND o.allocation_revision=s.allocation_revision)"
    " AND s.creation_key IS NOT NULL AND s.startup_fenced=1 AND s.startup_complete=0"
    " AND s.release_requested=1 AND s.native_release_pending=0 AND s.state IN ('failed','settled','rearmed')"
    " AND s.allocation_project IS NULL AND s.worktree IS NULL AND s.worktree_path IS NULL"
    " AND s.inherited_worktree IS NULL AND s.creating_host IS NULL AND s.worktree_ownership IS NULL"
    " AND s.creation_nonce IS NULL AND s.worktree_writer_requirement IS NULL"
    " AND s.task_relationship_event_id IS NULL AND s.agent_info IS NULL AND s.session_handle IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM acquisition_receipts a WHERE a.reservation_key=s.key)"
    " AND NOT EXISTS (SELECT 1 FROM acquisition_markers a WHERE a.reservation_key=s.key)"
    " AND NOT EXISTS (SELECT 1 FROM execution_send_custody c WHERE c.reservation_key=s.key AND c.state!='not_sent')),0)"
)


def next_revision(
    conn: sqlite3.Connection,
    previous: sqlite3.Row | None,
    history: list[sqlite3.Row],
    *,
    requested: int | None,
    successor: str,
    pool_successor: bool,
    creation_prefix: str,
) -> tuple[int, bool]:
    """Keep the original revision unless explicitly disposed or positively never allocated."""
    prior: int = previous["allocation_revision"] if previous is not None else 1
    revision = prior if requested is None else requested
    if revision < prior or (previous is None and revision != 1):
        raise TaskError(
            "allocation revision must retain the current revision or advance it explicitly"
        )
    if revision != prior and not revision_disposed(
        [r for r in history if r["allocation_revision"] == prior]
    ):
        raise TaskError(
            "new allocation revision requires proven disposal of the previous workspace"
        )
    if (
        requested is None
        and pool_successor
        and previous is not None
        and previous["task_id"] != successor
        and previous["task_relationship_required"]
        and previous["creation_key"] == creation_prefix + str(prior)
    ):
        # Only a different terminal emitter task, and only if the entire keyed
        # revision ended before allocation. The normal send/wake gate still runs.
        qualified = conn.execute(
            f"""SELECT 1 FROM tasks n WHERE n.id=:successor AND n.dedup_key IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM spawn_reservations s LEFT JOIN tasks t ON t.id=s.task_id
                WHERE (s.creation_key=:creation OR s.allocation_reservation_key=:root)
                  AND (s.task_id IS NOT :task OR s.allocation_revision IS NOT :revision
                       OR s.creation_key IS NOT :creation OR s.allocation_reservation_key IS NOT :root
                       OR t.id IS NULL OR t.status NOT IN ('completed','abandoned') OR t.repo IS NOT n.repo
                       OR t.dedup_key IS NOT n.dedup_key
                       OR NOT {RETIRED_BEFORE_ALLOCATION})
            )""",
            {
                "successor": successor,
                "creation": previous["creation_key"],
                "revision": prior,
                "root": previous["allocation_reservation_key"],
                "task": previous["task_id"],
            },
        ).fetchone()
        if qualified is not None:
            if prior >= 2**63 - 1:
                raise TaskError("allocation revision exhausted")
            revision += 1
    return revision, revision != prior
