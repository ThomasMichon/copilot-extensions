from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import tracking


def _tracking():
    from . import tracking as tracking_mod

    return tracking_mod


class SessionLifecycleError(ValueError):
    """Raised when an asserted session transition names an unknown session."""


def _next_lifecycle_revision(
    record: tracking.WorktreeRecord,
    *session_ids: str,
) -> int:
    highest = max((transition.revision for transition in record.head_transitions), default=0)
    record.lifecycle_revision = max(record.lifecycle_revision, highest) + 1
    dirty = set(getattr(record, "_session_projection_dirty", set()))
    for session_id in session_ids:
        entry = record.session_entry(session_id)
        if entry is None:
            continue
        entry.relation_revision = record.lifecycle_revision
        dirty.add(session_id)
    record._session_projection_dirty = dirty
    return record.lifecycle_revision


def _append_head_transition(
    record: tracking.WorktreeRecord,
    session_id: str | None,
    *,
    reason: str,
    handoff_ordinal: int | None = None,
    at: str | None = None,
    related_session_ids: tuple[str, ...] = (),
) -> tracking.HeadTransition:
    tracking = _tracking()
    if session_id is not None and record.session_entry(session_id) is None:
        raise SessionLifecycleError(
            f"session {session_id} is not tracked on worktree {record.worktree_id}"
        )
    prior_head = record.resolved_head_session
    affected = tuple(
        dict.fromkeys(
            session
            for session in (prior_head, session_id, *related_session_ids)
            if session is not None
        )
    )
    transition = tracking.HeadTransition(
        revision=_next_lifecycle_revision(record, *affected),
        session_id=session_id,
        reason=reason,
        at=at or tracking._now_iso(),
        handoff_ordinal=handoff_ordinal,
    )
    record.head_transitions.append(transition)
    record.head_session = session_id
    record.head_revision = transition.revision
    return transition


def repair_head_cache(record: tracking.WorktreeRecord) -> bool:
    transition = record.replayed_head_transition
    if transition is None:
        expected = record.resolved_head_session
        if record.head_session is None or record.head_session == expected:
            return False
        _append_head_transition(record, expected, reason="legacy-cache-repair")
        return True
    expected = record.replayed_head_session
    if record.head_session == expected and record.head_revision == transition.revision:
        return False
    record.head_session = expected
    record.head_revision = transition.revision
    return True


def _ensure_head_ledger(record: tracking.WorktreeRecord) -> None:
    if record.head_transitions:
        return
    legacy_head = record.resolved_head_session
    if legacy_head is not None:
        _append_head_transition(record, legacy_head, reason="legacy-import")


def open_handoff(
    record: tracking.WorktreeRecord,
    predecessor_id: str,
    token: str,
    *,
    opened_at: str | None = None,
    save: bool = True,
) -> tracking.SessionHandoff:
    tracking = _tracking()
    if not token:
        raise SessionLifecycleError("handoff token must not be empty")
    predecessor = record.session_entry(predecessor_id)
    if predecessor is None:
        raise SessionLifecycleError(
            f"predecessor {predecessor_id} is not tracked on worktree {record.worktree_id}"
        )
    for existing in record.handoffs:
        if existing.token != token:
            continue
        if existing.predecessor != predecessor_id:
            raise SessionLifecycleError(
                f"handoff token {token} already belongs to predecessor {existing.predecessor}"
            )
        return existing
    _ensure_head_ledger(record)
    for existing in record.handoffs:
        if existing.predecessor == predecessor_id and existing.state == "pending":
            existing.state = "cancelled"
    record.handoff_counter = max(
        record.handoff_counter,
        max((handoff.ordinal for handoff in record.handoffs), default=0),
    ) + 1
    handoff = tracking.SessionHandoff(
        ordinal=record.handoff_counter,
        token=token,
        predecessor=predecessor_id,
        state="pending",
        opened_at=opened_at or tracking._now_iso(),
    )
    record.handoffs.append(handoff)
    if predecessor.state == "active":
        predecessor.state = "yielded"
    _next_lifecycle_revision(record, predecessor_id)
    if save:
        tracking.save_record(record)
    return handoff


