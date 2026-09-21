"""Shared live-session-holder lookup for ``routes/worktrees.py``.

Extracted into its own module (``worktrees.py`` is at its grandfathered
module-size ceiling) rather than inlined there, mirroring this plugin's own
precedent (``worktree_probe.py``).
"""

from __future__ import annotations

import time
from typing import Any


def chosen_holder_id(holders: list[dict[str, Any]]) -> str | None:
    """The most-recently-updated live-session registration's id, or None.

    Used everywhere ``routes/worktrees.py`` returns a ``409
    live_cli_holds_worktree`` refusal, so the reported ``session_id`` is
    always the actual live CLI holder -- never a caller's own resuming
    session id, which a reclaim caller could otherwise mistake for a
    confirmed-dead holder and force through unsafely.
    """
    return max(holders, key=lambda r: r.get("updated_at") or 0)["session_id"] if holders else None


def reservation_conflict_detail(db: Any, worktree_id: str) -> dict[str, Any]:
    """Build the 409 detail for a failed ``reserve_worktree_ownership``.

    That call refuses for TWO distinct reasons: a fresh live CLI raced it
    (a genuine ``live_cli_holds_worktree`` -- a reclaim caller may
    stop-and-force through it), or another active ACP reservation already
    owns the worktree (a different conflict entirely -- forcing an
    interactive-CLI stop does nothing to it, so it must never be reported
    under the same reason a reclaim caller treats as reclaimable).
    """
    holders = db.list_fresh_live_sessions(worktree_id, now=time.time())
    if holders:
        return {
            "reason": "live_cli_holds_worktree",
            "worktree_id": worktree_id,
            "session_id": chosen_holder_id(holders),
        }
    owner = db.get_worktree_ownership(worktree_id)
    return {
        "reason": "worktree_owned_by_another_session",
        "worktree_id": worktree_id,
        "session_id": owner["session_id"] if owner else None,
    }
