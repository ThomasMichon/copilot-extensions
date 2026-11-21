"""Session self-claims as activity leases.

A ``kind="session"`` claim is renewed by activity Copilot already records:
every tool call and user message appends to the session's ``events.jsonl``.
Reusing those modification times adds no hook writes. A lease that has not
been renewed within its TTL is treated as released, so a missed
``sessionEnd`` cannot pin a worktree forever. Missing or unreadable activity
evidence is never proof of expiry: those verdicts are ``None``.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from . import obligations, tracking

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
        except OSError:
            continue
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
    record: object, claim: tracking.ResourceClaim, *, now: float | None = None,
) -> bool | None:
    """Tri-state lease verdict for a session claim: True only on stale evidence."""
    if claim.kind != "session":
        return None
    parsed = tracking.parse_claim_ref(claim.ref)
    if parsed is None or not parsed.session:
        return None
    return session_expired(parsed.session, ttl_for(record, claim.state), now=now)


def idle_finalized(
    record: tracking.WorktreeRecord,
    session_ids: Iterable[str] = (),
    *,
    now: float | None = None,
) -> bool:
    """True when only expired session leases keep a finalized record live.

    Every known session (observed live ids plus live self-claims) must have
    positive expiry evidence; no known session or any unknown verdict keeps
    the conservative live classification.
    """
    if record.status != "finalized":
        return False
    states: dict[str, str] = {sid: obligations.AT_REST for sid in session_ids if sid}
    for claim in record.resources:
        if claim.kind != "session" or not claim.is_live:
            continue
        parsed = tracking.parse_claim_ref(claim.ref)
        if parsed is None or not parsed.session:
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
    expired. ``keep`` preserves non-session activity (hosted sessions,
    workers). Any probe failure leaves the path active.
    """
    from . import sessions

    observed: dict[str, set[str]] = {}
    for path, sids in active_sessions.items():
        observed.setdefault(sessions._normalize_path(path), set()).update(sids or ())
    for rec in records:
        if rec.status != "finalized" or not rec.worktree_path:
            continue
        norm = sessions._normalize_path(rec.worktree_path)
        if norm not in active:
            continue
        try:
            if not keep(rec) and idle_finalized(rec, observed.get(norm, ())):
                active.discard(norm)
        except Exception:
            continue