def link_handoff(
    record: tracking.WorktreeRecord,
    token: str,
    successor_id: str,
    *,
    linked_at: str | None = None,
    save: bool = True,
) -> tracking.SessionHandoff:
    tracking = _tracking()
    successor = record.session_entry(successor_id)
    if successor is None:
        raise SessionLifecycleError(
            f"successor {successor_id} is not tracked on worktree {record.worktree_id}"
        )
    if successor.state in tracking._CONCLUDED_SESSION_STATES:
        raise SessionLifecycleError(
            f"successor {successor_id} is already {successor.state}"
        )
    handoff = next((candidate for candidate in record.handoffs if candidate.token == token), None)
    if handoff is None:
        raise SessionLifecycleError(
            f"handoff token {token} is not tracked on worktree {record.worktree_id}"
        )
    if handoff.state == "linked":
        if handoff.successor != successor_id:
            raise SessionLifecycleError(
                f"handoff token {token} is already linked to {handoff.successor}"
            )
        return handoff
    if handoff.state != "pending":
        raise SessionLifecycleError(f"handoff token {token} is {handoff.state}, not pending")
    if handoff.candidate and handoff.candidate != successor_id:
        raise SessionLifecycleError(
            f"handoff token {token} is associated with candidate {handoff.candidate}, not {successor_id}"
        )
    predecessor = record.session_entry(handoff.predecessor)
    if predecessor is None:
        raise SessionLifecycleError(
            f"handoff predecessor {handoff.predecessor} is not tracked on worktree {record.worktree_id}"
        )
    if predecessor.state == "concluded":
        raise SessionLifecycleError(
            f"handoff predecessor {predecessor.session_id} was explicitly concluded"
        )
    if predecessor.successor is not None and predecessor.successor != successor_id:
        raise SessionLifecycleError(
            f"handoff predecessor {predecessor.session_id} already links to {predecessor.successor}"
        )
    if successor.predecessor is not None and successor.predecessor != predecessor.session_id:
        raise SessionLifecycleError(
            f"handoff successor {successor_id} already follows {successor.predecessor}"
        )
    predecessor.state = "handed-off"
    predecessor.successor = successor_id
    successor.state = "active"
    successor.predecessor = predecessor.session_id
    handoff.state = "linked"
    handoff.successor = successor_id
    handoff.linked_at = linked_at or tracking._now_iso()
    _append_head_transition(
        record,
        successor_id,
        reason="handoff-linked",
        handoff_ordinal=handoff.ordinal,
        at=handoff.linked_at,
        related_session_ids=(predecessor.session_id,),
    )
    if save:
        tracking.save_record(record)
    return handoff


def associate_handoff_candidate(
    record: tracking.WorktreeRecord,
    token: str,
    session_id: str,
    *,
    associated_at: str | None = None,
    save: bool = True,
) -> tracking.SessionHandoff:
    tracking = _tracking()
    successor = record.session_entry(session_id)
    if successor is None:
        raise SessionLifecycleError(
            f"candidate {session_id} is not tracked on worktree {record.worktree_id}"
        )
    handoff = next((candidate for candidate in record.handoffs if candidate.token == token), None)
    if handoff is None:
        raise SessionLifecycleError(
            f"handoff token {token} is not tracked on worktree {record.worktree_id}"
        )
    if handoff.state == "linked":
        if handoff.successor != session_id:
            raise SessionLifecycleError(
                f"handoff token {token} is already linked to {handoff.successor}"
            )
        return handoff
    if handoff.state != "pending":
        raise SessionLifecycleError(f"handoff token {token} is {handoff.state}, not pending")
    if handoff.candidate and handoff.candidate != session_id:
        raise SessionLifecycleError(
            f"handoff token {token} already has candidate {handoff.candidate}"
        )
    if handoff.candidate != session_id:
        handoff.candidate = session_id
        handoff.candidate_at = associated_at or tracking._now_iso()
        _next_lifecycle_revision(record)
    if save:
        tracking.save_record(record)
    return handoff


