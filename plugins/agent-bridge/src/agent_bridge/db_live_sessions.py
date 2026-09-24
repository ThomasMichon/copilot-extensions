"""Database live-session, ownership, and inbox helpers."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from .db_core import (
    LIVE_SESSION_PURGE_SECONDS,
    LIVE_SESSION_STALE_SECONDS,
    live_session_is_fresh,
    local_pid_alive,
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
        reservation holds the worktree) or ``'taken-over'`` (this id was taken
        over). The route maps a rejection to HTTP 409.
        """
        cur = self.execute_write(
            "INSERT INTO live_sessions (session_id, machine, cwd, worktree_id, "
            "repo, branch, pid, role, driven_by, venue, status, registered_at, "
            "updated_at) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ? "
            "WHERE NOT EXISTS ("
            "  SELECT 1 FROM worktree_ownership wo "
            "  JOIN sessions s ON s.id = wo.session_id "
            "  WHERE wo.worktree_id = ? AND ? IS NOT NULL "
            "    AND s.status IN ('running', 'idle')"
            ") "
            "ON CONFLICT(session_id) DO UPDATE SET "
            "machine=excluded.machine, cwd=excluded.cwd, "
            "worktree_id=excluded.worktree_id, repo=excluded.repo, "
            "branch=excluded.branch, pid=excluded.pid, role=excluded.role, "
            "driven_by=excluded.driven_by, "
            "venue=COALESCE(excluded.venue, live_sessions.venue), "
            "status='live', updated_at=excluded.updated_at "
            "WHERE live_sessions.status != 'taken-over'",
            (session_id, machine, cwd, worktree_id, repo, branch, pid, role,
             driven_by, venue, now, now, worktree_id, worktree_id),
        )
        if cur.rowcount == 1:
            # Best-effort, additive: a worktree with a pending, unclaimed
            # CLI-mode reservation (agent-bridge-cli-mode-sessions Phase 2) is
            # claimed by this registration and the row is marked accordingly.
            # Never blocks or reverses the registration above -- claiming is
            # honest bookkeeping, not an admission gate; an unclaimed or absent
            # reservation is not an error. A venue descriptor recorded on the
            # reservation by the reserving launcher is inherited here, so a
            # remote session's venue comes from the trusted reservation.
            if worktree_id is not None and self.claim_cli_mode_reservation(
                worktree_id, session_id, now=now
            ):
                self.execute_write(
                    "UPDATE live_sessions SET cli_mode=1, venue=COALESCE("
                    "(SELECT venue FROM cli_mode_reservations "
                    " WHERE worktree_id=? AND claimed_by_session_id=?), venue) "
                    "WHERE session_id=?",
                    (worktree_id, session_id, session_id),
                )
            return "live"
        # Rejected -- derive why for the caller's error (the authoritative
        # decision was the 0-row write above).
        existing = self.get_live_session(session_id)
        if existing is not None and (existing.get("status") or "live") == "taken-over":
            return "taken-over"
        return "reserved"

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
        self, worktree_id: str, *, reservation_id: str | None = None,
    ) -> int:
        """Delete a CLI-mode reservation -- by worktree, or the exact
        ``reservation_id`` if given (refuses to delete a different, newer
        reservation created since). Returns how many rows were removed."""
        if reservation_id is not None:
            cur = self.execute_write(
                "DELETE FROM cli_mode_reservations "
                "WHERE worktree_id=? AND reservation_id=?",
                (worktree_id, reservation_id),
            )
        else:
            cur = self.execute_write(
                "DELETE FROM cli_mode_reservations WHERE worktree_id=?",
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
        self.execute_write(
            "UPDATE live_sessions SET turn_state=?, last_activity_at=?, "
            "updated_at=? WHERE session_id=?",
            (turn_state, last_activity_at, last_activity_at, session_id),
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
            "WHERE session_id=?",
            (latest_progress, now, session_id),
        )
        return cur.rowcount > 0

    def deregister_live_session(self, session_id: str) -> None:
        """Remove a live interactive-session registration and its message queue."""
        self.execute_write(
            "DELETE FROM live_sessions WHERE session_id=?", (session_id,)
        )
        self.execute_write(
            "DELETE FROM live_messages WHERE session_id=?", (session_id,)
        )

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
        if expected_session_id is not None:
            cur = self.execute_write(
                "UPDATE live_sessions SET status='taken-over', updated_at=? "
                "WHERE worktree_id=? AND status='live' AND session_id=?",
                (now, worktree_id, expected_session_id),
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
           ``wedged`` rows are never purged -- their process is alive.

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
            "WHERE status IN ('expired', 'taken-over') AND updated_at < ?",
            (purge_cutoff,),
        )
        if dead:
            dead_ids = [r["session_id"] for r in dead]
            self.execute_write(
                f"DELETE FROM live_messages WHERE session_id IN ({_in(dead_ids)})",
                tuple(dead_ids),
            )
            self.execute_write(
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

    def get_live_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self.execute_read(
            "SELECT * FROM live_sessions WHERE session_id=?", (session_id,)
        )
        return dict(rows[0]) if rows else None

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
            "SELECT ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE EXISTS ("
            "  SELECT 1 FROM live_sessions ls "
            "  WHERE ls.session_id = ? AND ls.status = 'live' "
            "    AND ls.updated_at >= ? "
            "    AND ("
            "      ls.worktree_id IS NULL OR ls.session_id = ("
            "        SELECT session_id FROM live_sessions "
            "        WHERE worktree_id = ls.worktree_id AND status = 'live' "
            "          AND updated_at >= ? "
            "        ORDER BY registered_at DESC, updated_at DESC LIMIT 1"
            "      )"
            "    )"
            "    AND (? IS NULL OR ? = ls.session_id)"
            ")",
            (
                session_id, sender, body, reply_to, kind, delivery,
                idempotency_key, now,
                session_id, cutoff, cutoff,
                expected_session_id, expected_session_id,
            ),
        )
        if cur.rowcount == 1:
            return int(cur.lastrowid or 0), None
        if idempotency_key:
            existing = self.execute_read(
                "SELECT id, session_id, sender, body, reply_to, kind, delivery "
                "FROM live_messages WHERE idempotency_key = ?",
                (idempotency_key,),
            )
            if existing:
                original = existing[0]
                same_request = (
                    original["session_id"] == session_id
                    and original["sender"] == sender
                    and original["body"] == body
                    and original["reply_to"] == reply_to
                    and original["kind"] == kind
                    and original["delivery"] == delivery
                    and (
                        expected_session_id is None
                        or expected_session_id == session_id
                    )
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
        """Undelivered messages for a session, oldest-first (delivery order)."""
        rows = self.execute_read(
            "SELECT * FROM live_messages "
            "WHERE session_id=? AND delivered_at IS NULL ORDER BY id ASC",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def ack_live_messages(
        self, session_id: str, ids: list[int], now: float
    ) -> int:
        """Mark the given messages delivered; return how many rows changed.

        Scoped to ``session_id`` so a caller can only ack its own queue, and
        idempotent (already-delivered rows are left untouched by the
        ``delivered_at IS NULL`` guard), so a redelivered ack never errors.
        """
        if not ids:
            return 0
        placeholders = ",".join("?" for _ in ids)
        cur = self.execute_write(
            f"UPDATE live_messages SET delivered_at=? "
            f"WHERE session_id=? AND delivered_at IS NULL AND id IN ({placeholders})",
            (now, session_id, *ids),
        )
        return cur.rowcount
