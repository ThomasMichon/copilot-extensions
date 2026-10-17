"""``TaskQueue`` mixin: atomic spawn-reservation lifecycle (the
spawn-at-most-once safety model over ``spawn_reservations``).

Extracted from :mod:`agent_dispatch.queue` (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423), covering
reservation acquisition (``reserve_spawn``) and the rearm/retire/defer/fail
active-state transitions. The conclusion-retry claim/revalidate family
(``record_spawn_conclusion``, ``claim_spawn_conclusion_retry``,
``validate_spawn_conclusion_claim``) later split further into its own
sibling mixin, :mod:`agent_dispatch.queue_spawn_conclusion` (module size);
``_validate_conclusion_claim``/``_newer_worktree_reservation`` stayed here
since ``_update_reservation`` (below, used by several transitions that
remain in this module) also needs them. Verified self-contained the same
way as every prior slice: an AST free-name walk found only module-level
helpers themselves used exclusively by this cluster (``spawn_key``,
``_validate_conclusion_claim``, ``_newer_worktree_reservation``), and no
test monkeypatches any of these methods by name. The three simple
read-only lookups (``get_reservation``, ``latest_reservation``,
``list_reservations``) stayed behind in ``queue.py`` itself, both because
``list_reservations`` calls ``self._canonical_repo`` (a ``TaskQueue``
helper genuinely defined there) and to keep this module comfortably under
the 1,000-line cap.

``SpawnReservation``/``TaskError``/``SpawnState``/``Status`` live in the
dependency-free :mod:`agent_dispatch.queue_records`, imported here as
ordinary top-level names -- no circular import, and
``typing.get_type_hints()`` resolves cleanly on every method (applying the
``queue_schedule_registry.py`` review lesson from the start).

:class:`SpawnReservationMixin` is composed into
:class:`agent_dispatch.queue.TaskQueue`; it relies on ``self._connect()``
and ``self._now()`` from that class and is not usable standalone.
Deferral lives in :mod:`agent_dispatch.queue_spawn_deferral`, inherited
here so the historical ``SpawnReservationMixin.defer_spawn`` surface stays
available.
"""

from __future__ import annotations

import logging
import hashlib
import json
import sqlite3
from collections.abc import Callable

from .queue_allocation_revisions import revision_disposed
from .queue_records import ExclusiveKeyBusyError, SpawnReservation, SpawnState, Status, TaskError
from .queue_spawn_deferral import SpawnDeferralMixin

log = logging.getLogger("agent-dispatch.queue")


def spawn_key(task_id: str, attempt: int) -> str:
    """The canonical reservation key for a (task, attempt) spawn."""
    return f"dispatch-task:{task_id}:{attempt}"






def _validate_conclusion_claim(
    stored_token: object,
    stored_expires_at: object,
    supplied_token: str | None,
    *,
    now: float,
    required: bool = False,
) -> None:
    if required and not stored_token:
        raise TaskError("cleanup claim token is required")
    if isinstance(stored_token, str) and stored_token:
        if supplied_token is None:
            raise TaskError("cleanup claim token is required")
        if supplied_token != stored_token:
            raise TaskError("cleanup claim changed")
        try:
            claim_expires_at = float(stored_expires_at or 0)
        except (TypeError, ValueError):
            claim_expires_at = 0.0
        if claim_expires_at <= now:
            raise TaskError("cleanup claim expired")
    elif supplied_token is not None:
        raise TaskError("cleanup claim changed")


def _newer_worktree_reservation(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    key: str,
) -> sqlite3.Row | None:
    if not row["worktree"]:
        return None
    return conn.execute(
        "SELECT key FROM spawn_reservations "
        "WHERE key <> ? AND (worktree = ? OR inherited_worktree = ?) "
        "AND reserved_at >= ? ORDER BY reserved_at DESC LIMIT 1",
        (
            key,
            row["worktree"],
            row["worktree"],
            row["reserved_at"],
        ),
    ).fetchone()


