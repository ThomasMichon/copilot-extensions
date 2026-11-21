"""Assignment fences evaluated inside the owner's task transition transaction."""

import sqlite3


def retains_assignment(conn: sqlite3.Connection, task_id: str) -> bool:
    if not conn.in_transaction:
        raise ValueError("reservation assignment requires a transaction")
    return conn.execute(
        "SELECT 1 FROM spawn_reservations WHERE task_id=? AND ("
        "state IN ('reserving','spawned','cold','releasing') OR native_release_pending=1"
        " OR conclusion_state IN ('pending','held')"
        " OR (worktree_ownership='created' AND worktree IS NOT NULL"
        " AND COALESCE(conclusion_state,'') NOT IN ('complete','skipped'))"
        " OR ((session_handle IS NOT NULL OR agent_info IS NOT NULL)"
        " AND conversation_retired=0 AND release_requested=0)) LIMIT 1", (task_id,),
    ).fetchone() is not None
