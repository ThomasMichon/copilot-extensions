"""Shared live-session-holder lookup for ``routes/worktrees.py``.

Extracted into its own module (``worktrees.py`` is at its grandfathered
module-size ceiling) rather than inlined there, mirroring this plugin's own
precedent (``worktree_probe.py``).
"""

from __future__ import annotations

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
