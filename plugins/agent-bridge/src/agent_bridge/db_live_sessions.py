"""Database live-session, ownership, and inbox helpers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .db_core import (
    LIVE_SESSION_PURGE_SECONDS,
    LIVE_SESSION_STALE_SECONDS,
    LIVE_SESSION_VENUE_PURGE_SECONDS,
    live_session_is_fresh,
    local_pid_alive,
)
from .db_live_session_aliases import (
    CANONICAL_SESSION_SQL as _CANON,
    PROCESS_START_TOLERANCE_SECONDS,
    register_live_session_atomic,
)

LIVE_MESSAGE_DELIVERIES = {"queue", "steer", "interrupt"}


def _validate_live_message_delivery(delivery: str | None) -> str:
    value = delivery or "queue"
    if value not in LIVE_MESSAGE_DELIVERIES:
        raise ValueError(f"unsupported live-message delivery: {value!r}")
    return value


class _LiveSessionsMixin:
    """Extension-backed live-session registry and delivery helpers."""

    def register_live_session(
        self,
        session_id: str,
        *,
        machine: str | None,
        cwd: str | None,
        worktree_id: str | None,
        repo: str | None,
        branch: str | None,
        pid: int | None,
        role: str | None,
        now: float,
        driven_by: str | None = None,
        venue: str | None = None,
        process_started_at: float | None = None,
    ) -> str:
        """Insert or refresh a live interactive-session registration (upsert).

        Atomic, and respects the two #2912 primitives in a single statement (so
        no register-after-check window, even against a second process):

        * **Ownership reservation** -- a *new* registration for a worktree is
          refused when a fresh owned-ACP :meth:`reserve_worktree_ownership`
          reservation holds it (an active owned session already controls the
          worktree). This is the ``registration must respect the reservation``
          half of #2912. A reservation whose owning session is no longer active
          (or absent) does not block.
        * **Terminal ``taken-over``** -- a heartbeat re-register is refused when
          the existing row is ``taken-over`` (a killed predecessor cannot
          resurrect itself via a late heartbeat). Lease-lapsed ``expired`` rows
          still revive normally.

        ``venue`` is an opaque, caller-supplied JSON string describing where a
        remote-venue CLI-mode session actually lives and how to reattach to it
        (``{"kind","target","mux_session_name"}``) -- ``None`` for the ordinary
        local case. Never interpreted here; carried through for
        reattach/observation guidance to read later
        (agent-bridge-cli-mode-sessions Phase 4).

        Returns the resulting registration status: ``'live'`` on a successful
        insert/refresh, or a rejection reason -- ``'reserved'`` (an owned ACP
        reservation holds the worktree), ``'taken-over'`` (this id was taken
        over) or ``'incarnation_mismatch'`` (the pid or process start it reports
        contradicts the row it would update, or -- through a renamed id's alias
        -- its machine does: a stale heartbeat can't overwrite the successor).
        The route maps a rejection to HTTP 409.

        The upsert, the CLI-mode reservation claim and a same-process rollover
        (alias insertion + predecessor deletion) commit in one transaction, so
        a concurrent deregistration can't strand a half-rolled-over successor.
        """
        return register_live_session_atomic(
            self, session_id, machine=machine, cwd=cwd, worktree_id=worktree_id,
            repo=repo, branch=branch, pid=pid, role=role, now=now, driven_by=driven_by,
            venue=venue, process_started_at=process_started_at,
        )

    def create_cli_mode_reservation(
        self, worktree_id: str, *, now: float, ttl_seconds: float = 300.0,
        venue: str | None = None,
    ) -> str | None:
        """Atomically reserve a worktree's next CLI-mode Session Host.

        Returns the new ``reservation_id``, or ``None`` if refused because a
        not-yet-expired reservation already holds this worktree (one active
        CLI-mode allocation per worktree at a time --
        §one-host-per-cwd-lane). An expired reservation (past ``expires_at``)
        is silently replaced, whether or not it was ever claimed -- an expired
        reservation is not a live session.

        ``venue`` is the optional opaque JSON venue descriptor the claiming
        registration inherits into ``live_sessions.venue``.

        This is the explicit, operator-initiated half of
        §opt-in-not-ambient-default: nothing calls this on a caller's behalf,
        so no worktree is CLI-mode-eligible unless someone deliberately
        allocated one.
        """
        reservation_id = uuid.uuid4().hex
        cur = self.execute_write(
            "INSERT INTO cli_mode_reservations "
            "(worktree_id, reservation_id, created_at, expires_at, "
            "claimed_by_session_id, venue) "
            "VALUES (?, ?, ?, ?, NULL, ?) "
            "ON CONFLICT(worktree_id) DO UPDATE SET "
            "reservation_id=excluded.reservation_id, "
            "created_at=excluded.created_at, expires_at=excluded.expires_at, "
            "claimed_by_session_id=NULL, venue=excluded.venue "
            "WHERE cli_mode_reservations.expires_at <= excluded.created_at",
            (worktree_id, reservation_id, now, now + ttl_seconds, venue),
        )
        return reservation_id if cur.rowcount == 1 else None

    def get_cli_mode_reservation(self, worktree_id: str) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM cli_mode_reservations WHERE worktree_id=?",
            (worktree_id,),
        )
        return dict(rows[0]) if rows else None

    def claim_cli_mode_reservation(
        self, worktree_id: str, session_id: str, *, now: float,
    ) -> bool:
        """Atomically claim the worktree's pending CLI-mode reservation, if any.

        Returns ``True`` only when an unclaimed, unexpired reservation existed
        and this call claimed it. A second claim attempt (e.g. a heartbeat
        re-registration) safely no-ops (``False``) once claimed -- callers
        should treat that as "already claimed", not an error.
        """
        cur = self.execute_write(
            "UPDATE cli_mode_reservations SET claimed_by_session_id=? "
            "WHERE worktree_id=? AND claimed_by_session_id IS NULL "
            "AND expires_at > ?",
            (session_id, worktree_id, now),
        )
        return cur.rowcount == 1

    def release_cli_mode_reservation(
        self,
        worktree_id: str,
        *,
        reservation_id: str | None = None,
        unclaimed_only: bool = False,
    ) -> int:
        """Delete a CLI-mode reservation -- by worktree, or the exact
        ``reservation_id`` if given (refuses to delete a different, newer
        reservation created since). With ``unclaimed_only``, also refuses to
        delete a reservation a session has already claimed. Returns how many
        rows were removed."""
        claim_predicate = " AND claimed_by_session_id IS NULL" if unclaimed_only else ""
        if reservation_id is not None:
            cur = self.execute_write(
                "DELETE FROM cli_mode_reservations "
                f"WHERE worktree_id=? AND reservation_id=?{claim_predicate}",
                (worktree_id, reservation_id),
            )
        else:
            cur = self.execute_write(
                f"DELETE FROM cli_mode_reservations WHERE worktree_id=?{claim_predicate}",
                (worktree_id,),
            )
        return cur.rowcount

    def update_live_turn_state(
        self,
        session_id: str,
        *,
        turn_state: str | None,
        last_activity_at: float,
    ) -> None:
        """Update a live session's derived turn-state + last-activity timestamp.

        Called from the represented event-ingest path (Phase 7 Channel A). A
        no-op for a session_id that isn't registered. Also refreshes
        ``updated_at`` so activity keeps the registration fresh.
        """
        self.execute_write(  # alias-aware like ack_live_messages (rollover between them)
            "UPDATE live_sessions SET turn_state=?, last_activity_at=?, "
            f"updated_at=? WHERE session_id={_CANON}",
            (turn_state, last_activity_at, last_activity_at, session_id, session_id),
        )

    def update_live_progress(
        self, session_id: str, *, latest_progress: str, now: float
    ) -> bool:
        """Store an operator-driven session's latest progress beat (JSON).

        Latest-only (overwrite); also refreshes ``updated_at``. Returns True if a
        registered session was updated, False if ``session_id`` is unknown
        (Phase 7 Slice 7c). The live-session analogue of a dispatched task's
        ``latest_progress``.
        """
        cur = self.execute_write(
            "UPDATE live_sessions SET latest_progress=?, updated_at=? "
            f"WHERE session_id={_CANON}",  # alias-aware: a beat to a retired id still lands
            (latest_progress, now, session_id, session_id),
        )
        return cur.rowcount > 0

    def deregister_live_session(
        self, session_id: str, *, pid: int | None = None, process_started_at: float | None = None,
    ) -> bool:
        """Atomically remove a live registration, its queue and aliases; True only if this exact row went.

        With the deregistering process's identity, only a row that process
        registered goes: another process's (a replacement that registered the
        id between this process's cleanup DELETEs) is left alone, checked in
        the same statement. Omitted on either side, a field never conflicts."""
        conn = self._get_conn()
        with self._write_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if conn.execute(
                    "DELETE FROM live_sessions WHERE session_id=? "
                    "AND (? IS NULL OR pid IS NULL OR pid = ?) "
                    "AND (? IS NULL OR process_started_at IS NULL "
                    "     OR ABS(process_started_at - ?) < ?)",
                    (session_id, pid, pid, process_started_at, process_started_at,
                     PROCESS_START_TOLERANCE_SECONDS),
                ).rowcount != 1:
                    conn.rollback()
                    return False
                conn.execute("DELETE FROM live_messages WHERE session_id=?", (session_id,))
                conn.execute("DELETE FROM live_session_aliases WHERE alias_session_id=? "
                             "OR target_session_id=?", (session_id, session_id))
                conn.commit()
                return True
            except Exception:
                conn.rollback()
                raise

    def expire_live_sessions_for_worktree(
        self, worktree_id: str, *, now: float, expected_session_id: str | None = None
    ) -> int:
        """Immediately demote every ``live`` registration for a worktree to the
        **terminal ``taken-over``** state and drop its undelivered inbox
        messages -- the *invalidate-on-take-over* hook (#2906 + #2912).

        A take-over has just terminated the interactive CLI, so a lingering
        ``status=='live'`` row must not keep the worktree un-ownable (it would
        trip the resume guard against the reclaim) nor accept a message that
        raced control changing hands. Unlike the reaper's lease-lapse
        ``expired`` (which a returning CLI's re-register upsert legitimately
        revives), ``taken-over`` is **terminal**: :meth:`register_live_session`
        refuses to revive it, so a killed predecessor's in-flight heartbeat can
        never flip its own row back to ``live`` after take-over (#2912). Returns
        how many registrations were demoted. Idempotent: a worktree with no live
        row is a no-op.

        ``expected_session_id``, when given, fences this demotion to *only*
        that exact ``session_id`` -- a caller that stopped one specific
        interactive CLI and knows its session id (the refusal's holder) should
        pass it, so a genuinely different CLI that registered for this
        worktree in the gap between the stop returning and this call (a fresh,
        never-confirmed-dead claimant) is left untouched rather than
        collaterally demoted (#2906 race hardening).
        """
        if expected_session_id is not None:  # alias-aware: the holder may have been renamed
            cur = self.execute_write(
                "UPDATE live_sessions SET status='taken-over', updated_at=? "
                f"WHERE worktree_id=? AND status='live' AND session_id={_CANON}",
                (now, worktree_id, expected_session_id, expected_session_id),
            )
        else:
            cur = self.execute_write(
                "UPDATE live_sessions SET status='taken-over', updated_at=? "
                "WHERE worktree_id=? AND status='live'",
                (now, worktree_id),
            )
        n = cur.rowcount
        if n:
            self.execute_write(
                "DELETE FROM live_messages WHERE delivered_at IS NULL "
                "AND session_id IN ("
                "SELECT session_id FROM live_sessions "
                "WHERE worktree_id=? AND status='taken-over')",
                (worktree_id,),
            )
        return n

    def reap_stale_live_sessions(
        self,
        *,
        now: float,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
        purge_seconds: float = LIVE_SESSION_PURGE_SECONDS,
        venue_purge_seconds: float = LIVE_SESSION_VENUE_PURGE_SECONDS,
        pid_alive: Callable[[Any], bool | None] = local_pid_alive,
    ) -> int:
        """Reconcile lapsed live-session leases against real process liveness,
        then purge long-dead rows (#2880, #3144, #3145).

        Three idempotent phases, run every sweep:

        1. **PID reconcile.** A lapsed heartbeat lease does *not* prove the CLI
           exited: the process can be alive while its extension stopped
           heartbeating (a wedged session / stalled event loop). For every row
           whose lease has lapsed (or that is already ``wedged``) we probe its
           pid:

           - **provably gone** -> ``expired`` (a dead CLI must never keep a
             worktree un-ownable, #2880), and its undelivered inbox messages are
             dropped so they can't reach a wrong future incarnation (#2906);
           - **still alive** -> ``wedged`` -- a distinct state that keeps the
             session legible and reclaimable to consumers (Neuron Forge can
             offer a read + explicit reclaim instead of pretending it is gone
             and forcing a blind take-over, #3145) while still reading as *not
             fresh* for the ownership/steer guards (``live_session_is_fresh``
             excludes it);
           - **undeterminable** (Windows / bad pid) -> lease fallback: demote a
             lapsed ``live`` row to ``expired`` exactly as before (#2880); leave
             an existing ``wedged`` row alone (can't confirm its death).

           A returning CLI's re-register upsert flips ``expired``/``wedged`` back
           to ``live``.

        2. **Purge.** ``expired`` and terminal ``taken-over`` rows whose
           ``updated_at`` is older than the purge grace window are DELETEd (with
           any leftover inbox messages), so the registry self-cleans instead of
           accumulating a graveyard that ``list`` and consumers surface (#3144).
           ``wedged`` rows are never purged -- their process is alive. An
           ``expired`` row that carries a remote ``venue`` waits
           ``venue_purge_seconds`` instead: its venue came from a launch
           reservation, and a session reconnecting after a purge comes back
           without one, unattributable to where it runs.

        Returns the number of registrations demoted *out of* ``live`` this sweep
        (expired + wedged). Idempotent: a re-run with nothing lapsed does no
        work.
        """
        cutoff = now - stale_seconds
        # Phase 1 -- scan lapsed-live rows AND existing wedged rows (a wedged
        # row whose process later exits must still progress to expired/purge).
        scan = self.execute_read(
            "SELECT session_id, pid, status, venue FROM live_sessions "
            "WHERE (status='live' AND updated_at < ?) OR status='wedged'",
            (cutoff,),
        )
        expire_from_live: list[str] = []
        wedge_from_live: list[str] = []
        expire_from_wedged: list[str] = []
        for row in scan:
            # A venue-hosted session's pid belongs to the remote machine;
            # probing it on this host is meaningless (it could even match an
            # unrelated local process), so treat it as undeterminable and use
            # the lease fallback.
            alive = None if row["venue"] else pid_alive(row["pid"])
            if row["status"] == "live":
                if alive is True:
                    wedge_from_live.append(row["session_id"])
                else:  # gone or undeterminable -> lease fallback
                    expire_from_live.append(row["session_id"])
            else:  # already wedged
                if alive is False:
                    expire_from_wedged.append(row["session_id"])

        demoted = 0

        def _in(ids: list[str]) -> str:
            return ",".join("?" * len(ids))

        # live -> expired (re-check the lease in the WHERE so a row revived by a
        # heartbeat between the SELECT and this UPDATE is not wrongly demoted).
        if expire_from_live:
            cur = self.execute_write(
                f"UPDATE live_sessions SET status='expired' "
                f"WHERE session_id IN ({_in(expire_from_live)}) "
                f"AND status='live' AND updated_at < ?",
                (*expire_from_live, cutoff),
            )
            demoted += cur.rowcount
            self.execute_write(
                f"DELETE FROM live_messages WHERE delivered_at IS NULL "
                f"AND session_id IN ({_in(expire_from_live)})",
                tuple(expire_from_live),
            )
        # live -> wedged (same lease re-check guard).
        if wedge_from_live:
            cur = self.execute_write(
                f"UPDATE live_sessions SET status='wedged' "
                f"WHERE session_id IN ({_in(wedge_from_live)}) "
                f"AND status='live' AND updated_at < ?",
                (*wedge_from_live, cutoff),
            )
            demoted += cur.rowcount
        # wedged -> expired (process confirmed gone). Guarded on status='wedged'
        # so a row revived to 'live' since the scan is left untouched.
        if expire_from_wedged:
            self.execute_write(
                f"UPDATE live_sessions SET status='expired', updated_at=? "
                f"WHERE session_id IN ({_in(expire_from_wedged)}) "
                f"AND status='wedged'",
                (now, *expire_from_wedged),
            )
            self.execute_write(
                f"DELETE FROM live_messages WHERE delivered_at IS NULL "
                f"AND session_id IN ({_in(expire_from_wedged)})",
                tuple(expire_from_wedged),
            )

        # Phase 2 -- purge long-dead rows (expired / taken-over) past the grace
        # window, so the registry does not accumulate a graveyard (#3144).
        purge_cutoff = now - purge_seconds
        dead = self.execute_read(
            "SELECT session_id FROM live_sessions "
            "WHERE status IN ('expired', 'taken-over') AND updated_at < ? "
            "AND NOT (status='expired' AND venue IS NOT NULL AND updated_at >= ?)",
            (purge_cutoff, now - venue_purge_seconds),
        )
        if dead:
            dead_ids = [r["session_id"] for r in dead]
            self.execute_write(
                f"DELETE FROM live_messages WHERE session_id IN ({_in(dead_ids)})",
                tuple(dead_ids),
            )
            self.execute_write(  # its aliases go with it (live_sessions_drop_orphaned_aliases),
                # in the same statement: a separate cleanup could delete an alias
                # a registration re-created for a purged id in between.
                f"DELETE FROM live_sessions WHERE session_id IN ({_in(dead_ids)})",
                tuple(dead_ids),
            )
        return demoted

    def reserve_worktree_ownership(
        self,
        worktree_id: str,
        session_id: str,
        *,
        now: float,
        reclaim: bool = False,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> bool:
        """Atomically claim the per-worktree ACP-ownership reservation (#2912).

        The resume verb takes this **before** spawning ACP so that an owned
        session and a live-CLI registration cannot both win a worktree even
        across processes. The claim + the not-held check are a **single**
        ``INSERT ... SELECT ... WHERE NOT EXISTS ... ON CONFLICT DO UPDATE``
        statement, so no register / reserve from another writer can slip between
        the check and the write (the register-after-check window the #2879 guard
        only narrows).

        A non-``reclaim`` claim succeeds only when **no fresh live CLI** holds
        the worktree **and** any existing reservation is either ours or owned by
        a session that is no longer active (running/idle) -- so a stale
        reservation from a crashed/ended owner is reclaimable but a live owner's
        is not. ``reclaim=True`` (take-over) force-takes the reservation
        unconditionally: the caller has just terminated the interactive CLI and
        intends to own the freed worktree.

        Returns True if the reservation is now held by ``session_id``, False if
        another party holds it (a fresh live CLI, or another active owner).
        """
        if reclaim:
            self.execute_write(
                "INSERT INTO worktree_ownership "
                "(worktree_id, session_id, reserved_at, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(worktree_id) DO UPDATE SET "
                "session_id=excluded.session_id, updated_at=excluded.updated_at",
                (worktree_id, session_id, now, now),
            )
            return True
        cutoff = now - stale_seconds
        cur = self.execute_write(
            "INSERT INTO worktree_ownership "
            "(worktree_id, session_id, reserved_at, updated_at) "
            "SELECT ?, ?, ?, ? "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM live_sessions "
            "  WHERE worktree_id = ? AND status = 'live' AND updated_at >= ?"
            ") "
            "ON CONFLICT(worktree_id) DO UPDATE SET "
            "session_id=excluded.session_id, updated_at=excluded.updated_at "
            "WHERE ("
            "    worktree_ownership.session_id = excluded.session_id "
            "    OR NOT EXISTS ("
            "      SELECT 1 FROM sessions s "
            "      WHERE s.id = worktree_ownership.session_id "
            "        AND s.status IN ('running', 'idle')"
            "    )"
            "  ) AND NOT EXISTS ("
            "    SELECT 1 FROM live_sessions "
            "    WHERE worktree_id = ? AND status = 'live' AND updated_at >= ?"
            "  )",
            (worktree_id, session_id, now, now,
             worktree_id, cutoff, worktree_id, cutoff),
        )
        if cur.rowcount == 1:
            return True
        # 0 rows: either blocked, or the row already names us (a no-op UPDATE
        # whose WHERE held but changed nothing). Confirm current holder.
        row = self.get_worktree_ownership(worktree_id)
        return row is not None and row.get("session_id") == session_id

    def release_worktree_ownership(
        self, *, worktree_id: str | None = None, session_id: str | None = None
    ) -> int:
        """Release an ACP-ownership reservation -- by worktree, by owning
        session, or both (#2912). Called when an owned session stops/ends so a
        live CLI can take the freed worktree without waiting on lease staleness.
        Returns how many reservations were removed."""
        if worktree_id is not None and session_id is not None:
            cur = self.execute_write(
                "DELETE FROM worktree_ownership "
                "WHERE worktree_id=? AND session_id=?",
                (worktree_id, session_id),
            )
        elif worktree_id is not None:
            cur = self.execute_write(
                "DELETE FROM worktree_ownership WHERE worktree_id=?",
                (worktree_id,),
            )
        elif session_id is not None:
            cur = self.execute_write(
                "DELETE FROM worktree_ownership WHERE session_id=?",
                (session_id,),
            )
        else:
            return 0
        return cur.rowcount

    def get_worktree_ownership(self, worktree_id: str) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM worktree_ownership WHERE worktree_id=?", (worktree_id,)
        )
        return dict(rows[0]) if rows else None

    def get_live_session_exact(self, session_id: str) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM live_sessions WHERE session_id=?", (session_id,)
        )
        return dict(rows[0]) if rows else None

    def resolve_live_session_id(self, session_id: str) -> str:
        """Resolve a retired live-session id to its current id, if aliased."""
        current = session_id
        seen: set[str] = set()
        for _ in range(8):
            if current in seen:
                return current
            seen.add(current)
            rows = self.execute_read(
                "SELECT target_session_id FROM live_session_aliases "
                "WHERE alias_session_id=?",
                (current,),
            )
            if not rows:
                return current
            target = rows[0]["target_session_id"]
            if not isinstance(target, str) or not target or target == current:
                return current
            current = target
        return current

    def live_session_aliases_to(self, session_id: str) -> list[str]:
        """Retired ids that now forward to ``session_id``."""
        rows = self.execute_read(
            "SELECT alias_session_id FROM live_session_aliases "
            "WHERE target_session_id=?",
            (session_id,),
        )
        return [r["alias_session_id"] for r in rows]

    def get_live_session(self, session_id: str) -> dict[str, Any] | None:
        sql = f"SELECT * FROM live_sessions WHERE session_id={_CANON}"  # alias + row: one read
        return next((dict(r) for r in self.execute_read(sql, (session_id,) * 2)), None)

    def get_fresh_live_session(
        self,
        session_id: str,
        *,
        now: float,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> dict[str, Any] | None:
        """Like :meth:`get_live_session`, but only when the heartbeat lease is
        still valid (see :func:`live_session_is_fresh`); else None."""
        row = self.get_live_session(session_id)
        if row is None or not live_session_is_fresh(row, now, stale_seconds):
            return None
        return row

    def list_live_sessions(
        self, worktree_id: str | None = None, *, include_dead: bool = False
    ) -> list[dict[str, Any]]:
        """List live-session registrations, optionally scoped to a worktree.

        By default this **hides dead rows** -- terminal ``expired`` and
        ``taken-over`` registrations -- so ``list`` and consumers (Neuron Forge)
        see only sessions that still matter: ``live`` (fresh) and ``wedged``
        (process alive but heartbeat-stalled, #3145). Dead rows self-clean via
        the reaper's purge (#3144); pass ``include_dead=True`` to see them for
        debugging.
        """
        dead_clause = (
            "" if include_dead else "status NOT IN ('expired', 'taken-over')"
        )
        clauses = []
        params: list[Any] = []
        if worktree_id:
            clauses.append("worktree_id=?")
            params.append(worktree_id)
        if dead_clause:
            clauses.append(dead_clause)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.execute_read(
            f"SELECT * FROM live_sessions{where} ORDER BY updated_at DESC",
            tuple(params),
        )
        return [dict(r) for r in rows]

    def list_fresh_live_sessions(
        self,
        worktree_id: str | None = None,
        *,
        now: float,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> list[dict[str, Any]]:
        """Live registrations with a still-valid heartbeat lease (fresh only).

        The load-bearing query for the atomic ownership guard (#2879): a worktree
        is held by a live CLI only if it has a *fresh* ``live`` registration, so
        a stale/expired corpse never blocks an owned resume."""
        return [
            r
            for r in self.list_live_sessions(worktree_id)
            if live_session_is_fresh(r, now, stale_seconds)
        ]

    def resolve_live_session(
        self,
        handle: str,
        *,
        now: float,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> dict[str, Any] | None:
        """Resolve a *handle* -> its current live session row (or None).

        A handle is either an exact ``session_id`` or a **worktree handle**
        (``worktree_id``). This is the load-bearing addressing primitive for
        D3: an agent is a *series of sessions in one worktree*, so peers address
        it by worktree handle and the bridge resolves that to whichever session
        is live *right now* -- so identity and ``reply-to`` survive a handoff.

        Precedence:
          1. exact ``session_id`` match (returned regardless of freshness, to
             preserve direct-id delivery; a durable message queue waits for the
             session either way);
          2. else the **current fresh live incarnation** whose ``worktree_id``
             equals the handle, using the same immutable-registration ordering
             as the atomic enqueue fence. A later heartbeat on an older
             incarnation cannot make it current again.

        Returns None when the handle is neither a known session id nor a
        currently-live worktree.
        """
        exact = self.get_live_session(handle)
        if exact is not None:
            return exact
        cutoff = now - stale_seconds
        rows = self.execute_read(
            "SELECT * FROM live_sessions "
            "WHERE worktree_id=? AND status='live' AND updated_at>=? "
            "ORDER BY registered_at DESC, updated_at DESC LIMIT 1",
            (handle, cutoff),
        )
        return dict(rows[0]) if rows else None

    def current_live_session_for_worktree(
        self,
        worktree_id: str,
        *,
        now: float,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> str | None:
        """The session id of the **current** live incarnation for a worktree.

        Unlike :meth:`resolve_live_session` (which exact-matches a session id
        first and ignores ``status``), this is a worktree-only lookup that
        considers **only fresh ``live`` rows** and orders by ``registered_at``
        (the immutable per-incarnation start) so a later heartbeat on an older
        incarnation can't make it re-win "current", and a take-over-expired row
        (``status!='live'``, even with a bumped ``updated_at``) never counts.
        This is the load-bearing supersession check for the inbox lease
        (#2906). Returns None when no fresh live session holds the worktree.
        """
        cutoff = now - stale_seconds
        rows = self.execute_read(
            "SELECT session_id FROM live_sessions "
            "WHERE worktree_id=? AND status='live' AND updated_at>=? "
            "ORDER BY registered_at DESC, updated_at DESC LIMIT 1",
            (worktree_id, cutoff),
        )
        return rows[0]["session_id"] if rows else None

    def current_represented_session_for_worktree(
        self,
        worktree_id: str,
        *,
        now: float,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> str | None:
        """Return the current readable live or wedged represented incarnation.

        Messaging intentionally excludes ``wedged`` rows; result inspection must
        retain them because the process is still known alive and its represented
        tail remains useful.
        """
        cutoff = now - stale_seconds
        rows = self.execute_read(
            "SELECT session_id FROM live_sessions "
            "WHERE worktree_id=? AND (status='wedged' OR "
            "(status='live' AND updated_at>=?)) "
            "ORDER BY registered_at DESC, updated_at DESC LIMIT 1",
            (worktree_id, cutoff),
        )
        return rows[0]["session_id"] if rows else None

    def enqueue_live_message(
        self, session_id: str, sender: str, body: str, now: float,
        reply_to: str | None = None, kind: str = "prompt",
        delivery: str = "queue",
        idempotency_key: str | None = None,
    ) -> int:
        """Enqueue a message for delivery into a live session; return its id.

        ``kind`` is the D2 intent tag: ``prompt`` (a work directive, the
        default) vs ``notify``/``status-check`` (a terse out-of-band ack, never
        new work). Rendered into the delivered envelope so the receiver reacts
        appropriately.
        """
        delivery = _validate_live_message_delivery(delivery)
        cur = self.execute_write(
            "INSERT INTO live_messages (session_id, sender, body, reply_to, "
            "kind, delivery, idempotency_key, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, sender, body, reply_to, kind, delivery, idempotency_key, now),
        )
        return int(cur.lastrowid or 0)

    def enqueue_live_message_if_fresh(
        self,
        session_id: str,
        *,
        sender: str,
        body: str,
        now: float,
        reply_to: str | None = None,
        kind: str = "prompt",
        delivery: str = "queue",
        expected_session_id: str | None = None,
        idempotency_key: str | None = None,
        stale_seconds: float = LIVE_SESSION_STALE_SECONDS,
    ) -> tuple[int | None, str | None]:
        """Atomically lease-check the target registration and enqueue, or reject.

        The freshness lease + supersession check + insert are a **single
        ``INSERT ... SELECT ... WHERE EXISTS``** statement, so the guard and the
        write are atomic at the SQLite level -- no other writer (a reaper, a
        take-over invalidation, a register, or a session roll), **even in a
        second process sharing the database**, can commit between the check and
        the insert. This is the race-free enforcement #2906 asks for (NF's
        client-side pre-send check can only approximate it).

        The guard admits the message only when ``session_id`` is a *fresh live*
        registration that is also the **current** incarnation for its worktree
        (the row with the greatest ``registered_at`` among fresh live rows --
        immune to heartbeat timing), and any ``expected_session_id`` matches it.

        Returns ``(message_id, None)`` on success, or ``(None, reason)`` when
        rejected. ``reason`` is one of ``not_found`` (map to 404), ``stale``,
        ``superseded:<sid>``, or ``expected_mismatch:<sid>`` (map to 409). The
        reason is derived from a follow-up read purely to shape the caller's
        error message; the accept/reject decision itself is the atomic insert.
        """
        delivery = _validate_live_message_delivery(delivery)
        cutoff = now - stale_seconds
        cur = self.execute_write(
            "INSERT OR IGNORE INTO live_messages "
            "(session_id, sender, body, reply_to, kind, delivery, "
            "idempotency_key, created_at) "
            f"SELECT {_CANON}, ?, ?, ?, ?, ?, ?, ? "
            "WHERE EXISTS ("
            "  SELECT 1 FROM live_sessions ls "
            f"  WHERE ls.session_id = {_CANON} AND ls.status = 'live' "
            "    AND ls.updated_at >= ? "
            "    AND ("
            "      ls.worktree_id IS NULL OR ls.session_id = ("
            "        SELECT session_id FROM live_sessions "
            "        WHERE worktree_id = ls.worktree_id AND status = 'live' "
            "          AND updated_at >= ? "
            "        ORDER BY registered_at DESC, updated_at DESC LIMIT 1"
            "      )"
            "    )"
            f"    AND (? IS NULL OR {_CANON} = ls.session_id)"
            ")",
            (
                session_id, session_id, sender, body, reply_to, kind, delivery,
                idempotency_key, now,
                session_id, session_id, cutoff, cutoff,
                expected_session_id, expected_session_id, expected_session_id,
            ),
        )
        if cur.rowcount == 1:
            return int(cur.lastrowid or 0), None
        if idempotency_key:
            # The message and every canonical-id comparison in ONE statement
            # (one snapshot): a rollover committing between separate reads
            # would resolve the two handles differently and turn an identical
            # retry into a false conflict.
            msg_canon = (
                "COALESCE((SELECT target_session_id FROM live_session_aliases "
                "WHERE alias_session_id = live_messages.session_id), live_messages.session_id)"
            )
            existing = self.execute_read(
                "SELECT id, sender, body, reply_to, kind, delivery, "
                f"{msg_canon} = {_CANON} AS same_target, "
                f"(? IS NULL OR {_CANON} = {_CANON}) AS same_expected "
                "FROM live_messages WHERE idempotency_key = ?",
                (session_id, session_id, expected_session_id, expected_session_id,
                 expected_session_id, session_id, session_id, idempotency_key),
            )
            if existing:
                original = existing[0]
                same_request = (
                    bool(original["same_target"])
                    and original["sender"] == sender
                    and original["body"] == body
                    and original["reply_to"] == reply_to
                    and original["kind"] == kind
                    and original["delivery"] == delivery
                    and bool(original["same_expected"])
                )
                if same_request:
                    return int(original["id"]), None
                return None, "idempotency_conflict"
        # Rejected -- derive a reason for the error message (best-effort; the
        # authoritative decision was the 0-row insert above).
        row = self.get_live_session(session_id)
        if row is None:
            return None, "not_found"
        if not live_session_is_fresh(row, now, stale_seconds):
            return None, "stale"
        worktree_id = row.get("worktree_id")
        current_sid = session_id
        if worktree_id:
            current_sid = (
                self.current_live_session_for_worktree(
                    worktree_id, now=now, stale_seconds=stale_seconds
                )
                or session_id
            )
        if current_sid != session_id:
            return None, f"superseded:{current_sid}"
        if expected_session_id is not None and expected_session_id != current_sid:
            return None, f"expected_mismatch:{current_sid}"
        return None, "stale"

    def list_pending_live_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Undelivered messages for a session, oldest-first (delivery order).

        Session controls (``kind`` starting with ``control:``, e.g. a mode
        change) are excluded: they are claimed apart (:meth:`claim_live_controls`),
        so an extension that predates controls never delivers one as a prompt.
        """
        rows = self.execute_read(
            "SELECT * FROM live_messages "
            f"WHERE session_id={_CANON} AND delivered_at IS NULL AND kind NOT LIKE 'control:%' "
            "ORDER BY id ASC",
            (session_id, session_id),
        )
        return [dict(r) for r in rows]

    def claim_live_controls(
        self, session_id: str, now: float, max_age: float
    ) -> list[dict[str, Any]]:
        """Claim a session's pending controls for its extension to apply.

        One transaction selects the unclaimed controls and stamps
        ``claimed_at``, so each control goes to exactly one poll, and a
        claimed control can no longer be withdrawn by its requester's timeout
        (see :meth:`withdraw_live_control`). Controls older than ``max_age``
        are expired instead: their requester is gone (e.g. the daemon
        restarted mid-wait), so they must never apply late.
        """
        with self._write_lock:
            conn = self._get_conn()
            conn.execute("BEGIN IMMEDIATE")
            try:
                session_id = conn.execute(
                    f"SELECT {_CANON}", (session_id, session_id)
                ).fetchone()[0]
                conn.execute(
                    "UPDATE live_messages SET delivered_at=?, outcome='expired' "
                    "WHERE session_id=? AND kind LIKE 'control:%' "
                    "AND delivered_at IS NULL AND claimed_at IS NULL AND created_at < ?",
                    (now, session_id, now - max_age),
                )
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM live_messages WHERE session_id=? "
                    "AND kind LIKE 'control:%' AND delivered_at IS NULL "
                    "AND claimed_at IS NULL ORDER BY id ASC",
                    (session_id,),
                ).fetchall()]
                if rows:
                    placeholders = ",".join("?" for _ in rows)
                    conn.execute(
                        f"UPDATE live_messages SET claimed_at=? WHERE id IN ({placeholders})",
                        (now, *(r["id"] for r in rows)),
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return rows

    def withdraw_live_control(self, session_id: str, control_id: int, now: float) -> bool:
        """Withdraw a control nobody has claimed yet; True when withdrawn.

        False means the extension already claimed it (it may be applying it
        now) or it already has an outcome -- the requester must then report
        that outcome, or that the change is in flight, never "not applied".
        """
        cur = self.execute_write(
            "UPDATE live_messages SET delivered_at=?, outcome='withdrawn' "
            f"WHERE session_id={_CANON} AND id=? AND kind LIKE 'control:%' "
            "AND delivered_at IS NULL AND claimed_at IS NULL",
            (now, session_id, session_id, control_id),
        )
        return cur.rowcount == 1

    def live_control_state(self, session_id: str, control_id: int) -> dict[str, Any] | None:
        """A control's ``claimed_at`` and ``outcome`` (``None`` if unknown)."""
        rows = self.execute_read(
            "SELECT claimed_at, outcome FROM live_messages "
            f"WHERE session_id={_CANON} AND id=? AND kind LIKE 'control:%'",
            (session_id, session_id, control_id),
        )
        return dict(rows[0]) if rows else None

    def ack_live_messages(
        self,
        session_id: str,
        ids: list[int],
        now: float,
        *,
        controls: bool = False,
        outcome: str | None = None,
    ) -> int:
        """Mark the given messages delivered; return how many rows changed.

        Scoped to ``session_id`` so a caller can only ack its own queue, and
        idempotent (already-delivered rows are left untouched by the
        ``delivered_at IS NULL`` guard), so a redelivered ack never errors.
        Messages and controls are acked apart (``controls``), so a message ack
        can't settle a control nor a control ack a message. A control is
        settled only after its extension claimed it, recording ``outcome``
        (``applied`` or ``rejected``).
        """
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        if controls:
            cur = self.execute_write(
                f"UPDATE live_messages SET delivered_at=?, outcome=? "
                f"WHERE session_id={_CANON} AND delivered_at IS NULL AND kind LIKE 'control:%' "
                f"AND claimed_at IS NOT NULL AND id IN ({placeholders})",
                (now, outcome or "applied", session_id, session_id, *ids),
            )
        else:
            cur = self.execute_write(
                f"UPDATE live_messages SET delivered_at=? "
                f"WHERE session_id={_CANON} AND delivered_at IS NULL AND kind NOT LIKE 'control:%' "
                f"AND id IN ({placeholders})",
                (now, session_id, session_id, *ids),
            )
        return cur.rowcount
