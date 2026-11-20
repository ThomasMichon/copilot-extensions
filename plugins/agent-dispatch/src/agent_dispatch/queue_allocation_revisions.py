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
RETIRED_WITHOUT_WORKSPACE = (
    "COALESCE((s.driver IS 'ordinary-bridge' AND s.task_relationship_required=1"
    " AND s.task_relationship_generation IS NOT NULL AND s.conversation_retired=0"
    " AND EXISTS (SELECT 1 FROM pool_reservation_bindings p WHERE p.reservation_key=s.key)"
    " AND EXISTS (SELECT 1 FROM spawn_reservations o WHERE o.key=s.allocation_reservation_key"
    " AND o.key=o.allocation_reservation_key AND o.task_id=s.task_id AND o.creation_key=s.creation_key"
    " AND o.allocation_revision=s.allocation_revision)"
    " AND s.creation_key IS NOT NULL AND s.startup_fenced=1 AND s.startup_complete=0"
    " AND s.release_requested=1 AND s.native_release_pending=0 AND s.state IN ('failed','settled','rearmed')"
    " AND s.worktree IS NULL AND s.worktree_path IS NULL"
    " AND s.inherited_worktree IS NULL AND s.creating_host IS NULL AND s.worktree_ownership IS NULL"
    " AND s.creation_nonce IS NULL AND s.worktree_writer_requirement IS NULL"
    " AND s.task_relationship_event_id IS NULL AND s.agent_info IS NULL AND s.session_handle IS NULL"
    " AND NOT EXISTS (SELECT 1 FROM acquisition_receipts a WHERE a.reservation_key=s.key)"
    " AND NOT EXISTS (SELECT 1 FROM acquisition_markers a WHERE a.reservation_key=s.key)"
    " AND NOT EXISTS (SELECT 1 FROM execution_send_custody c WHERE c.reservation_key=s.key AND c.state!='not_sent')),0)"
)
RETIRED_BEFORE_ALLOCATION = f"({RETIRED_WITHOUT_WORKSPACE} AND s.allocation_project IS NULL)"


def _disposed_captured_revision(conn: sqlite3.Connection, rows: list[sqlite3.Row]) -> bool:
    """Disposal certifies a workspace, not a native, claim or transport."""
    from .managed_assignment_proof import managed_allocation
    from .ordinary_agent_facts import identity
    from .ordinary_native_bindings import reservation_native_identity
    from .queue_execution_custody import reservation_transport_accountable
    from .queue_relationship_continuation import current_binding

    if not revision_disposed(rows):
        return False
    root = next((r for r in rows if r["key"] == r["allocation_reservation_key"]), None)
    workspace = next((r for r in rows if r["worktree"]), None)
    if root is None or workspace is None or not root["ordinary_admission_policy"]:
        return False
    event_id = workspace["task_relationship_event_id"]
    event = conn.execute(
        "SELECT task_relationship FROM task_events WHERE task_id=? AND id=?",
        (root["task_id"], event_id),
    ).fetchone()
    if event is None or not event[0]:
        return False
    try:
        relationship = json.loads(event[0])
        if relationship["state"] != "closed":
            return False
        receipt = relationship["receipt"]
        proof = receipt["producer"]["closeout"]["relationship_verification"]
        if (
            proof["retired"] is not True
            or proof["quiescent"] is not True
            or proof["subject"] != receipt["ref"]
        ):
            return False
        execution = conn.execute(
            "SELECT * FROM spawn_reservations WHERE task_id=? AND key=?",
            (root["task_id"], current_binding(relationship)["reservation_key"]),
        ).fetchone()
        if execution is None:
            return False
        origin = managed_allocation(conn, root["task_id"], execution, relationship)
        if origin["key"] != root["key"] or not execution["worktree_writer_requirement"]:
            return False
        for row in rows:
            if (
                row["driver"] != "ordinary-bridge"
                or row["ordinary_admission_policy"] != root["ordinary_admission_policy"]
                or not row["task_relationship_required"]
                or not row["release_requested"]
                or row["state"] not in {"settled", "failed", "rearmed"}
                or row["native_release_pending"]
                or not (row["startup_complete"] or row["startup_fenced"])
                or row["cooling_token"]
                or row["cleanup_claim_token"]
                or row["conclusion_state"] in {"pending", "held"}
                or reservation_transport_accountable(conn, row)
            ):
                return False
            if not row["worktree"]:
                # A project-bound root is not empty by inference: managed_allocation
                # verified its later prepared receipt on this exact creation key.
                if row["allocation_project"] is not None and (
                    row["key"] != origin["key"]
                    or row["allocation_project"] != origin["allocation_project"]
                ):
                    return False
                if not conn.execute(
                    "SELECT 1 FROM spawn_reservations s WHERE key=? AND "
                    + RETIRED_WITHOUT_WORKSPACE,
                    (row["key"],),
                ).fetchone():
                    return False
                continue
            if (
                row["task_relationship_event_id"] != event_id
                or row["worktree_ownership"] not in {"created", "reused"}
                or row["worktree_ownership"] == "reused"
                and row["inherited_worktree"] != row["worktree"]
                or row["worktree_writer_requirement"] != execution["worktree_writer_requirement"]
                or any(
                    row[k] != origin[k]
                    for k in (
                        "task_id",
                        "reserved_by",
                        "creation_key",
                        "allocation_revision",
                        "worktree",
                        "worktree_path",
                        "allocation_project",
                        "execution_machine",
                        "creation_nonce",
                    )
                )
            ):
                return False
            native = reservation_native_identity(conn, row)
            if row["agent_info"] or row["session_handle"] or native:
                if not row["agent_info"]:
                    return False
                agent = identity(json.loads(row["agent_info"]))
                if (
                    agent.native != native
                    or row["session_handle"] != f"local-body:{agent.bridge}"
                    or (agent.project, agent.worktree) != (row["allocation_project"], row["worktree"])
                    or agent.path is not None and agent.path != row["worktree_path"]
                ):
                    return False
    except (TaskError, TypeError, ValueError, KeyError):
        return False
    return True


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
        # revision is empty or positively disposed. The normal send/wake gate still runs.
        qualified = conn.execute(
            f"""SELECT NOT EXISTS (SELECT 1 FROM spawn_reservations s WHERE s.creation_key=:creation
                                 AND NOT {RETIRED_BEFORE_ALLOCATION}) AS never_allocated
                FROM tasks n WHERE n.id=:successor AND n.dedup_key IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM spawn_reservations s LEFT JOIN tasks t ON t.id=s.task_id
                WHERE (s.creation_key=:creation OR s.allocation_reservation_key=:root)
                  AND (s.task_id IS NOT :task OR s.allocation_revision IS NOT :revision
                       OR s.creation_key IS NOT :creation OR s.allocation_reservation_key IS NOT :root
                       OR t.id IS NULL OR t.status NOT IN ('completed','abandoned') OR t.repo IS NOT n.repo
                       OR t.dedup_key IS NOT n.dedup_key)
            )""",
            {
                "successor": successor,
                "creation": previous["creation_key"],
                "revision": prior,
                "root": previous["allocation_reservation_key"],
                "task": previous["task_id"],
            },
        ).fetchone()
        if qualified is not None and (
            qualified["never_allocated"]
            or _disposed_captured_revision(
                conn,
                [row for row in history if row["allocation_revision"] == prior],
            )
        ):
            if prior >= 2**63 - 1:
                raise TaskError("allocation revision exhausted")
            revision += 1
    return revision, revision != prior
