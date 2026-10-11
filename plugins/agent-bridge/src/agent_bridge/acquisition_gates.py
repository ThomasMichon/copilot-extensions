"""Existing owned lifecycle entry points respect an in-flight acquisition gate."""

from __future__ import annotations

from typing import Any


class AcquisitionGateHeld(ValueError):
    """Another acquisition owns the worktree's process-launch boundary."""


def assert_resume_allowed(db: Any, session: Any) -> None:
    if not session.target.worktree_id:
        return
    if not db.execute_read(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='acquisition_launch_gates'",
    ):
        return
    rows = db.execute_read(
        "SELECT * FROM acquisition_launch_gates WHERE worktree_id=?",
        (session.target.worktree_id,),
    )
    if not rows:
        return
    gate = rows[0]
    allowed = gate["mode"] == "acp" and (
        gate["session_id"] == session.session_id
        or (
            gate["session_id"] is None
            and session.caller_id == f"acquisition:{gate['delivery_id']}"
        )
    )
    if not allowed:
        raise AcquisitionGateHeld("another acquisition holds this worktree launch fence")