def _cancel_pending_handoffs(record: tracking.WorktreeRecord) -> bool:
    changed = False
    for handoff in record.handoffs:
        if handoff.state == "pending":
            handoff.state = "cancelled"
            changed = True
    return changed


def _handoff_state(record: tracking.WorktreeRecord, token: str) -> str | None:
    handoff = next((handoff for handoff in record.handoffs if handoff.token == token), None)
    return handoff.state if handoff is not None else None


def _pending_handoffs_all_from_yielded(record: tracking.WorktreeRecord) -> bool:
    for handoff in record.pending_handoffs:
        predecessor = record.session_entry(handoff.predecessor)
        if predecessor is None or predecessor.state != "yielded":
            return False
    return True


def set_head_session(
    record: tracking.WorktreeRecord,
    session_id: str,
    *,
    save: bool = True,
) -> None:
    tracking = _tracking()
    _ensure_head_ledger(record)
    if record.resolved_head_session != session_id:
        _append_head_transition(record, session_id, reason="adopted")
    if save:
        tracking.save_record(record)


def conclude_session(
    record: tracking.WorktreeRecord,
    session_id: str,
    *,
    state: tracking.SessionState = "concluded",
    handoff_token: str | None = None,
    save: bool = True,
) -> None:
    tracking = _tracking()
    if state not in tracking._CONCLUDED_SESSION_STATES:
        raise SessionLifecycleError(
            f"conclude state must be one of {tracking._CONCLUDED_SESSION_STATES}, got {state!r}"
        )
    entry = record.session_entry(session_id)
    if entry is None:
        raise SessionLifecycleError(
            f"session {session_id} is not tracked on worktree {record.worktree_id}"
        )
    current = record.resolved_head_session
    prior_state = entry.state
    _ensure_head_ledger(record)
    entry.state = state
    if state == "handed-off" and handoff_token and not any(
        handoff.predecessor == session_id and handoff.state in ("pending", "linked")
        for handoff in record.handoffs
    ):
        open_handoff(record, session_id, handoff_token, save=False)
    if current == session_id:
        pending = next(
            (
                handoff
                for handoff in reversed(record.handoffs)
                if handoff.predecessor == session_id and handoff.state == "pending"
            ),
            None,
        )
        _append_head_transition(
            record,
            None,
            reason=state,
            handoff_ordinal=pending.ordinal if pending else None,
        )
    elif prior_state != state:
        _next_lifecycle_revision(record, session_id)
    if save:
        tracking.save_record(record)


def link_succession(
    record: tracking.WorktreeRecord,
    predecessor_id: str,
    successor_id: str,
    *,
    predecessor_state: tracking.SessionState = "handed-off",
    handoff_token: str | None = None,
    save: bool = True,
) -> None:
    tracking = _tracking()
    pred = record.session_entry(predecessor_id)
    succ = record.session_entry(successor_id)
    if pred is None:
        raise SessionLifecycleError(
            f"predecessor {predecessor_id} is not tracked on worktree {record.worktree_id}"
        )
    if succ is None:
        raise SessionLifecycleError(
            f"successor {successor_id} is not tracked on worktree {record.worktree_id}"
        )
    if predecessor_state == "handed-off":
        token = handoff_token or f"manual-{record.handoff_counter + 1}"
        handoff = open_handoff(record, predecessor_id, token, save=False)
        link_handoff(record, handoff.token, successor_id, save=False)
    else:
        pred.successor = successor_id
        pred.state = predecessor_state
        succ.predecessor = predecessor_id
        _ensure_head_ledger(record)
        _append_head_transition(
            record,
            successor_id,
            reason="succession-linked",
            related_session_ids=(pred.session_id,),
        )
    if save:
        tracking.save_record(record)


