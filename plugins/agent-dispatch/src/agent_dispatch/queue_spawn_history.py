"""``TaskQueue`` mixin: explicit reconciliation of historical legacy reservations.

The startup-fencing migration gives pre-existing rows ``startup_complete=0``
and ``startup_fenced=0`` without inferring launch completion. Pool placement
treats any such unbound row as accountable legacy capacity, so a long-lived
queue cannot adopt claim-before-spawn until that history is explicitly
reconciled. This operator-attested operation fences terminal, unbound history
created before a cutoff. It never touches active reservations, pending native
release, pending conclusions, retained transport or a native still shared with
live work, and it preserves every row.

Composed into :class:`agent_dispatch.queue.TaskQueue`; relies on
``self._connect()``, ``self._audit()`` and ``self._now()``.
"""

from __future__ import annotations

import collections
import json
import math
from typing import Any

from .ordinary_native_bindings import reservation_native_identity
from .queue_pool_accountability import ReservationAccountability
from .queue_records import SpawnState, Status, TaskError

_SAMPLE = 20


class SpawnHistoryMixin:
    """``fence_legacy_history`` for :class:`TaskQueue`."""

    def fence_legacy_history(
        self, *, before: float, apply: bool = False, permitted: bool = False,
        confirmed_no_live_holder: bool = False, reason: str = "",
    ) -> dict[str, Any]:
        """Preview, or apply, startup fencing and native retirement for old history."""
        if type(before) not in (int, float) or not math.isfinite(before) or before <= 0:
            raise TaskError("history reconciliation requires a finite positive cutoff")
        if apply and (not permitted or not confirmed_no_live_holder or not reason.strip()):
            raise TaskError(
                "history reconciliation requires permission, verified no-live-holder attestation and reason",
            )
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            accounting = ReservationAccountability(conn)
            rows = conn.execute(
                "SELECT s.*, b.reservation_key AS bound_key FROM spawn_reservations s"
                " LEFT JOIN pool_reservation_bindings b ON b.reservation_key = s.key"
                " ORDER BY s.reserved_at, s.key",
            ).fetchall()
            identities = {row["key"]: self._history_identities(conn, accounting, row) for row in rows}
            protected = self._protected_identities(conn, accounting, rows, identities)
            excluded: collections.Counter[str] = collections.Counter()
            eligible: list[Any] = []
            for row in rows:
                if row["bound_key"] is not None:
                    continue
                reason_key = self._history_exclusion(
                    row, before, accounting, identities[row["key"]], protected,
                )
                if reason_key is None:
                    eligible.append(row)
                elif reason_key != "not_accountable":
                    excluded[reason_key] += 1
            result: dict[str, Any] = {
                "before": before, "applied": False, "eligible": len(eligible),
                "excluded": dict(sorted(excluded.items())),
                "sample": [row["key"] for row in eligible[:_SAMPLE]],
            }
            if not apply:
                conn.rollback()
                return result
            timestamp = self._now(None)
            by_task: dict[str, list[str]] = collections.defaultdict(list)
            for row in eligible:
                # Mirror the recovery paths' completed-release marker so carried-session
                # selection and binding evidence both see the conversation as retired.
                retire = bool(identities[row["key"]])
                conn.execute(
                    "UPDATE spawn_reservations SET startup_fenced=1, conversation_retired=?,"
                    " release_requested=?, updated_at=? WHERE key=?",
                    (1 if retire else row["conversation_retired"],
                     1 if retire else row["release_requested"], timestamp, row["key"]),
                )
                by_task[row["task_id"]].append(row["key"])
            recheck = ReservationAccountability(conn)
            keys = [row["key"] for row in eligible]
            for offset in range(0, len(keys), 500):
                chunk = keys[offset:offset + 500]
                for row in conn.execute(
                    f"SELECT * FROM spawn_reservations WHERE key IN ({','.join('?' * len(chunk))})", chunk,
                ):
                    if recheck.accountable(row):
                        conn.rollback()
                        raise TaskError(
                            f"history reconciliation left {row['key']} accountable; nothing was changed",
                        )
            for task_id, task_keys in by_task.items():
                status_row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
                status = status_row["status"] if status_row else None
                self._audit(
                    conn, task_id, ts=timestamp, from_status=status, to_status=status,
                    worker="operator",
                    note=(
                        f"legacy history reconciled: {len(task_keys)} reservation(s) fenced before "
                        f"{before}; no-live-holder attested; history preserved; reason={reason.strip()}"
                    ),
                )
            conn.commit()
        result["applied"] = True
        result["fenced"] = len(eligible)
        result["tasks"] = len(by_task)
        return result

    def adopt_legacy_capacity(
        self, *, apply: bool = False, permitted: bool = False, reason: str = "",
    ) -> dict[str, Any]:
        """Preview, or apply, binding still-accountable unbound rows to their only pool.

        Pool placement refuses while any unbound reservation is accountable, because
        its lane cannot be attributed. A row whose task and recorded execution machine
        permit exactly one configured pool instance is bound to it, so it keeps
        holding that lane instead of blocking every pool. Ambiguous or unmatched rows
        are reported and left untouched; nothing is fenced, released or rewritten.
        A row with no recorded worktree (an in-flight launch) is not safely
        adoptable, because pool publication requires that workspace; it is
        reported as ``workspace_unrecorded`` and can be adopted once recorded. A
        recorded workspace the pool or current task routing refuses (an excluded
        worktree, another machine) is reported as ``workspace_conflict``; the
        same check guards publication into an accounting-only binding. The pool's
        own filters are evaluated against that recorded workspace. A row whose
        execution host is unknown (no recorded execution machine, fleet handle or
        creating host) is ``machine_unrecorded``, and one whose host evidence
        disagrees (with the owner's host) is ``machine_conflict``; the host is
        never inferred from the candidate pools.
        Every row stays unresolved while the pool catalog is not ready, since an
        incomplete catalog cannot prove a pool is the only match. An adopted row is
        accounting-only: it gains no pool recovery assignment, so a parked legacy
        body keeps its legacy cold-resume owner, which still waits for a free
        lane in its bound pool before restarting the body.
        """
        if apply and (not permitted or not reason.strip()):
            raise TaskError("legacy capacity adoption requires permission and a reason")
        from .bridge_remote import normalize_host
        from .queue_pool_placement import _permits, bound_workspace_conflict, reservation_hosts
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            timestamp = self._now(None)
            catalog = self._pool_catalog_from_conn(conn, now=timestamp)
            # An incomplete catalog could hide the other pools a row may belong to.
            definitions = catalog.definitions if catalog.ready else ()
            accounting = ReservationAccountability(conn)
            rows = conn.execute(
                "SELECT s.* FROM spawn_reservations s"
                " LEFT JOIN pool_reservation_bindings b ON b.reservation_key = s.key"
                " WHERE b.reservation_key IS NULL ORDER BY s.reserved_at, s.key",
            ).fetchall()
            adopt: list[tuple[Any, Any]] = []
            unresolved: dict[str, list[str]] = collections.defaultdict(list)
            for row in rows:
                if not accounting.accountable(row):
                    continue
                task = self._fetch(conn, row["task_id"])
                # Never infer the host from the candidate pools: only the row's
                # own evidence may place it, and conflicting evidence places none.
                hosts = reservation_hosts(row, task)
                machine = next(iter(hosts)) if len(hosts) == 1 else None
                candidates = [
                    pool for pool in definitions
                    if task is not None and machine is not None
                    and normalize_host(pool.machine) == machine and _permits(pool, task)
                ]
                # Pool filters judge the workspace the body actually occupies.
                matches = [
                    pool for pool in candidates
                    if not row["worktree"] or _permits(pool, task, worktree=row["worktree"])
                ]
                if not catalog.ready:
                    unresolved["catalog_not_ready"].append(row["key"])
                elif len(hosts) != 1:
                    unresolved["machine_conflict" if hosts else "machine_unrecorded"].append(row["key"])
                elif candidates and not matches:
                    unresolved["workspace_conflict"].append(row["key"])
                elif len(matches) == 1 and not row["worktree"]:
                    # A bound row must publish into its recorded workspace; a
                    # launch that has not recorded one stays legacy for now.
                    unresolved["workspace_unrecorded"].append(row["key"])
                elif len(matches) == 1 and bound_workspace_conflict(
                    task, row["worktree"], machine, machine=matches[0].machine,
                    env=matches[0].env, registration_id=matches[0].registration_id,
                    capabilities=matches[0].filters.permit.get("capabilities", frozenset()),
                ):
                    # Publication would run the body in a workspace the pool or
                    # current task routing refuses.
                    unresolved["workspace_conflict"].append(row["key"])
                elif len(matches) == 1:
                    adopt.append((row, matches[0]))
                else:
                    unresolved["ambiguous" if matches else "no_matching_pool"].append(row["key"])
            result: dict[str, Any] = {
                "applied": False, "adoptable": len(adopt),
                "unresolved": {kind: keys[:_SAMPLE] for kind, keys in sorted(unresolved.items())},
                "unresolved_counts": {kind: len(keys) for kind, keys in sorted(unresolved.items())},
                "catalog_blockers": list(catalog.blockers),
                "sample": [
                    {"key": row["key"], "pool": "/".join(pool.identity)} for row, pool in adopt[:_SAMPLE]
                ],
            }
            if not apply:
                conn.rollback()
                return result
            by_task: dict[str, list[str]] = collections.defaultdict(list)
            for row, pool in adopt:
                conn.execute(
                    "INSERT INTO pool_reservation_bindings VALUES (?, ?, ?, ?, ?)",
                    (row["key"], *pool.identity,
                     json.dumps(sorted(pool.filters.permit.get("capabilities", frozenset())))),
                )
                by_task[row["task_id"]].append(f"{row['key']}->{'/'.join(pool.identity)}")
            for task_id, adopted in by_task.items():
                status_row = conn.execute("SELECT status FROM tasks WHERE id=?", (task_id,)).fetchone()
                status = status_row["status"] if status_row else None
                self._audit(
                    conn, task_id, ts=timestamp, from_status=status, to_status=status,
                    worker="operator",
                    note=f"legacy capacity adopted into pool lanes: {', '.join(adopted)}; reason={reason.strip()}",
                )
            conn.commit()
        result["applied"] = True
        result["adopted"] = len(adopt)
        return result

    @staticmethod
    def _history_identities(
        conn: Any, accounting: ReservationAccountability, row: Any,
    ) -> frozenset[str] | None:
        """Every identity a row may refer to; ``None`` when the evidence conflicts."""
        history = {**dict(row), "native_session_id": accounting.receipts.get(row["key"])}
        try:
            native = reservation_native_identity(conn, history)
        except (TaskError, ValueError):
            return None
        if native is None and row["session_handle"] and row["startup_complete"]:
            # Only a fenced, never-completed startup may retire an unproven
            # handle as qualified-empty history.
            return None
        return frozenset({row["session_handle"], native, history["native_session_id"]} - {None})

    @staticmethod
    def _recorded_identities(accounting: ReservationAccountability, row: Any) -> set[str]:
        """Every independently recorded identity, without validating that they agree."""
        found = {row["session_handle"], accounting.receipts.get(row["key"])}
        try:
            info = json.loads(row["agent_info"]) if row["agent_info"] else None
        except ValueError:
            info = None
        if isinstance(info, dict):
            connection = info.get("connection")
            found |= {
                info.get("native_session_id"), info.get("acp_session_id"), info.get("session_id"),
                connection.get("native_session_id") if isinstance(connection, dict) else None,
            }
        return {value for value in found if isinstance(value, str) and value}

    @classmethod
    def _protected_identities(
        cls, conn: Any, accounting: ReservationAccountability, rows: list[Any],
        identities: dict[str, frozenset[str] | None],
    ) -> dict[str, str]:
        """Identities still owned by live work, a task owner or an open task's latest native."""
        protected: dict[str, str] = {}
        # A terminal task's owner field is history, not a live claim on the native.
        for row in conn.execute(
            "SELECT owner_session_id FROM tasks WHERE owner_session_id IS NOT NULL"
            f" AND status NOT IN ({','.join('?' * len(Status.TERMINAL))})",
            tuple(Status.TERMINAL),
        ):
            protected[row["owner_session_id"]] = "shared_live_native"
        open_tasks = {
            row["id"] for row in conn.execute(
                f"SELECT id FROM tasks WHERE status NOT IN ({','.join('?' * len(Status.CONCLUDED))})",
                tuple(Status.CONCLUDED),
            )
        }
        latest: dict[str, tuple[tuple[int, float, str], set[str]]] = {}
        transport: set[str] = set()
        for row in rows:
            raw = set(identities[row["key"]] or ()) | cls._recorded_identities(accounting, row)
            if row["state"] in SpawnState.ACTIVE or row["native_release_pending"]:
                for native in raw:
                    protected[native] = "shared_live_native"
            history = {**dict(row), "native_session_id": accounting.receipts.get(row["key"])}
            try:
                unresolved = accounting.transport.accountable(history)
            except (TaskError, ValueError):
                unresolved = True  # conflicting evidence cannot prove the transport settled
            if unresolved:
                transport |= raw
            # Order by numeric attempt, not scan order: timestamps can tie or regress.
            order = (row["attempt"] or 0, row["reserved_at"] or 0.0, row["key"])
            if row["task_id"] in open_tasks and raw and (
                row["task_id"] not in latest or order > latest[row["task_id"]][0]
            ):
                latest[row["task_id"]] = (order, raw)
        for _, raw in latest.values():
            for native in raw:
                protected.setdefault(native, "resumable_task_native")
        for native in transport:
            protected.setdefault(native, "transport")
        return protected

    @staticmethod
    def _history_exclusion(
        row: Any, before: float, accounting: ReservationAccountability,
        identities: frozenset[str] | None, protected: dict[str, str],
    ) -> str | None:
        if row["state"] in SpawnState.ACTIVE:
            return "active"
        if row["native_release_pending"]:
            return "native_release_pending"
        if identities is None:
            return "unclassifiable"
        try:
            if not accounting.accountable(row):
                return "not_accountable"
        except (TaskError, ValueError):
            return "unclassifiable"
        if row["reserved_at"] is None or row["reserved_at"] >= before:
            return "after_cutoff"
        if row["conclusion_state"] == "pending":
            return "conclusion_pending"
        history = {**dict(row), "native_session_id": accounting.receipts.get(row["key"])}
        if accounting.transport.accountable(history):
            return "transport"
        for native in sorted(identities | SpawnHistoryMixin._recorded_identities(accounting, row)):
            if native in protected:
                return protected[native]
        return None
