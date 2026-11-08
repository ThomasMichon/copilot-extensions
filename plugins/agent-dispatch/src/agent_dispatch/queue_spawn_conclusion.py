"""``TaskQueue`` mixin: spawn-reservation conclusion-claim lifecycle.

Split out of :mod:`agent_dispatch.queue_spawn_reservations` (itself a Phase
10 componentization extraction from :mod:`agent_dispatch.queue`) purely for
module size: the conclusion-retry claim/revalidate family
(``record_spawn_conclusion``, ``claim_spawn_conclusion_retry``,
``validate_spawn_conclusion_claim``) is a cohesive sub-concern of the
broader spawn-reservation lifecycle -- cleanup-retry claiming after a
reservation has already reached a terminal (``failed``/``settled``) state --
distinct from reservation acquisition and the active-state transitions
``queue_spawn_reservations.py`` itself still owns.

``_validate_conclusion_claim``/``_newer_worktree_reservation`` stay defined
in :mod:`agent_dispatch.queue_spawn_reservations` (``_update_reservation``,
which remains there, also needs them) and are imported here as ordinary
top-level names -- no circular import, since this module is a one-way
consumer of that one.

:class:`SpawnConclusionMixin` is composed into
:class:`agent_dispatch.queue.TaskQueue`; it relies on ``self._connect()``
and ``self._now()`` from that class and is not usable standalone.
"""

from __future__ import annotations

import json
import uuid

from .queue_records import SpawnReservation, SpawnState, TaskError
from .queue_spawn_reservations import _newer_worktree_reservation, _validate_conclusion_claim


def _conclusion_payload(raw: object) -> dict[str, object]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


class SpawnConclusionMixin:
    """``TaskQueue`` mixin: spawn-reservation conclusion-claim lifecycle."""

    def recover_held_cleanup(self, key: str, **kwargs) -> dict:
        from .cleanup_recovery import recover
        return recover(self, key, **kwargs)

    def cleanup_recovery_history(self, key: str) -> list[dict]:
        from .cleanup_recovery import history
        return history(self, key)

    def record_spawn_conclusion(
        self,
        key: str,
        *,
        conclusion_state: str,
        conclusion_detail: str,
        detail: str | None = None,
        claim_token: str | None = None,
        claimant_machine: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Persist conclusion progress on an active or retired reservation."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * "
                "FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            mutable_states = SpawnState.ACTIVE | frozenset({SpawnState.FAILED, SpawnState.SETTLED})
            if row["state"] not in mutable_states:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} is {row['state']!r}, not mutable")
            _validate_conclusion_claim(
                row["cleanup_claim_token"],
                row["cleanup_claim_expires_at"],
                claim_token,
                now=ts,
                required=(
                    row["state"]
                    in {
                        SpawnState.FAILED,
                        SpawnState.SETTLED,
                    }
                    and row["conclusion_state"] == "pending"
                ),
            )
            from .cleanup_recovery import validate_recovery
            validate_recovery(self, conn, row, claimant_machine=claimant_machine)
            if claim_token is not None:
                newer = _newer_worktree_reservation(conn, row, key)
                if newer is not None:
                    conn.execute("COMMIT")
                    raise TaskError(f"worktree carried by newer reservation {newer['key']}")
            conn.execute(
                "UPDATE spawn_reservations SET updated_at = ?, "
                "detail = COALESCE(?, detail), conclusion_state = ?, "
                "conclusion_detail = ?, "
                "cleanup_claim_expires_at = CASE WHEN ? IS NOT NULL THEN 0 "
                "ELSE cleanup_claim_expires_at END WHERE key = ?",
                (
                    ts,
                    detail,
                    conclusion_state,
                    conclusion_detail,
                    claim_token,
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def claim_spawn_conclusion_retry(
        self,
        key: str,
        *,
        claim_seconds: float = 300.0,
        claimant_machine: str | None = None,
        now: float | None = None,
    ) -> tuple[SpawnReservation, bool, str | None]:
        """Claim a retired cleanup retry without racing worktree reuse."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if (
                row["state"] not in {SpawnState.FAILED, SpawnState.SETTLED}
                or row["conclusion_state"] != "pending"
            ):
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} has no retired pending cleanup")
            from .cleanup_recovery import validate_recovery
            validate_recovery(self, conn, row, check_allocation=False, claimant_machine=claimant_machine)
            try:
                current_expiry = float(row["cleanup_claim_expires_at"] or 0)
            except (TypeError, ValueError):
                current_expiry = 0.0
            if row["cleanup_claim_token"] and current_expiry > ts:
                conn.execute("COMMIT")
                return SpawnReservation._from_row(row), False, None
            blocker = _newer_worktree_reservation(conn, row, key)
            claim_token = None
            if blocker is not None:
                payload: dict[str, object] = {
                    "action": "preserved",
                    "reason": "worktree-carried-by-newer-reservation",
                    "prior": row["conclusion_detail"],
                }
                conclusion_state = "held"
            else:
                # Newer worktrees transition durably to held above; only a
                # real lease grant needs the remaining allocation fences.
                validate_recovery(self, conn, row, claimant_machine=claimant_machine)
                claim_token = uuid.uuid4().hex
                payload = {
                    **_conclusion_payload(row["conclusion_detail"]),
                    "action": "pending",
                    "reason": "cleanup-retry-claimed",
                    "claim_token": claim_token,
                    "claim_expires_at": ts + max(1.0, claim_seconds),
                }
                conclusion_state = "pending"
            if blocker is not None:
                payload["blocking_reservation_key"] = blocker["key"]
            conn.execute(
                "UPDATE spawn_reservations SET updated_at = ?, "
                "conclusion_state = ?, conclusion_detail = ?, "
                "cleanup_claim_token = ?, cleanup_claim_expires_at = ? "
                "WHERE key = ?",
                (
                    ts,
                    conclusion_state,
                    json.dumps(
                        payload,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    claim_token,
                    (ts + max(1.0, claim_seconds) if claim_token is not None else None),
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated), blocker is None, claim_token

    def validate_spawn_conclusion_claim(
        self,
        key: str,
        claim_token: str,
        *,
        claimant_machine: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Revalidate a cleanup lease and worktree fence before side effects."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            _validate_conclusion_claim(
                row["cleanup_claim_token"],
                row["cleanup_claim_expires_at"],
                claim_token,
                now=ts,
            )
            from .cleanup_recovery import validate_recovery
            validate_recovery(self, conn, row, claimant_machine=claimant_machine)
            newer = _newer_worktree_reservation(conn, row, key)
            if newer is not None:
                conn.execute("COMMIT")
                raise TaskError(f"worktree carried by newer reservation {newer['key']}")
            updated = SpawnReservation._from_row(row)
            conn.execute("COMMIT")
        return updated