def create_new_record(
    worktree_id: str,
    branch: str,
    worktree_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
    *,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    interface: tracking.WorktreeInterface | None = None,
    origin: tracking.WorktreeOrigin | None = None,
    dispatch_attempt: tracking.DispatchAttempt | None = None,
    parent_session: str | None = None,
    caller_worktree: str | None = None,
    owner_ref: str | None = None,
    pair_id: str | None = None,
    pair_role: str | None = None,
    pair_ref: str | None = None,
    pair_kind: str | None = None,
    codename: str | None = None,
    codename_source: str | None = None,
    bound_agent: str | None = None,
) -> tracking.WorktreeRecord:
    tracking = _tracking()
    from .tracking_controller_relations import (
        _derive_initial_controller_relations,
        _mark_controller_projection_dirty,
    )

    now = tracking._now_iso()
    normalized_parent_session = parent_session or None
    normalized_caller_worktree = caller_worktree or None
    normalized_owner_ref = owner_ref or None
    normalized_bound_agent = (bound_agent or "").strip() or None
    try:
        controllers, controller_revision = _derive_initial_controller_relations(
            machine=machine,
            project=repo,
            owner_ref=normalized_owner_ref,
            caller_worktree=normalized_caller_worktree,
            parent_session=normalized_parent_session,
            created_at=now,
        )
    except tracking.ControllerRelationError:
        controllers, controller_revision = [], 0
    record = tracking.WorktreeRecord(
        worktree_id=worktree_id,
        branch=branch,
        worktree_path=worktree_path,
        repo=repo,
        machine=machine,
        platform=platform_name,
        started_at=now,
        last_resumed_at=now,
        resume_count=0,
        title=None,
        status="active",
        completed_at=None,
        sessions=[],
        kind=kind,
        owner=owner,
        interface=interface,
        origin=origin,
        dispatch_attempt=dispatch_attempt,
        parent_session=normalized_parent_session,
        controller_revision=controller_revision,
        controllers=controllers,
        caller_worktree=normalized_caller_worktree,
        owner_ref=normalized_owner_ref,
        pair_id=pair_id or None,
        pair_role=pair_role or None,
        pair_ref=pair_ref or None,
        pair_kind=pair_kind or None,
        codename=codename or None,
        codename_source=codename_source or None,
        bound_agent=normalized_bound_agent,
    )
    _mark_controller_projection_dirty(
        record, *(relation.controller_session_id for relation in controllers)
    )
    path = tracking_path / f"{worktree_id}.yaml"
    tracking.save_record(record, path)
    return record


def create_new_record_if_absent(
    worktree_id: str,
    branch: str,
    worktree_path: str,
    repo: str,
    machine: str,
    platform_name: str,
    tracking_path: Path,
    *,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    interface: tracking.WorktreeInterface | None = None,
    origin: tracking.WorktreeOrigin | None = None,
    checkout_managed: bool = True,
) -> tuple[tracking.WorktreeRecord, bool]:
    tracking = _tracking()
    path = tracking_path / f"{worktree_id}.yaml"
    tracking_path.mkdir(parents=True, exist_ok=True)
    with tracking._RecordLock(path, require_sidecar=True):
        if path.exists():
            return tracking.load_record(path), False
        now = tracking._now_iso()
        record = tracking.WorktreeRecord(
            worktree_id=worktree_id,
            branch=branch,
            worktree_path=worktree_path,
            repo=repo,
            machine=machine,
            platform=platform_name,
            started_at=now,
            last_resumed_at=now,
            resume_count=0,
            title=None,
            status="active",
            completed_at=None,
            sessions=[],
            kind=kind,
            owner=owner,
            interface=interface,
            origin=origin,
            checkout_managed=checkout_managed,
        )
        tracking._save_record_unlocked(record, path)
    tracking._flush_session_projections(record)
    return record, True
