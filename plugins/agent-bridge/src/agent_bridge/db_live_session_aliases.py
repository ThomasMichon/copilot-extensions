"""Live-session alias helpers."""

from __future__ import annotations

#: SQL resolving a (possibly retired) live-session id to its current id inside
#: the same statement as a read or write, so a concurrent rename can't slip in
#: between lookup and write. Aliases are kept one hop deep: each rename
#: re-points existing aliases at the newest id. Binds the id twice.
CANONICAL_SESSION_SQL = (
    "COALESCE((SELECT target_session_id FROM live_session_aliases "
    "WHERE alias_session_id = ?), ?)"
)

import json
from typing import Any

from .db_core import LIVE_SESSION_STALE_SECONDS

#: How far two reports of one process's start time may drift. The extension
#: fixes it once per process (wall clock minus uptime), so the same process
#: reports the same value; only a recomputation (an extension reload in the
#: same process) moves it, by milliseconds. A reused pid's process can't have
#: started within this window of the original's: the original registered (and
#: so outlived it) before the pid was free again.
PROCESS_START_TOLERANCE_SECONDS = 0.25


def _newer_turn(predecessor: Any, successor: Any) -> tuple[Any, Any]:
    """The (turn_state, last_activity_at) pair with the latest activity; the
    successor's when only it has one, the predecessor's when only it does."""
    pred_at, succ_at = predecessor["last_activity_at"], successor["last_activity_at"]
    if succ_at is None or (pred_at is not None and pred_at > succ_at):
        if pred_at is not None or successor["turn_state"] is None:
            return predecessor["turn_state"], pred_at
    return successor["turn_state"], succ_at


def _progress_ts(raw: Any) -> float | None:
    try:
        ts = json.loads(raw).get("ts") if raw else None
    except (TypeError, ValueError, AttributeError):
        return None
    return float(ts) if isinstance(ts, (int, float)) else None


def _newer_progress(predecessor: Any, successor: Any) -> Any:
    """The successor's latest_progress unless it has none or the
    predecessor's is provably newer."""
    pred, succ = predecessor["latest_progress"], successor["latest_progress"]
    if succ is None:
        return pred
    pred_ts, succ_ts = _progress_ts(pred), _progress_ts(succ)
    if pred_ts is not None and (succ_ts is None or pred_ts > succ_ts):
        return pred
    return succ


_REGISTER_SQL = (
    "INSERT INTO live_sessions (session_id, machine, cwd, worktree_id, repo, branch, "
    "pid, role, driven_by, venue, process_started_at, status, registered_at, updated_at) "
    f"SELECT {CANONICAL_SESSION_SQL}, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ? "
    "WHERE NOT EXISTS ("
    "  SELECT 1 FROM worktree_ownership wo "
    "  JOIN sessions s ON s.id = wo.session_id "
    "  WHERE wo.worktree_id = ? AND ? IS NOT NULL "
    "    AND s.status IN ('running', 'idle')"
    ") "
    "ON CONFLICT(session_id) DO UPDATE SET "
    # A heartbeat that omits metadata (only the id, while the extension's own
    # metadata resolves) keeps what's known, like pid: never erase targeting.
    "machine=COALESCE(excluded.machine, live_sessions.machine), cwd=COALESCE(excluded.cwd, live_sessions.cwd), "
    "worktree_id=COALESCE(excluded.worktree_id, live_sessions.worktree_id), "
    "repo=COALESCE(excluded.repo, live_sessions.repo), "
    "branch=COALESCE(excluded.branch, live_sessions.branch), pid=COALESCE(excluded.pid, live_sessions.pid), "
    "role=COALESCE(excluded.role, live_sessions.role), "
    "driven_by=COALESCE(excluded.driven_by, live_sessions.driven_by), "
    "venue=COALESCE(excluded.venue, live_sessions.venue), process_started_at="
    "COALESCE(excluded.process_started_at, live_sessions.process_started_at), "
    "status='live', updated_at=excluded.updated_at "
    "WHERE live_sessions.status != 'taken-over' "
    # An id-only heartbeat skips the insert guard above but keeps the row's
    # worktree: the same ownership rule applies to that retained worktree.
    "AND NOT EXISTS ("
    "  SELECT 1 FROM worktree_ownership wo "
    "  JOIN sessions s ON s.id = wo.session_id "
    "  WHERE wo.worktree_id = COALESCE(excluded.worktree_id, live_sessions.worktree_id) "
    "    AND s.status IN ('running', 'idle')"
    ")"
)


