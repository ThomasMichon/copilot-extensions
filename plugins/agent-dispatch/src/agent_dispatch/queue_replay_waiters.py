"""Bounded replay lookups and progress on the existing waiter transaction owner."""

from __future__ import annotations

from .delegated_replay_contract import replay_scope
from .queue_records import TaskError


class ReplayWaiterProgressMixin:
    @staticmethod
    def _check_replay_request(conn, delegation):
        if "replay" in delegation:
            replay = delegation["replay"]
            if conn.execute(
                "SELECT 1 FROM run_waiters WHERE replay_scope = ? AND replay_request_id = ?",
                (replay_scope(replay["binding"]), replay["request_id"]),
            ).fetchone():
                raise TaskError("replay request already bound to a waiter")

    @staticmethod
    def _store_replay_intent(conn, delegation, task_id, generation):
        if delegation is not None and "replay" in delegation:
            replay = delegation["replay"]
            conn.execute(
                "UPDATE run_waiters SET replay_scope = ?, replay_request_id = ? "
                "WHERE task_id = ? AND generation = ?",
                (replay_scope(replay["binding"]), replay["request_id"], task_id, generation),
            )

    def list_replay_waiters(self, binding: dict, *, after: int = 0, limit: int = 32) -> list[dict]:
        if type(limit) is not int or not 1 <= limit <= 32 or type(after) is not int or after < 0:
            raise ValueError("invalid replay scan bound")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_waiters WHERE replay_scope = ? AND replay_settled = 0 "
                "AND id > ? ORDER BY id LIMIT ?", (replay_scope(binding), after, limit),
            ).fetchall()
        return [self._run_waiter_from_row(row) for row in rows]

    def find_replay_waiter(self, binding: dict, registration_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_waiters WHERE replay_scope = ? AND replay_registration_id = ?",
                (replay_scope(binding), registration_id),
            ).fetchone()
        return self._run_waiter_from_row(row) if row else None

    def replay_waiter_progress(self, waiter: dict, *, attempt: bool = False,
                               registration_id: str | None = None,
                               settled: bool = False, settle_unattempted: bool = False) -> dict:
        """Short transaction only; no remote operation runs with this lock held."""
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM run_waiters WHERE id = ?", (waiter["id"],)).fetchone()
            if row is None:
                raise TaskError("replay waiter no longer exists")
            current = self._run_waiter_from_row(row)
            original = lambda w: {k: v for k, v in w["delegation"].items()
                                  if k != "registration_id"}
            if (current["kind"] != "delegated" or original(current) != original(waiter)
                    or current["generation"] != waiter["generation"]
                    or current["task_generation"] != waiter["task_generation"]):
                raise TaskError("replay waiter identity mismatch")
            if registration_id is not None:
                if current["replay_registration_id"] not in (None, registration_id):
                    raise TaskError("replay registration mismatch")
                bound = conn.execute(
                    "SELECT id FROM run_waiters WHERE replay_scope = ? AND replay_registration_id = ?",
                    (row["replay_scope"], registration_id),
                ).fetchone()
                if bound is not None and bound["id"] != row["id"]:
                    raise TaskError("replay registration already bound to a waiter")
                conn.execute("UPDATE run_waiters SET replay_registration_id = ? WHERE id = ?",
                             (registration_id, row["id"]))
            if attempt and current["state"] == "preparing" and not current["replay_settled"]:
                task = self._fetch(conn, current["task_id"])
                if self._run_waiter_matches_task(current, task):
                    conn.execute("UPDATE run_waiters SET replay_attempted = 1 WHERE id = ?",
                                 (row["id"],))
            if settled:
                if current["state"] in ("preparing", "active"):
                    raise TaskError("cannot settle a live replay waiter")
                if (current["event_id"] != waiter["event_id"]
                        or current["replay_registration_id"] != waiter["replay_registration_id"]):
                    raise TaskError("replay completion changed")
                conn.execute("UPDATE run_waiters SET replay_settled = 1 WHERE id = ?", (row["id"],))
            if (settle_unattempted and current["state"] not in ("preparing", "active")
                    and not current["replay_attempted"]):
                conn.execute("UPDATE run_waiters SET replay_settled = 1 WHERE id = ?", (row["id"],))
            updated = conn.execute("SELECT * FROM run_waiters WHERE id = ?", (row["id"],)).fetchone()
            conn.execute("COMMIT")
        return self._run_waiter_from_row(updated)
