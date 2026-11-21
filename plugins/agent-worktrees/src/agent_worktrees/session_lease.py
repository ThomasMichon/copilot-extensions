"""Session self-claims as activity leases.

A ``kind="session"`` claim is renewed by activity Copilot already records:
every tool call and user message appends to the session's ``events.jsonl``.
Reusing those modification times adds no hook writes. A lease that has not
been renewed within its TTL is treated as released, so a missed
``sessionEnd`` cannot pin a worktree forever. Missing or unreadable activity
evidence is never proof of expiry: those verdicts are ``None``. Only an
absent optional activity file is skipped; any other stat failure makes the
whole verdict unknown.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from . import obligations, session_self_claims, tracking, tracking_claims

#: Idle period after which an ``active`` session lease is released.
ACTIVE_LEASE_TTL_S = 2 * 60 * 60
#: Shorter idle grace for an ``at-rest`` session claim on a finalized record.
FINALIZED_AT_REST_GRACE_S = 30 * 60

_TTL_ENV = "AGENT_WORKTREES_SESSION_LEASE_TTL_S"
_GRACE_ENV = "AGENT_WORKTREES_SESSION_LEASE_GRACE_S"
_ACTIVITY_FILES = ("events.jsonl", "workspace.yaml", "session.db")


def _seconds(env: str, default: int) -> int:
    try:
        value = int(os.environ.get(env, ""))
    except ValueError:
        return default
    return value if value > 0 else default


def active_ttl() -> int:
    return _seconds(_TTL_ENV, ACTIVE_LEASE_TTL_S)


def finalized_grace() -> int:
    return _seconds(_GRACE_ENV, FINALIZED_AT_REST_GRACE_S)


def last_activity(session_id: str) -> float | None:
    """Newest activity timestamp for a local session, or ``None`` if unknown."""
    if not session_id or "/" in session_id or "\\" in session_id or session_id in {".", ".."}:
        return None
    from . import sessions

    directory = Path(sessions._session_state_dir()) / session_id
    stamps: list[float] = []
    for name in _ACTIVITY_FILES:
        try:
            stamps.append((directory / name).stat().st_mtime)
        except (FileNotFoundError, NotADirectoryError):
            continue
        except OSError:
            return None
    return max(stamps) if stamps else None


def ttl_for(record: object, claim_state: str | None) -> int:
    """Lease TTL for a claim in ``claim_state`` on ``record``."""
    status = getattr(record, "status", None)
    if status == "finalized" and obligations.normalize(claim_state or "") != obligations.ACTIVE:
        return finalized_grace()
    return active_ttl()


def session_expired(
    session_id: str, ttl: int, *, now: float | None = None,
) -> bool | None:
    stamp = last_activity(session_id)
    if stamp is None:
        return None
    return ((time.time() if now is None else now) - stamp) > ttl


def lease_expired(
    record: object, claim: tracking_claims.ResourceClaim, *, now: float | None = None,
) -> bool | None:
    """Tri-state lease verdict for a session claim: True only on stale evidence."""
    if claim.kind != "session":
        return None
    parsed = tracking_claims.parse_claim_ref(claim.ref)
    if parsed is None or not parsed.session:
        return None
    return session_expired(parsed.session, ttl_for(record, claim.state), now=now)


def expired_finalized_self_claim(
    record: tracking.WorktreeRecord,
    claim: tracking_claims.ResourceClaim,
    *,
    now: float | None = None,
) -> bool:
    """True for a finalized record's own at-rest session claim whose lease expired.

    Such a claim no longer blocks cleanup or retirement. Only positive
    expiry evidence counts; non-finalized records, other kinds, claims that
    are not this record's exact self claim (project, worktree, tracked
    session and receiving-local owner) and unknown activity keep it held.
    """
    if record.status != "finalized" or claim.kind != "session" or claim.has_producer:
        return False
    if not claim.is_live or claim.is_unsettled:
        return False
    if not _is_self_claim(record, claim):
        return False
    return lease_expired(record, claim, now=now) is True


def _is_self_claim(
    record: tracking.WorktreeRecord, claim: tracking_claims.ResourceClaim,
) -> bool:
    parsed = tracking_claims.parse_claim_ref(claim.ref)
    if parsed is None or not parsed.session:
        return False
    return any(
        own is claim for own in session_self_claims.self_claims(record, parsed.session)
    )


def release_expired_finalized(
    record: tracking.WorktreeRecord, *, now: float | None = None,
) -> list[tracking_claims.ResourceClaim]:
    """Settle expired finalized self session claims as released, in memory.

    Callers persist the record under its tracking lock.
    """
    released = [
        claim for claim in record.resources
        if expired_finalized_self_claim(record, claim, now=now)
    ]
    for claim in released:
        claim.state = obligations.RELEASED
    return released


def idle_finalized(
    record: tracking.WorktreeRecord,
    session_ids: Iterable[str] = (),
    *,
    now: float | None = None,
) -> bool:
    """True when only expired session leases keep a finalized record live.

    Every known session (observed live ids plus live self-claims) must have
    positive expiry evidence; no known session, a live session claim that is
    not an exact self claim, or any unknown verdict keeps the conservative
    live classification.
    """
    if record.status != "finalized":
        return False
    states: dict[str, str] = {sid: obligations.AT_REST for sid in session_ids if sid}
    for claim in record.resources:
        if claim.kind != "session" or not claim.is_live:
            continue
        parsed = tracking_claims.parse_claim_ref(claim.ref)
        if parsed is None or not parsed.session or not _is_self_claim(record, claim):
            return False
        if obligations.normalize(claim.state) == obligations.ACTIVE:
            states[parsed.session] = obligations.ACTIVE
        else:
            states.setdefault(parsed.session, claim.state)
    if not states:
        return False
    return all(
        session_expired(sid, ttl_for(record, state), now=now) is True
        for sid, state in states.items()
    )


def _latched(record: tracking.WorktreeRecord) -> bool:
    stamp = record.finalized_checkout
    return isinstance(stamp, dict) and isinstance(stamp.get("head"), str) and bool(stamp["head"])


def drop_expired_finalized(
    records: Iterable[tracking.WorktreeRecord],
    active_sessions: Mapping[str, Iterable[str]],
    active: set[str],
    *,
    keep: Callable[[tracking.WorktreeRecord], bool],
) -> None:
    """Remove finalized paths kept live only by expired session leases.

    Finalize is a latch: continued conversation, an idle open pane or a
    resumed session must not make a finalized checkout ACTIVE once its lease
    expired. ``keep`` preserves activity with no provable lease (hosted sessions,
    workers, bridge or bound-live signals). Only a stamped latch
    (``finalized_checkout``) can expire: without it later commits cannot be
    told from finalized content, so a legacy record stays active. Any probe
    failure leaves the path active.
    """
    from . import sessions

    for rec in records:
        if not rec.worktree_path:
            continue
        norm = sessions._normalize_path(rec.worktree_path)
        if norm not in active:
            continue
        try:
            if not keep(rec) and finalized_lease_released(
                rec, observed_sessions(active_sessions, rec.worktree_path),
            ):
                active.discard(norm)
        except Exception:
            continue


def observed_sessions(active_sessions: Mapping[str, Iterable[str]], path: str) -> set[str]:
    """Session ids observed at ``path`` under any spelling of that path."""
    from . import sessions

    norm = sessions._normalize_path(path)
    found: set[str] = set()
    for candidate, sids in active_sessions.items():
        if sessions._normalize_path(candidate) == norm:
            found.update(sids or ())
    return found


def finalized_lease_released(
    record: tracking.WorktreeRecord,
    session_ids: Iterable[str] = (),
    *,
    now: float | None = None,
) -> bool:
    """The one lease-aware eligibility predicate for finalized checkouts.

    True only for a stamped finalized latch whose every known session lease
    has positively expired. Both the pre-lock active-path scan and the
    lock-held cleanup revalidation decide through this predicate, so they
    can neither disagree about an expired lease nor accept a renewed one.
    """
    return (
        record.status == "finalized"
        and bool(record.worktree_path)
        and _latched(record)
        and idle_finalized(record, session_ids, now=now)
    )


def pane_holds_checkout(
    record: tracking.WorktreeRecord,
    active_paths: set[str],
    active_sessions: Mapping[str, Iterable[str]],
) -> bool:
    """Whether an open mux pane still keeps ``record``'s checkout live.

    It does unless the shared :func:`finalized_lease_released` predicate --
    the same one the pre-lock scan applied -- proves every session lease on
    this finalized latch expired.
    """
    from . import sessions

    if not record.worktree_path:
        return True
    if sessions._normalize_path(record.worktree_path) in active_paths:
        return True
    return not finalized_lease_released(
        record, observed_sessions(active_sessions, record.worktree_path),
    )
