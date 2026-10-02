"""``session_register`` verb.

``agent-worktrees-authoritative-daemon`` effort, Phase 3 -- the seventh
migrated call-site cluster, and the first ever taken from the
sessionStart-hook-critical path: ``tracking_session_registry.register_session``
itself (previously inline). Every earlier cluster deliberately avoided this
exact function -- Phase 2's own plan named it the highest-risk case, and
Phase 3's fourth cluster's design survey again deferred it, both times for
the same reason: any bug here breaks session registration for every single
worktree session, not just one CLI command.

Landed now (operator direction, 2026-09-27) because six clusters already
proved the verb-migration template end-to-end, AND because the alternative
considered first -- extracting only the function's internal ``link_handoff``
call as a narrower verb -- does not actually reduce risk. That call is one
conditional branch inside a single, deeply interdependent transaction
(resource-claim add, session-activation state, conditional handoff-linking
OR head-transition/pending-handoff cancellation, all sharing one
``_RecordLock``, with the function's own return value depending on which
branch fires) -- pulling only the branch out would split one atomic write
into two, which is a real regression, not a simplification. Verb-ifying the
WHOLE transaction, as this module does, keeps the atomicity guarantee intact
and is the same shape every other cluster already uses.

**Latency, not just correctness, is the operative constraint here** (the
reason the call site below passes ``boot_wait_s=0`` rather than the default
4s): a sessionStart hook runs synchronously in the path of every session
launch. ``tracking_write.write_with_boot``'s boot-wait exists to give a
*freshly spawned* daemon a few seconds to come up before falling back --
appropriate for an explicit CLI command a human or agent is already waiting
on, never for a hook that blocks the interactive session itself. Passing
``boot_wait_s=0`` still fires ``ensure_monitor()`` (a non-blocking
``subprocess.Popen`` spawn, warming the daemon for the *next* session) but
never spin-waits for it: an already-warm daemon is dialed immediately (the
overwhelmingly common case, since the resident monitor is normally kept
running continuously once started); a cold/unreachable one falls straight
through to the identical in-process code with no added wait at all,
matching this function's pre-migration latency exactly.

Any ambient-context read the original inline transaction needed (project,
tracking dir) was ALREADY resolved by the caller before entering its own
lock (``tracking._owning_tracking_dir(worktree_id)``, computed once at the
top of the pre-migration function) -- so this migration required no new
caller-side resolution, unlike verbs whose pre-migration call sites read
ambient config from inside their own locked block.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from . import obligations, tracking, tracking_write
from .tracking_session_registry import _start_session_activation


def head_is_provably_dead(head_session: str | None) -> bool:
    """True only when ``head_session``'s conversation exists on this machine
    and no live Copilot holds its ``inuse.<pid>.lock``.

    A head whose directory isn't here (or that can't be read) is never
    called dead, so a live head is never displaced on uncertain evidence.
    """
    if not head_session:
        return False
    try:
        from . import sessions

        state_dir = sessions._session_state_dir()
        if not (state_dir / head_session).is_dir():
            return False
        live_pid, _stale = sessions._session_entry_lock_state(state_dir, head_session)
        return live_pid is None
    except Exception:
        return False


def head_hold_note(session_id: str, head: str | None) -> str:
    """A bind-session stderr note when ``session_id`` didn't become the head
    (never silent about who still holds it); empty when it did."""
    if not head or head == session_id:
        return ""
    why = "" if head_is_provably_dead(head) else " (its Copilot is still running)"
    return f"bind-session: {session_id} is bound but NOT the head; the head is still {head}{why}.\n"


def apply_session_register(args: dict) -> dict:
    """Registered as the ``session_register`` verb. Mirrors the former
    ``tracking_session_registry.register_session`` transaction exactly --
    every branch, in the same order, under the same single
    ``tracking._RecordLock`` -- so behavior (including which branch runs
    and what it returns) is unchanged; only the wire shape differs. Returns
    ``{"error": "lifecycle", "message": ...}`` for a
    ``tracking.SessionLifecycleError`` rejection (a terminal/managed
    worktree, or an invalid handoff-link state), never raising -- the
    caller (``register_session``) re-raises it, matching the pre-migration
    contract of letting this exception propagate synchronously to every
    existing call site's own broad exception handling."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    session_id = args["session_id"]
    pid = args.get("pid")
    pane_id = args.get("pane_id")
    started_at = args.get("started_at")
    source = args.get("source", "hook")
    recorded_at = args.get("recorded_at")
    handoff_token = args.get("handoff_token")
    candidate_token = args.get("candidate_token")
    initial_projection = bool(args.get("initial_projection"))

    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        if (
            record.kind in tracking.MANAGED_KINDS
            and record.status in {"complete", "completed", "finalized"}
        ):
            return {
                "error": "lifecycle",
                "message": (
                    f"worktree {worktree_id} is terminal and managed; "
                    "refusing new session activation"
                ),
            }
        if record.sessions is None:
            record.sessions = []
        event_at = started_at or tracking._now_iso()
        observed_at = recorded_at or tracking._now_iso()
        tracking._ensure_head_ledger(record)
        self_session_ref = tracking.format_claim_ref(
            record.machine, record.repo, record.worktree_id, session=session_id
        )
        tracking.add_resource_claim(
            record,
            tracking.ResourceClaim(
                kind="session",
                ref=self_session_ref,
                created_at=event_at,
                state=obligations.ACTIVE,
                note="live Copilot session",
            ),
            save=False,
        )

        def _link_if_fresh() -> tracking.SessionHandoff | None:
            prior = next(
                (h for h in record.handoffs if h.token == handoff_token), None,
            )
            already_linked = (
                prior is not None
                and prior.state == "linked"
                and prior.successor == session_id
            )
            linked = tracking.link_handoff(
                record, handoff_token, session_id, linked_at=event_at, save=False,
            )
            return None if already_linked else linked

        for entry in record.sessions:
            if entry.session_id != session_id:
                continue
            activation_added = _start_session_activation(
                entry, event_at=event_at, recorded_at=observed_at, source=source,
            )
            if pid:
                entry.pid = pid
            if pane_id:
                entry.pane_id = pane_id
            if handoff_token and tracking._handoff_state(record, handoff_token) == "cancelled":
                handoff_token = None
            if handoff_token:
                try:
                    linked_handoff = _link_if_fresh()
                except tracking.SessionLifecycleError as exc:
                    if activation_added:
                        tracking._next_lifecycle_revision(record, session_id)
                    tracking.save_record(record, yaml_path)
                    return {"error": "lifecycle", "message": str(exc)}
                tracking.save_record(record, yaml_path)
                return {
                    "ok": True,
                    "linked_handoff": (
                        dataclasses.asdict(linked_handoff)
                        if linked_handoff is not None else None
                    ),
                }
            current_head = record.resolved_head_session
            vacant_claim = current_head is None and (
                entry.state == "active"
                or (
                    entry.state == "yielded"
                    and (
                        not record.head_transitions
                        or record.head_transitions[-1].session_id == session_id
                    )
                )
            )
            # A head whose Copilot is gone never blocks the session actually
            # running here: a resumed session (or an explicit bind, from any
            # prior state) takes over, so the ledger can't stay stuck on it.
            dead_claim = (
                current_head is not None
                and current_head != session_id
                and (entry.state in ("active", "yielded") or source == "bind")
                and head_is_provably_dead(current_head)
            )
            if (
                not candidate_token
                and (vacant_claim or dead_claim)
                and (
                    source == "bind"
                    or tracking._pending_handoffs_all_from_yielded(record)
                )
            ):
                # A session reclaiming its OWN prior "yielded" state (it opened
                # a handoff that was never formally linked to a successor) is
                # a supported recovery, not a bug: flip it back to "active" as
                # part of the same rebind that cancels its stale pending
                # handoff(s). This loop only ever reaches the entry matching
                # `session_id` (the registering session itself), so this can
                # never let an unrelated session reclaim another session's
                # yielded state. The raw-latest-head-transition check mirrors
                # `cancel_handoff`'s `predecessor_is_latest_head` guard: without
                # it, an OLDER yielded session could steal head back from a
                # NEWER yielded lineage -- `resolved_head_session` deliberately
                # hides every yielded session, so a genuinely newer head that
                # has since yielded its own handoff would also read as "no
                # head" here. That guard doesn't apply to a dead head: its
                # process is gone, so the running session takes over.
                if entry.state != "active":
                    entry.state = "active"
                tracking._cancel_pending_handoffs(record)
                tracking._append_head_transition(
                    record, session_id,
                    reason="reclaim" if dead_claim and not vacant_claim else "rebind",
                    at=event_at,
                )
            elif activation_added:
                tracking._next_lifecycle_revision(record, session_id)
            tracking.save_record(record, yaml_path)
            return {"ok": True, "linked_handoff": None}

        current_head = record.resolved_head_session
        dead_head = current_head is not None and head_is_provably_dead(current_head)
        had_active_head = current_head is not None and not dead_head
        new_entry = tracking.SessionEntry(
            session_id=session_id,
            started_at=event_at,
            pid=pid,
            pane_id=pane_id,
            activations=[
                tracking.SessionActivation(
                    ordinal=1,
                    started_at=event_at,
                    start_recorded_at=observed_at,
                    start_source=source,
                )
            ],
        )
        record.sessions.append(new_entry)
        tracking._next_lifecycle_revision(record, session_id)
        if initial_projection and record.controller_for_session(session_id) is None:
            initial_sessions = set(
                getattr(record, "_session_projection_initial_registration", set())
            )
            initial_sessions.add(session_id)
            record._session_projection_initial_registration = initial_sessions
        linked_handoff = None
        if handoff_token and tracking._handoff_state(record, handoff_token) == "cancelled":
            handoff_token = None
        if handoff_token:
            try:
                linked_handoff = _link_if_fresh()
            except tracking.SessionLifecycleError as exc:
                tracking.save_record(record, yaml_path)
                return {"error": "lifecycle", "message": str(exc)}
        elif not candidate_token and not had_active_head and (
            source == "bind" or tracking._pending_handoffs_all_from_yielded(record)
        ):
            tracking._cancel_pending_handoffs(record)
            tracking._append_head_transition(
                record,
                session_id,
                reason=(
                    "reclaim" if dead_head
                    else "rebind" if source == "bind" else "initial"
                ),
                at=event_at,
            )
        tracking.save_record(record, yaml_path)
        return {
            "ok": True,
            "linked_handoff": (
                dataclasses.asdict(linked_handoff) if linked_handoff is not None else None
            ),
        }


tracking_write.register_verb("session_register", apply_session_register)