class SpawnReservationMixin(SpawnDeferralMixin):
    """Spawn-reservation lifecycle methods for :class:`TaskQueue`."""

    # -- spawn reservations --------------------------------------------------

    def reserve_spawn(
        self, task_id: str, *, reserved_by: str | None = None, now: float | None = None,
        allow_suspended_reembodiment: bool = False,
        driver: str | None = None,
        allocation_revision: int | None = None,
    ) -> tuple[SpawnReservation, bool]:
        """Atomically reserve the right to spawn an embody worker for ``task_id``.

        This is the primitive that makes "queued task -> exactly one host embody
        session" durable and idempotent. It is **distinct from the execution
        claim**: the claim is taken later by the embodied worker under its own
        worktree identity; this reservation is taken by the *spawner* (a
        ``create --spawn`` CLI, or the supervisor loop) *before* launching
        embody, so a crash / re-poll / lease-expiry between observing a
        spawn-eligible task and actually spawning it can never double-spawn.

        Semantics (all under one write lock):

        * If an **active** reservation
          (``reserving``/``spawned``/``cold``/``releasing``) already exists for
          the task, return it with ``False`` -- the task is already being
          spawned or its prior allocation is still being released; the caller
          must **not** spawn.
        * Otherwise mint a fresh reservation. ``attempt`` is ``max(prior
          attempts) + 1`` (``1`` for the first), keyed
          ``dispatch-task:<task_id>:<attempt>``, in state ``reserving``. Return
          it with ``True`` -- the caller owns this spawn.

        A prior ``failed``/``settled`` reservation therefore does not block a
        retry: the next attempt gets a fresh key.

        ``allow_suspended_reembodiment`` (PR #2913 review finding): the default
        eligibility gate requires ``queued`` and unowned -- the normal
        pool-spawn shape. A **suspended** task is never claimable (``claim``
        only ever picks from ``queued``) and always retains its owner from
        before suspension, so it can never satisfy that gate at all -- yet
        interactive re-embodiment (:mod:`agent_dispatch.interactive_embody`)
        legitimately needs to mint a reservation for exactly this case. Set
        ``True`` to additionally accept a ``suspended`` task regardless of
        owner; every other check (active-reservation dedupe, exclusive-key
        carry-forward) is unchanged.
        """
        ts = self._now(now)
        if allocation_revision is not None and (
            type(allocation_revision) is not int or not 1 <= allocation_revision <= 2**63 - 1
        ):
            raise TaskError("allocation revision must be a positive integer")
        from .ci_repair_spawn import authority_snapshot, reservation_authorized
        admission = None
        with self._connect() as probe:
            candidate = self._fetch(probe, task_id)
            if candidate and candidate.source == "ci-repair":
                admission = authority_snapshot(probe, candidate)
                if not reservation_authorized(probe, candidate, reserved_by):
                    raise TaskError("CI repair reservation requires independently verified authority")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.source == "ci-repair":
                if admission != authority_snapshot(conn, task):
                    raise TaskError("CI repair reservation requires its registered admission lane")
            rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE task_id = ? ORDER BY attempt ASC",
                (task_id,),
            ).fetchall()
            identity = conn.execute(
                "SELECT identity FROM workspace_queue_identity WHERE singleton = 1",
            ).fetchone()["identity"]
            logical_identity = json.dumps(
                [identity, task.repo, "dedup" if task.dedup_key is not None else "task",
                 task.dedup_key if task.dedup_key is not None else task.id],
                separators=(",", ":"),
            )
            creation_prefix = "dispatch-workspace:" + hashlib.sha256(logical_identity.encode()).hexdigest() + ":"
            allocation_rows = conn.execute(
                "SELECT * FROM spawn_reservations WHERE creation_key LIKE ? "
                "ORDER BY allocation_revision DESC, reserved_at DESC, attempt DESC",
                (creation_prefix + "%",),
            ).fetchall()
            latest_allocation = allocation_rows[0] if allocation_rows else (rows[-1] if rows else None)
            previous_revision = latest_allocation["allocation_revision"] if latest_allocation else 1
            revision = previous_revision if allocation_revision is None else allocation_revision
            if revision < previous_revision or (latest_allocation is None and revision != 1):
                raise TaskError("allocation revision must retain the current revision or advance it explicitly")
            new_allocation = revision != previous_revision
            if (
                latest_allocation is not None and latest_allocation["creation_key"] is None
                and latest_allocation["worktree"] is None
                and latest_allocation["state"] not in SpawnState.ACTIVE and not new_allocation
            ):
                raise TaskError("legacy allocation intent is unknown; preserve it for reconciliation")
            if new_allocation and not revision_disposed([
                row for row in (allocation_rows or rows)
                if row["allocation_revision"] == previous_revision
            ]):
                raise TaskError("new allocation revision requires proven disposal of the previous workspace")
            creation_key = creation_prefix + str(revision)
            if task.exclusive_key is not None:
                active = conn.execute(
                    "SELECT * FROM spawn_reservations "
                    "WHERE exclusive_key = ? AND state IN (?, ?, ?, ?) "
                    "AND (exclusive_released = 0 OR task_id = ?) "
                    "ORDER BY reserved_at ASC LIMIT 1",
                    (
                        task.exclusive_key, SpawnState.RESERVING, SpawnState.SPAWNED,
                        SpawnState.COLD, SpawnState.RELEASING, task_id,
                    ),
                ).fetchone()
                if active is not None:
                    if active["task_id"] == task_id and active["allocation_revision"] != revision:
                        raise TaskError("active reservation conflicts with requested allocation revision")
                    conn.execute("COMMIT")
                    return SpawnReservation._from_row(active), False
            else:
                for row in rows:
                    if row["state"] in SpawnState.ACTIVE:
                        if row["allocation_revision"] != revision:
                            raise TaskError("active reservation conflicts with requested allocation revision")
                        conn.execute("COMMIT")
                        return SpawnReservation._from_row(row), False
            eligible = task.status == Status.QUEUED and task.owner is None
            if allow_suspended_reembodiment and task.status == Status.SUSPENDED:
                eligible = True
            if not eligible:
                conn.execute("COMMIT")
                raise TaskError(
                    f"task {task_id!r} is {task.status!r} with owner "
                    f"{task.owner!r}; spawn reservation requires queued and unowned"
                )
            attempt = (max(r["attempt"] for r in rows) + 1) if rows else 1
            key = spawn_key(task_id, attempt)
            allocation_reservation_key = (
                latest_allocation["allocation_reservation_key"] or latest_allocation["key"]
                if latest_allocation is not None and not new_allocation else key
            )
            carried_worktree = task.affinity.get("worktree")
            if new_allocation and carried_worktree:
                raise TaskError("explicit target conflicts with a replacement allocation revision")
            worktree_ownership = "targeted" if carried_worktree else None
            carried_session = None
            prior = None
            carried_project = carried_path = carried_host = carried_driver = None
            if latest_allocation is not None and not new_allocation:
                carried_project = latest_allocation["allocation_project"]
            conversation_retired = False
            if (
                carried_worktree is None
                and task.status == Status.SUSPENDED
                and task.owner
                and "/" in task.owner
            ):
                # A suspended task retains its owner (`machine/worktree`) from
                # before suspension -- unlike a fresh queued spawn, this is a
                # REAL, already-checked-out worktree to reuse, not a
                # first-time creation. Without this, `prepare_reusable_worktree`
                # would see an empty `reservation["worktree"]` and create a
                # brand-new worktree, discarding the task's actual context.
                carried_worktree = task.owner.split("/", 1)[1]
                worktree_ownership = "reused"
            if not new_allocation and (task.exclusive_key is not None or rows or allocation_rows):
                if latest_allocation is not None:
                    lineage_column = "creation_key" if latest_allocation["creation_key"] else "task_id"
                    lineage_value = creation_key if latest_allocation["creation_key"] else task_id
                else:
                    lineage_column = "exclusive_key" if task.exclusive_key is not None else "task_id"
                    lineage_value = task.exclusive_key if task.exclusive_key is not None else task_id
                revision_filter = "AND allocation_revision = ? " if latest_allocation is not None else ""
                prior = conn.execute(
                    "SELECT * "
                    "FROM spawn_reservations "
                    f"WHERE {lineage_column} = ? AND worktree IS NOT NULL "
                    f"{revision_filter}"
                    "ORDER BY reserved_at DESC LIMIT 1",
                    (lineage_value, revision) if latest_allocation is not None else (lineage_value,),
                ).fetchone()
                if prior is not None:
                    if carried_worktree and carried_worktree != prior["worktree"]:
                        raise TaskError("explicit target conflicts with retained workspace identity")
                    carried_worktree = prior["worktree"]
                    carried_session = prior["session_handle"]
                    carried_project, carried_path = prior["allocation_project"], prior["worktree_path"]
                    carried_host, carried_driver = prior["creating_host"], prior["driver"]
                    if carried_session is not None:
                        # rearmed counts as retired too (copilot-extensions#4978):
                        # an operator rearm starts the task over, so its carried
                        # session must never be resumed again.
                        retired = conn.execute(
                            "SELECT 1 FROM spawn_reservations "
                            f"WHERE {lineage_column} = ? AND session_handle = ? "
                            "AND release_requested = 1 AND state IN (?, ?, ?) LIMIT 1",
                            (
                                lineage_value,
                                carried_session,
                                SpawnState.SETTLED,
                                SpawnState.FAILED,
                                SpawnState.REARMED,
                            ),
                        ).fetchone()
                        if retired is not None:
                            log.info(
                                "reserve_spawn: dropping retired carried session %r "
                                "for %r (task %s, attempt %s); spawning fresh",
                                carried_session, task.exclusive_key, task_id, attempt,
                            )
                            carried_session = None
                            # Distinct from "never had a carried session" --
                            # a retired conversation must not be recovered via
                            # the bridge's worktree-level resume fallback
                            # either: that fallback resumes whatever session
                            # is latest in the worktree DIRECTORY, independent
                            # of this table's own session_handle bookkeeping,
                            # so it would silently resurrect the very
                            # conversation just dropped above.
                            conversation_retired = True
                        else:
                            log.info(
                                "reserve_spawn: carrying session %r forward for %r "
                                "(task %s, attempt %s); will try to resume it",
                                carried_session, task.exclusive_key, task_id, attempt,
                            )
                            # A genuinely fresh, not-yet-retired handle: this
                            # re-verification is authoritative and must never
                            # be overridden by an older marker a prior row
                            # happened to still carry (e.g. attempt 2 recorded
                            # this very session after attempt 1 was itself
                            # retired -- that marker belongs to the conversation
                            # that is gone, not to this new one).
                            conversation_retired = False
                    else:
                        # Handle-less retry: the prior reservation never
                        # recorded a replacement session at all (it failed,
                        # or hasn't recorded one yet), so the retired-
                        # detection re-verification above never had a handle
                        # to check in the first place. Inherit its own
                        # marker verbatim -- it is the only evidence this
                        # attempt has.
                        conversation_retired = bool(prior["conversation_retired"])
                    worktree_ownership = "reused"
            if carried_worktree is not None:
                cleanup_claims = conn.execute(
                    "SELECT key, task_id, creation_key FROM spawn_reservations "
                    "WHERE worktree = ? AND state IN (?, ?) "
                    "AND conclusion_state = ?",
                    (
                        carried_worktree,
                        SpawnState.FAILED,
                        SpawnState.SETTLED,
                        "pending",
                    ),
                ).fetchall()
                for cleanup_claim in cleanup_claims:
                    previous_task = self._fetch(conn, cleanup_claim["task_id"])
                    if (
                        cleanup_claim["task_id"] == task_id or cleanup_claim["creation_key"] == creation_key
                        or task.dedup_key is not None and previous_task is not None
                        and previous_task.repo == task.repo and previous_task.dedup_key == task.dedup_key
                    ):
                        raise TaskError(
                            f"worktree {carried_worktree!r} has in-flight cleanup {cleanup_claim['key']}; "
                            "allocation identity is retained"
                        )
                    # An independent logical request may reserve its own key,
                    # but cannot inherit a predecessor's cleaning allocation.
                    carried_worktree = carried_session = None
                    carried_path = carried_host = carried_driver = None
                    carried_project = latest_allocation["allocation_project"] if latest_allocation else None
                    worktree_ownership = None
                    conversation_retired = False
                    break
            agent_info = None
            if latest_allocation is not None and latest_allocation["driver"] == "ordinary-bridge" and driver != "ordinary-bridge":
                carried_worktree = carried_session = None
                carried_path = carried_host = carried_driver = None
                worktree_ownership = None
            if driver == "ordinary-bridge" and not new_allocation:
                from .ordinary_bridge import carry_agent
                carried_worktree, carried_session, worktree_ownership, agent_info = carry_agent(
                    conn, task, carried_worktree, carried_session, worktree_ownership,
                    conversation_retired, creation_key=creation_key)
                if prior is not None and carried_worktree == prior["worktree"]:
                    allocation_reservation_key = prior["allocation_reservation_key"] or prior["key"]
            conn.execute(
                "INSERT INTO spawn_reservations "
                "(key, task_id, exclusive_key, attempt, state, reserved_by, "
                "session_handle, worktree, inherited_worktree, "
                "worktree_ownership, conversation_retired, allocation_project, worktree_path, "
                "creating_host, driver, reserved_at, updated_at, allocation_revision, "
                "creation_key, allocation_reservation_key, agent_info) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    key,
                    task_id,
                    task.exclusive_key,
                    attempt,
                    SpawnState.RESERVING,
                    reserved_by,
                    carried_session,
                    carried_worktree,
                    carried_worktree,
                    worktree_ownership,
                    int(conversation_retired),
                    carried_project,
                    carried_path,
                    carried_host,
                    driver if latest_allocation is not None and latest_allocation["driver"] == "ordinary-bridge" else driver or carried_driver,
                    ts,
                    ts,
                    revision,
                    creation_key,
                    allocation_reservation_key,
                    agent_info,
                ),
            )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row), True

    def _update_reservation(
        self,
        key: str,
        *,
        to_state: str,
        allowed_from: frozenset[str],
        now: float | None = None,
        session_handle: str | None = None,
        worktree: str | None = None,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        release_requested: bool = False,
        exclusive_released: bool | None = None,
        guard: Callable[[sqlite3.Row], None] | None = None,
    ) -> SpawnReservation:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in allowed_from:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not one of "
                    f"{sorted(allowed_from)} (cannot -> {to_state!r})"
                )
            if guard is not None:
                try:
                    guard(row)
                except TaskError:
                    conn.execute("COMMIT")
                    raise
            if to_state == SpawnState.DEFERRED and row["release_requested"]:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} has pending release (cannot defer)")
            if (
                conclusion_state is not None
                or conclusion_detail is not None
                or claim_token is not None
            ):
                from .cleanup_recovery import validate_recovery
                validate_recovery(self, conn, row)
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
                if claim_token is not None:
                    newer = _newer_worktree_reservation(conn, row, key)
                    if newer is not None:
                        conn.execute("COMMIT")
                        raise TaskError(f"worktree carried by newer reservation {newer['key']}")
            if worktree is not None:
                if (row["allocation_project"] or row["worktree_path"]) and worktree != row["worktree"]:
                    raise TaskError("recorded workspace identity cannot change")
                cleanup_claims = conn.execute(
                    "SELECT key FROM spawn_reservations "
                    "WHERE key <> ? AND worktree = ? AND state IN (?, ?) "
                    "AND conclusion_state = ?",
                    (
                        key,
                        worktree,
                        SpawnState.FAILED,
                        SpawnState.SETTLED,
                        "pending",
                    ),
                ).fetchall()
                for cleanup_claim in cleanup_claims:
                    conn.execute("COMMIT")
                    raise TaskError(
                        f"worktree {worktree!r} has in-flight cleanup {cleanup_claim['key']}"
                    )
            try:
                conn.execute(
                    "UPDATE spawn_reservations SET state = ?, updated_at = ?, "
                    "session_handle = CASE WHEN ? IS NOT NULL THEN ? ELSE session_handle END, "
                    "worktree = CASE WHEN ? IS NOT NULL THEN ? ELSE worktree END, "
                    "inherited_worktree = CASE "
                    "WHEN inherited_worktree IS NOT NULL THEN inherited_worktree "
                    "WHEN worktree_ownership = 'reused' THEN worktree "
                    "ELSE inherited_worktree END, "
                    "detail = COALESCE(?, detail), "
                    "conclusion_state = CASE WHEN ? IS NOT NULL THEN ? "
                    "ELSE conclusion_state END, "
                    "conclusion_detail = CASE WHEN ? IS NOT NULL THEN ? "
                    "ELSE conclusion_detail END, "
                    "cleanup_claim_expires_at = CASE WHEN ? IS NOT NULL THEN 0 "
                    "ELSE cleanup_claim_expires_at END, "
                    "release_requested = CASE WHEN ? THEN 1 ELSE release_requested END, "
                    "exclusive_released = COALESCE(?, exclusive_released) "
                    "WHERE key = ?",
                    (
                        to_state, ts, session_handle, session_handle, worktree, worktree,
                        detail, conclusion_state, conclusion_state, conclusion_detail,
                        conclusion_detail, claim_token, 1 if release_requested else 0,
                        None if exclusive_released is None else int(exclusive_released), key,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise ExclusiveKeyBusyError(f"{key}: exclusive_key busy: {exc}") from exc
            if to_state in SpawnState.RELEASABLE and row["state"] in SpawnState.ACTIVE:
                latest = conn.execute(
                    "SELECT key FROM spawn_reservations WHERE task_id = ? "
                    "ORDER BY attempt DESC LIMIT 1",
                    (row["task_id"],),
                ).fetchone()
                if latest is not None and latest["key"] == key:
                    conn.execute(
                        "UPDATE tasks SET activity = NULL, activity_updated_at = ? WHERE id = ?",
                        (ts, row["task_id"]),
                    )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)

    def record_staging_spawn(self, key: str, session_handle: str) -> SpawnReservation:
        """Persist an idle body before admission without making it claimable."""
        return self._update_reservation(
            key, to_state=SpawnState.RESERVING,
            allowed_from=frozenset({SpawnState.RESERVING}),
            session_handle=session_handle,
        )

    def record_spawn(
        self,
        key: str,
        *,
        session_handle: str | None = None,
        worktree: str | None = None,
        now: float | None = None,
        authority_expected: tuple | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``spawned`` and record its embody session handle.

        Called right after a successful ``agent-worktrees embody`` launch. The
        handle is what lets a supervisor restart reconcile (join the reservation
        to the live session) instead of re-spawning.
        """
        def authority_guard(row):
            if authority_expected is not None:
                from .ci_repair_spawn import authority_snapshot
                with self._connect() as probe:
                    task = self._fetch(probe, row["task_id"])
                    if authority_snapshot(probe, task) != authority_expected:
                        raise TaskError("CI repair authority changed before spawn publication")
        return self._update_reservation(
            key,
            to_state=SpawnState.SPAWNED,
            allowed_from=frozenset({SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.COLD}),
            session_handle=session_handle,
            worktree=worktree,
            exclusive_released=False,
            now=now,
            guard=authority_guard,
        )

    def record_spawn_worktree(
        self,
        key: str,
        worktree: str,
        *,
        ownership: str = "unknown",
        creating_host: str | None = None,
        driver: str | None = None,
        allocation_project: str | None = None,
        worktree_path: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Record a reusable worktree while the reservation is still reserving.

        Replacing a carried worktree clears its carried session handle: a
        confirmed-missing worktree cannot safely retain a session binding from
        the vanished checkout.
        """
        worktree = worktree.strip()
        if not worktree:
            raise TaskError("spawn worktree must be non-empty")
        if ownership not in {"created", "targeted", "reused", "unknown"}:
            raise TaskError(f"invalid spawn worktree ownership: {ownership!r}")
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
            if row["state"] != SpawnState.RESERVING:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not {SpawnState.RESERVING!r}"
                )
            if row["allocation_project"] or row["worktree_path"]:
                if (
                    row["worktree"] is not None and row["worktree"] != worktree
                    or row["allocation_project"] is not None and row["allocation_project"] != allocation_project
                    or row["worktree_path"] is not None and row["worktree_path"] != worktree_path
                ):
                    raise TaskError("recorded workspace identity cannot change")
            cleanup_claims = conn.execute(
                "SELECT key FROM spawn_reservations "
                "WHERE key <> ? AND worktree = ? AND state IN (?, ?) "
                "AND conclusion_state = ?",
                (
                    key,
                    worktree,
                    SpawnState.FAILED,
                    SpawnState.SETTLED,
                    "pending",
                ),
            ).fetchall()
            for cleanup_claim in cleanup_claims:
                conn.execute("COMMIT")
                raise TaskError(
                    f"worktree {worktree!r} has in-flight cleanup {cleanup_claim['key']}"
                )
            conn.execute(
                "UPDATE spawn_reservations SET "
                "session_handle = CASE "
                "WHEN worktree IS NULL OR worktree <> ? THEN NULL "
                "ELSE session_handle END, "
                "inherited_worktree = CASE "
                "WHEN inherited_worktree IS NOT NULL THEN inherited_worktree "
                "WHEN worktree_ownership = 'reused' THEN worktree "
                "ELSE inherited_worktree END, "
                "worktree = ?, worktree_ownership = ?, creating_host = ?, "
                "driver = ?, allocation_project = ?, worktree_path = ?, updated_at = ? WHERE key = ?",
                (
                    worktree,
                    worktree,
                    ownership,
                    creating_host if ownership == "created" else row["creating_host"],
                    driver,
                    allocation_project,
                    worktree_path,
                    ts,
                    key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def record_spawn_allocation(self, key: str, project: str) -> SpawnReservation:
        """Bind execution routing before the first non-transactional allocation."""
        if not isinstance(project, str) or not project.strip():
            raise TaskError("allocation project must be non-empty")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
            if row is None:
                raise TaskError(f"no such reservation: {key}")
            if row["state"] != SpawnState.RESERVING:
                raise TaskError("allocation binding requires a reserving attempt")
            if row["allocation_project"] is not None and row["allocation_project"] != project:
                raise TaskError("recorded allocation project cannot change")
            conn.execute(
                "UPDATE spawn_reservations SET allocation_project=?, updated_at=? WHERE key=?",
                (project, self._now(None), key),
            )
            updated = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def record_cold(
        self, key: str, *, release_exclusive: bool = False, now: float | None = None
    ) -> SpawnReservation:
        """Mark a spawned headless body intentionally stopped and dormant.

        ``release_exclusive`` drops this reservation's ``exclusive_key`` hold
        while cold, so a sibling may spawn; ``record_spawn`` reacquires it.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.COLD,
            allowed_from=frozenset({SpawnState.SPAWNED, SpawnState.COLD}),
            exclusive_released=release_exclusive or None,
            now=now,
        )

    def fail_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        force: bool = False,
        confirmed_absent: bool = False,
        release_requested: bool = False,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``failed`` (spawn failed or lost), releasing the
        task so a fresh attempt may be reserved.

        Repeating the call on an already-failed row is an idempotent metadata
        update, allowing allocation cleanup to remain inspectable after body
        release.

        ``force`` is an explicit, audited operator override for a
        ``releasing`` reservation with no recorded ``session_handle``, which
        no automatic exact-absence proof (:meth:`retire_spawn`) could ever
        confirm and would otherwise sit ``releasing`` forever
        (copilot-extensions#3179).

        ``confirmed_absent`` additionally permits ``force`` to override a
        **handle-carrying** ``releasing`` row once the caller has
        independently established the handle's session/worktree no longer
        exists -- e.g. ``doctor``'s ``orphaned_worktree_gone`` verdict on a
        reservation whose owning task already went terminal, otherwise
        fencing its ``exclusive_key`` forever (copilot-extensions#3025).
        Never widens plain ``force``'s own handle-less eligibility on its
        own -- masking a still-live orphaned worker requires the caller to
        have done the absence check itself.

        ``release_requested`` marks this ``session_handle`` as retired for
        :meth:`reserve_spawn`'s carried-session lookup. Pass ``True`` only
        once independently confirmed gone -- never for an ordinary failure
        where the body might still be alive and worth resuming.

        The eligibility check runs inside the same transaction as the state
        transition (via ``_update_reservation``'s ``guard``), not a separate
        preceding query -- a concurrent ``record_spawn`` attaching a handle
        in between would otherwise let ``--force`` race past a live handle.
        """
        allowed_from = (SpawnState.ACTIVE - frozenset({SpawnState.RELEASING})) | frozenset(
            {SpawnState.FAILED}
        )
        guard = None
        if force:
            allowed_from = allowed_from | frozenset({SpawnState.RELEASING})

            def guard(row: sqlite3.Row) -> None:
                if (
                    row["state"] == SpawnState.RELEASING
                    and row["session_handle"]
                    and not confirmed_absent
                ):
                    raise TaskError(
                        f"reservation {key} is 'releasing' with a recorded "
                        f"session_handle {row['session_handle']!r} -- pass "
                        "--confirmed-absent if you have independently "
                        "verified it no longer exists (e.g. via "
                        "`agent-dispatch doctor`), or let automatic cleanup/"
                        "`reservations settle` resolve this one instead"
                    )
        return self._update_reservation(
            key,
            to_state=SpawnState.FAILED,
            allowed_from=allowed_from,
            detail=detail,
            conclusion_state=conclusion_state,
            conclusion_detail=conclusion_detail,
            claim_token=claim_token,
            release_requested=release_requested,
            guard=guard,
            now=now,
        )

    def retire_spawn(
        self,
        key: str,
        *,
        exact_absence: bool,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Atomically retire a RELEASING reservation on exact absence proof."""
        if not exact_absence:
            raise TaskError("spawn retirement requires exact absence proof")
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
            if row["state"] != SpawnState.RELEASING:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} is {row['state']!r}, not releasing")
            to_state = (
                SpawnState.SETTLED
                if row["release_disposition"] == "settled"
                else SpawnState.FAILED
            )
            conn.execute(
                "UPDATE spawn_reservations SET state = ?, updated_at = ?, "
                "detail = COALESCE(?, detail), "
                "conclusion_state = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_state END, "
                "conclusion_detail = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_detail END WHERE key = ?",
                (
                    to_state,
                    ts,
                    detail,
                    conclusion_state,
                    conclusion_state,
                    conclusion_detail,
                    conclusion_detail,
                    key,
                ),
            )
            latest = conn.execute(
                "SELECT key FROM spawn_reservations WHERE task_id = ? "
                "ORDER BY attempt DESC LIMIT 1",
                (row["task_id"],),
            ).fetchone()
            if latest is not None and latest["key"] == key:
                conn.execute(
                    "UPDATE tasks SET activity = NULL, activity_updated_at = ? WHERE id = ?",
                    (ts, row["task_id"]),
                )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
        session_handle: str | None = None,
        worktree: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Fence an attempt and atomically retain any failed body identity."""
        if disposition not in {"failed", "settled"}:
            raise TaskError(f"invalid spawn release disposition: {disposition!r}")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] not in SpawnState.ACTIVE:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not active (cannot request release)"
                )
            if worktree and row["worktree"] and row["worktree"] != worktree:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} worktree is {row['worktree']!r}, "
                    f"not {worktree!r}"
                )
            if row["state"] == SpawnState.RELEASING:
                release_disposition = (
                    "failed"
                    if "failed" in {row["release_disposition"], disposition}
                    else row["release_disposition"] or disposition
                )
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested = 1, "
                    "release_disposition = ?, detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "updated_at = ? WHERE key = ?",
                    (
                        release_disposition,
                        detail,
                        session_handle,
                        worktree,
                        ts,
                        key,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE spawn_reservations SET state = ?, "
                    "release_requested = 1, release_disposition = ?, "
                    "detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "conclusion_state = COALESCE(conclusion_state, ?), "
                    "updated_at = ? WHERE key = ?",
                    (
                        SpawnState.RELEASING,
                        disposition,
                        detail,
                        session_handle,
                        worktree,
                        "pending",
                        ts,
                        key,
                    ),
                )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)

    def settle_spawn(
        self,
        key: str,
        *,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        claim_token: str | None = None,
        release_requested: bool = False,
        now: float | None = None,
    ) -> SpawnReservation:
        """Mark a reservation ``settled`` (its task reached a terminal outcome).

        Repeating the call on an already-settled row is an idempotent detail
        update. Once exact body absence is proven, allocation cleanup may remain
        ``pending`` or ``held`` here as inspectable conclusion metadata without
        retaining an active spawn fence.

        ``release_requested`` -- see :meth:`fail_spawn`'s doc: pass ``True``
        only once the caller has independently confirmed the body is gone.
        """
        return self._update_reservation(
            key,
            to_state=SpawnState.SETTLED,
            allowed_from=(SpawnState.ACTIVE - frozenset({SpawnState.RELEASING}))
            | frozenset({SpawnState.SETTLED}),
            detail=detail,
            conclusion_state=conclusion_state,
            conclusion_detail=conclusion_detail,
            claim_token=claim_token,
            release_requested=release_requested,
            now=now,
        )