def _incarnation_mismatch(
    conn: Any, session_id: str, *, machine: str | None, pid: int | None,
    process_started_at: float | None, aliased: bool,
) -> bool:
    """Whether a registration's own identity contradicts the canonical row it
    would update (inside the caller's transaction, after alias resolution).

    Another process (pid, or a known start time outside the tolerance) never
    updates a row. Through an alias -- a heartbeat for a renamed id, landing
    on its successor's row -- another machine doesn't either (a direct
    re-registration may still move machines, as before). Omitted fields never
    conflict -- an id-only heartbeat keeps the row's metadata -- and a
    taken-over row is left to the write's own rejection."""
    row = conn.execute(
        "SELECT machine, pid, process_started_at, status FROM live_sessions WHERE session_id=?",
        (session_id,),
    ).fetchone()
    if row is None or (row["status"] or "live") == "taken-over":
        return False
    if (aliased and machine and row["machine"]
            and machine.casefold() != str(row["machine"]).casefold()):
        return True
    if pid is not None and row["pid"] is not None and int(pid) != int(row["pid"]):
        return True
    known = row["process_started_at"]
    return (process_started_at is not None and known is not None
            and abs(float(process_started_at) - float(known)) >= PROCESS_START_TOLERANCE_SECONDS)


def register_live_session_atomic(
    db: Any, session_id: str, *, machine: str | None, cwd: str | None,
    worktree_id: str | None, repo: str | None, branch: str | None, pid: int | None,
    role: str | None, now: float, driven_by: str | None, venue: str | None,
    process_started_at: float | None,
) -> str:
    """Upsert a registration, claim a pending CLI-mode reservation and fold in a
    same-process predecessor in **one** write transaction, so a concurrent
    deregistration (another connection) can't land between them and strand the
    successor without its claim, venue or the predecessor's handle. Returns
    ``'live'``, ``'taken-over'`` or ``'reserved'``
    (see ``register_live_session``)."""
    conn = db._get_conn()
    with db._write_lock:
        conn.execute("BEGIN IMMEDIATE")
        try:
            requested = session_id
            session_id = db.resolve_live_session_id(session_id)
            mismatch = _incarnation_mismatch(
                conn, session_id, machine=machine, pid=pid, process_started_at=process_started_at,
                aliased=session_id != requested)
            if mismatch:
                conn.rollback()
                return "incarnation_mismatch"
            cur = conn.execute(
                _REGISTER_SQL,
                (session_id, session_id, machine, cwd, worktree_id, repo, branch, pid,
                 role, driven_by, venue, process_started_at, now, now, worktree_id,
                 worktree_id),
            )
            session_id = db.resolve_live_session_id(session_id)  # the id actually written
            if cur.rowcount != 1:
                # The 0-row write was the authoritative rejection; derive why.
                existing = conn.execute(
                    "SELECT status FROM live_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
                conn.rollback()
                taken = existing is not None and (existing["status"] or "live") == "taken-over"
                return "taken-over" if taken else "reserved"
            if worktree_id is not None:
                # Bookkeeping, never an admission gate: claim a pending CLI-mode
                # reservation (its trusted venue too), then fold in a same-process
                # predecessor (a resume may rename mid-rejoin).
                if conn.execute(
                    "UPDATE cli_mode_reservations SET claimed_by_session_id=? "
                    "WHERE worktree_id=? AND claimed_by_session_id IS NULL AND expires_at > ?",
                    (session_id, worktree_id, now),
                ).rowcount == 1:
                    conn.execute(
                        "UPDATE live_sessions SET cli_mode=1, venue=COALESCE("
                        "(SELECT venue FROM cli_mode_reservations "
                        " WHERE worktree_id=? AND claimed_by_session_id=?), venue) "
                        "WHERE session_id=?",
                        (worktree_id, session_id, session_id),
                    )
                _fold_in_predecessor(conn, worktree_id, session_id, now=now)
            conn.commit()
            return "live"
        except Exception:
            conn.rollback()
            raise


def _fold_in_predecessor(
    conn: Any, worktree_id: str, session_id: str, *, now: float
) -> str | None:
    """Move a CLI-mode session's handle and claim to ``session_id`` when the
    same process re-registers under a new conversation id (a resume). Runs
    inside the caller's write transaction.

    The predecessor is found from durable live registrations, not the launch
    reservation (which a launcher may already have released, or which may have
    expired): same worktree and *known* machine (two unknown machines never
    match), ``cli_mode``, and the *same, known*
    PID. A missing PID on either side never counts as a match. A PID alone
    does not prove the same process (it can be reused), so when both rows
    carry ``process_started_at`` those must agree. A legacy predecessor
    without one must have registered after this process started (else an
    earlier holder of the pid registered it) and still be heartbeating (fresh
    ``live``) or confirmed alive (``wedged``) -- never a lapsed or
    confirmed-dead registration. When only the predecessor has a start time,
    identity can't be established and nothing is folded in; when neither
    does (older extensions), the fresh-or-wedged lease is the only evidence.
    """
    successor = conn.execute(
        "SELECT * FROM live_sessions WHERE session_id=? "
        "AND worktree_id=?",
        (session_id, worktree_id),
    ).fetchone()
    if successor is None or successor["pid"] is None or not successor["machine"]:
        return None
    started = successor["process_started_at"]
    predecessor = conn.execute(
        "SELECT * FROM live_sessions WHERE worktree_id=? "
        "AND session_id != ? AND cli_mode=1 AND pid IS NOT NULL "
        "AND status != 'taken-over' "
        # Never fold back the session this id was already renamed into.
        "AND session_id NOT IN (SELECT target_session_id FROM live_session_aliases "
        "WHERE alias_session_id=?) "
        "AND pid=? AND machine = ? AND CASE "
        "WHEN ? IS NOT NULL AND process_started_at IS NOT NULL "
        "THEN ABS(process_started_at - ?) < ? "
        # A legacy predecessor (no start time): the same process registered it
        # after starting, so a registration from before this process started
        # came from an earlier holder of the pid. A fresh lease alone is not
        # process identity.
        "WHEN ? IS NOT NULL "
        "THEN registered_at >= ? - ? AND (status='wedged' OR (status='live' AND updated_at >= ?)) "
        # Only the predecessor has a start time: mixed evidence, so identity
        # can't be established.
        "WHEN process_started_at IS NOT NULL THEN 0 "
        # Neither side reports one (older extensions): the lease is all there is.
        "ELSE status='wedged' OR (status='live' AND updated_at >= ?) END "
        "ORDER BY updated_at DESC LIMIT 1",
        (worktree_id, session_id, session_id, successor["pid"], successor["machine"],
         started, started, PROCESS_START_TOLERANCE_SECONDS,
         started, started, PROCESS_START_TOLERANCE_SECONDS,
         now - LIVE_SESSION_STALE_SECONDS, now - LIVE_SESSION_STALE_SECONDS),
    ).fetchone()
    if predecessor is None:
        return None
    predecessor_id = predecessor["session_id"]
    # A reservation this registration just claimed (a rejoin) is the
    # current venue; otherwise the one the predecessor holds.
    reservation = conn.execute(
        "SELECT venue FROM cli_mode_reservations "
        "WHERE worktree_id=? AND claimed_by_session_id IN (?, ?) "
        "ORDER BY claimed_by_session_id=? DESC LIMIT 1",
        (worktree_id, session_id, predecessor_id, session_id),
    ).fetchone()
    venue = (
        (reservation["venue"] if reservation is not None else None)
        or predecessor["venue"] or successor["venue"]
    )
    driven_by = successor["driven_by"] or predecessor["driven_by"]
    conn.execute(
        "UPDATE cli_mode_reservations SET claimed_by_session_id=? "
        "WHERE worktree_id=? AND claimed_by_session_id=?",
        (session_id, worktree_id, predecessor_id),
    )
    # Same process, so it keeps its place in the worktree's registration
    # order: a newer process that superseded it stays current.
    turn_state, last_activity_at = _newer_turn(predecessor, successor)
    conn.execute(
        "UPDATE live_sessions SET cli_mode=1, venue=?, driven_by=?, "
        "registered_at=?, updated_at=?, turn_state=?, last_activity_at=?, "
        "latest_progress=? WHERE session_id=?",
        (venue, driven_by, predecessor["registered_at"], now, turn_state,
         last_activity_at, _newer_progress(predecessor, successor), session_id),
    )
    conn.execute(
        "INSERT INTO live_session_aliases "
        "(alias_session_id, target_session_id, created_at) "
        "VALUES (?, ?, ?) "
        "ON CONFLICT(alias_session_id) DO UPDATE SET "
        "target_session_id=excluded.target_session_id, "
        "created_at=excluded.created_at",
        (predecessor_id, session_id, now),
    )
    conn.execute(
        "UPDATE live_session_aliases SET target_session_id=? "
        "WHERE target_session_id=?",
        (session_id, predecessor_id),
    )
    conn.execute(
        "UPDATE live_messages SET session_id=? WHERE session_id=?",
        (session_id, predecessor_id),
    )
    conn.execute(
        "DELETE FROM live_sessions WHERE session_id=?",
        (predecessor_id,),
    )
    return predecessor_id
