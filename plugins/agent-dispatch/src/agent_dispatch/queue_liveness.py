"""``TaskQueue`` mixin: liveness reconciliation, orphan reaping, and backlog health.

Extracted mechanically from :mod:`agent_dispatch.queue` to control that
module's size. The methods and constants below move verbatim, with no behavior
change, into a sibling mixin composed back into ``TaskQueue``.

:class:`LivenessMixin` is composed into :class:`agent_dispatch.queue.TaskQueue`
via multiple inheritance; it relies on ``self._connect()``, ``self._now()``,
``self._canonical_repo()``, and ``self._audit()`` from that class and is not
usable standalone.
"""

from __future__ import annotations

from collections.abc import Callable

from .queue_records import Status


class LivenessMixin:
    """Liveness, orphan-reaping, and backlog-health methods for
    :class:`TaskQueue`."""

    def recover_expired_leases(self, *, now: float | None = None) -> int:
        """Deprecated compatibility shim -- now runs a **liveness** GC pass.

        The recovery trigger moved from wall-clock lease expiry to worker
        liveness (see :meth:`reconcile_liveness`). This method is retained so the
        ``POST /recover`` route and any external caller keep working, but it no
        longer requeues on elapsed time: it requeues only tasks whose owner is
        **confirmed gone**. Returns the number requeued.
        """
        return self.reconcile_liveness(now=now)["requeued"]

    #: Liveness verdicts a reconcile acts on (mirror of ``tracking`` constants,
    #: duplicated here so the engine takes no import dependency on the resolver).
    LIVENESS_LIVE = "live"
    LIVENESS_GONE = "gone"
    LIVENESS_UNKNOWN = "unknown"
    #: A held task requeued this many times by GC (owner kept going gone) is
    #: retired to the terminal ``dead_letter`` state instead of churning forever.
    DEFAULT_MAX_ATTEMPTS = 5

    def reconcile_liveness(
        self,
        resolver: Callable[[str, str | None, str | None], str] | None = None,
        *,
        max_attempts: int | None = None,
        now: float | None = None,
    ) -> dict[str, int]:
        """Garbage-collect held tasks by reconciling them against **owner-session
        liveness** -- the recovery mechanism that replaces time-based lease expiry.

        For each ``claimed``/``started`` task the owner's liveness is resolved to a
        tri-state verdict (keyed on the task's captured ``owner_session_id`` -- not
        mere worktree occupancy) and acted on:

        - ``live``    -> leave it (the *same* owner still holds it, no matter how
          long -- there is **no** wall-clock expiry).
        - ``gone``    -> **fenced** requeue (owner confirmed gone). Past
          ``max_attempts`` requeues the task is retired to ``dead_letter`` instead.
        - ``unknown`` -> leave it (resolver couldn't tell, or identity not captured
          yet -- degrade safe; never requeue on ignorance).

        The last verdict is persisted to ``last_liveness`` so the buildup metric
        can classify held tasks without re-probing the bridge.

        ``resolver`` is ``(worktree, machine, owner_session_id) -> verdict``; the
        default shells :func:`tracking.liveness_verdict`. Injecting it keeps the
        engine subprocess-free and lets tests drive verdicts deterministically.

        **Fencing:** liveness is probed **outside** the write lock, then each gone
        task is requeued under a short transaction with a conditional update on
        ``(id, status, owner_session_id, generation)`` -- so if the owner
        registered, resumed, completed, or the task was re-claimed between probe
        and write, the update **no-ops** (no double-execution, no clobber).

        Returns counts: ``checked``/``live``/``gone``/``unknown``/``requeued``/
        ``dead_lettered``.
        """
        if resolver is None:
            from . import tracking

            def resolver(worktree: str, machine: str | None, owner_session_id: str | None) -> str:
                return tracking.liveness_verdict(
                    worktree, machine=machine, owner_session_id=owner_session_id
                )

        cap = self.DEFAULT_MAX_ATTEMPTS if max_attempts is None else max_attempts
        ts = self._now(now)
        counts = {
            "checked": 0,
            "live": 0,
            "gone": 0,
            "unknown": 0,
            "requeued": 0,
            "dead_lettered": 0,
        }
        with self._connect() as conn:
            held = conn.execute(
                "SELECT id, owner, owner_session_id, generation, attempts"
                " FROM tasks WHERE status IN (?, ?)",
                (Status.CLAIMED, Status.STARTED),
            ).fetchall()
        # (task_id, verdict, owner_session_id, generation, attempts) per held task.
        probed: list[tuple[str, str, str | None, int, int]] = []
        for row in held:
            counts["checked"] += 1
            machine, _sep, worktree = (row["owner"] or "").partition("/")
            if not worktree:
                verdict = self.LIVENESS_UNKNOWN
            else:
                verdict = resolver(worktree, machine or None, row["owner_session_id"])
            counts[verdict] = counts.get(verdict, 0) + 1
            probed.append(
                (row["id"], verdict, row["owner_session_id"], row["generation"], row["attempts"])
            )
        if not probed:
            return counts
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for task_id, verdict, owner_session_id, generation, attempts in probed:
                # Persist the last verdict for the buildup metric (fenced on the
                # generation so a re-claim mid-pass isn't tagged with a stale beat).
                conn.execute(
                    "UPDATE tasks SET last_liveness = ? WHERE id = ? AND generation = ?"
                    " AND status IN (?, ?)",
                    (verdict, task_id, generation, Status.CLAIMED, Status.STARTED),
                )
                if verdict != self.LIVENESS_GONE:
                    continue
                to_status = Status.DEAD_LETTER if attempts >= cap else Status.QUEUED
                owner_clause = (
                    "owner_session_id = ?"
                    if owner_session_id is not None
                    else "owner_session_id IS NULL"
                )
                params: list[object] = [to_status, ts]
                if to_status == Status.QUEUED:
                    # requeue: clear ownership + identity, bump attempts
                    set_sql = (
                        "status = ?, updated_at = ?, owner = NULL,"
                        " owner_session_id = NULL, lease_expires_at = NULL,"
                        " attempts = attempts + 1"
                    )
                else:
                    set_sql = "status = ?, updated_at = ?"
                sql = (
                    f"UPDATE tasks SET {set_sql} WHERE id = ? AND status IN (?, ?)"  # noqa: S608 (set_sql is a constant; all values parameterized)
                    f" AND generation = ? AND {owner_clause}"
                )
                params += [task_id, Status.CLAIMED, Status.STARTED, generation]
                if owner_session_id is not None:
                    params.append(owner_session_id)
                cur = conn.execute(sql, params)
                if cur.rowcount:
                    self._audit(
                        conn,
                        task_id,
                        ts=ts,
                        from_status=Status.STARTED,
                        to_status=to_status,
                        note="owner-gone"
                        if to_status == Status.QUEUED
                        else "owner-gone (dead-letter: max attempts)",
                    )
                    if to_status == Status.QUEUED:
                        counts["requeued"] += 1
                    else:
                        counts["dead_lettered"] += 1
            conn.execute("COMMIT")
        return counts

    def reap_orphaned_targets(
        self,
        live_worktrees: set[str] | None,
        *,
        machine: str,
        grace_secs: float,
        now: float | None = None,
    ) -> dict[str, int]:
        """Abandon **unowned** (proposed/queued) tasks pinned to a target worktree
        on ``machine`` that is no longer live.

        :meth:`reconcile_liveness` only recovers *owned* held tasks against their
        owner's session liveness; an **unowned** proposed/queued task has no owner,
        so nothing ever reaps it -- pinned (``--target-worktree``) to a worktree
        that was later pruned, it lingers forever. That is the context-handoff
        leak: a stored handoff whose live-cutover never completed (or a fallback
        the operator never resumed) accumulates one dead task per session. This
        closes it.

        ``live_worktrees`` is the set of worktree ids currently **live** on
        ``machine`` (e.g. ``agent-worktrees list --tracking-status active``). A
        task is reaped iff ALL hold:

        - status is ``proposed`` or ``queued`` (unowned -- no worker holds it);
        - ``target_machine`` case-insensitively equals ``machine`` (we only judge
          against a live-worktree set we actually have -- a task targeting another
          machine is that coordinator's to reap);
        - ``target_worktree`` is set and **not** in ``live_worktrees``;
        - it is older than ``grace_secs`` (a just-created handoff whose successor
          hasn't started yet is never reaped -- mirrors the liveness GC's refusal
          to act on a claim/register race).

        **Degrade safe:** ``live_worktrees is None`` (the caller's probe failed)
        reaps nothing -- never act on ignorance, exactly like the ``unknown``
        liveness verdict. **Fenced:** each abandon is conditional on ``(id,
        status, generation)``, so a task claimed/consumed between the read and the
        write no-ops (no clobber of freshly-picked-up work).

        Returns counts: ``checked`` / ``orphaned`` / ``reaped``.
        """
        counts = {"checked": 0, "orphaned": 0, "reaped": 0}
        if live_worktrees is None:
            return counts
        ts = self._now(now)
        cutoff = ts - max(0.0, grace_secs)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, target_worktree, generation, status FROM tasks"
                " WHERE status IN (?, ?)"
                "  AND target_worktree IS NOT NULL"
                "  AND target_machine IS NOT NULL"
                "  AND lower(target_machine) = lower(?)"
                "  AND created_at < ?",
                (Status.PROPOSED, Status.QUEUED, machine, cutoff),
            ).fetchall()
        victims: list[tuple[str, int, str]] = []
        for row in rows:
            counts["checked"] += 1
            if row["target_worktree"] in live_worktrees:
                continue
            counts["orphaned"] += 1
            victims.append((row["id"], row["generation"], row["status"]))
        if not victims:
            return counts
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            for task_id, generation, status in victims:
                cur = conn.execute(
                    "UPDATE tasks SET status = ?, updated_at = ?, completed_at = ?,"
                    " owner = NULL, lease_expires_at = NULL"
                    " WHERE id = ? AND status = ? AND generation = ?",
                    (Status.ABANDONED, ts, ts, task_id, status, generation),
                )
                if cur.rowcount:
                    self._audit(
                        conn,
                        task_id,
                        ts=ts,
                        from_status=status,
                        to_status=Status.ABANDONED,
                        note="orphaned: target worktree no longer live",
                    )
                    counts["reaped"] += 1
            conn.execute("COMMIT")
        return counts

    def backlog_health(
        self, *, repo: str | None = None, now: float | None = None
    ) -> dict[str, float | int | None]:
        """A queryable **buildup** signal: how much work is waiting to drain.

        In a healthy system tasks are short-lived; a growing, undraining backlog
        is a system-health signal that warrants attention (see the vision's
        *buildup-is-a-health-signal*). This surfaces the raw numbers -- it takes
        **no** action (escalate-or-demote is a consumer policy, not the
        engine). Reports, scoped to ``repo`` when given:

        - ``queued`` / ``proposed`` / ``held`` / ``suspended`` /
          ``dead_letter`` -- counts by phase.
        - ``oldest_queued_age`` -- seconds the oldest ``queued`` task has waited
          (``None`` when empty), the clearest "is it draining?" beat.
        - ``held_live`` / ``held_gone`` / ``held_unknown`` -- held tasks broken out
          by the **last GC liveness verdict** (a held task not yet reconciled
          counts as ``unknown``). A ``gone`` owner is requeued immediately, so the
          real backlog signal is ``held_live`` -- a live owner that has stopped
          progressing.
        - ``oldest_held_live_age`` -- seconds since the oldest **live**-owned held
          task last made progress (``last_seen_at``), i.e. the *stuck-but-alive*
          signal Q2 says buildup should surface (``None`` when none).
        """
        repo = self._canonical_repo(repo)
        ts = self._now(now)
        where_repo = " AND repo = ?" if repo is not None else ""
        args: tuple[object, ...] = (repo,) if repo is not None else ()
        with self._connect() as conn:

            def _count(status: str) -> int:
                return conn.execute(
                    f"SELECT COUNT(*) FROM tasks WHERE status = ?{where_repo}",  # noqa: S608 (constant clause; parameterized)
                    (status, *args),
                ).fetchone()[0]

            queued = _count(Status.QUEUED)
            proposed = _count(Status.PROPOSED)
            suspended = _count(Status.SUSPENDED)
            dead_letter = _count(Status.DEAD_LETTER)
            held_rows = conn.execute(
                "SELECT last_liveness, last_seen_at FROM tasks"  # noqa: S608 (constant clause; parameterized)
                f" WHERE status IN (?, ?){where_repo}",
                (Status.CLAIMED, Status.STARTED, *args),
            ).fetchall()
            oldest = conn.execute(
                f"SELECT MIN(created_at) FROM tasks WHERE status = ?{where_repo}",  # noqa: S608 (constant clause; parameterized)
                (Status.QUEUED, *args),
            ).fetchone()[0]
        held_live = held_gone = held_unknown = 0
        oldest_live_seen: float | None = None
        for row in held_rows:
            verdict = row["last_liveness"]
            if verdict == self.LIVENESS_LIVE:
                held_live += 1
                seen = row["last_seen_at"]
                if seen is not None and (oldest_live_seen is None or seen < oldest_live_seen):
                    oldest_live_seen = seen
            elif verdict == self.LIVENESS_GONE:
                held_gone += 1
            else:  # unknown or not-yet-reconciled (NULL)
                held_unknown += 1
        return {
            "queued": queued,
            "proposed": proposed,
            "held": len(held_rows),
            "suspended": suspended,
            "held_live": held_live,
            "held_gone": held_gone,
            "held_unknown": held_unknown,
            "dead_letter": dead_letter,
            "oldest_queued_age": round(ts - oldest, 3) if oldest is not None else None,
            "oldest_held_live_age": (
                round(ts - oldest_live_seen, 3) if oldest_live_seen is not None else None
            ),
        }
