"""Sessions started in a project's main (anchor) checkout, recorded on the ``@anchor`` ledger.

The ``@anchor`` tracking record already journals claims made from the main
checkout. Copilot sessions started there are recorded on the same ledger so
``list-sessions``/``head-session --worktree @anchor`` can return them, but the
anchor stays a ledger, never a worktree: it is skipped by ``list_records`` by
default, and finalize, push-changes, and cleanup refuse it.
"""

from __future__ import annotations

from pathlib import Path

from . import activity, output, tracking
from . import config as cfg

NOT_A_WORKTREE = "the project's main checkout is not a removable worktree"


def refuse(worktree_id: str, verb: str) -> bool:
    """Return True (after reporting why) when ``worktree_id`` is the ``@anchor`` ledger."""
    if worktree_id != tracking.ANCHOR_ID:
        return False
    output.err(f"Cannot {verb} {worktree_id}: {NOT_A_WORKTREE}.")
    return True


def reap_refusal() -> dict:
    """The ``reap_one`` result for ``@anchor``: skipped, never removed."""
    return {"ok": False, "removed": False, "skipped": True, "reason": NOT_A_WORKTREE}


def register_session(
    session_id: str,
    anchor: Path,
    *,
    pid: int | None,
    pane_id: str | None,
    event_at: str | None,
    source: str,
    launch_id: str | None = None,
) -> bool:
    """Record a session started in the main checkout on the ``@anchor`` ledger.

    No status updater, monitor session, profile assignment, or handoff linkage
    is attached. Best-effort -- a failure here never fails the hook.
    """
    try:
        config = cfg.load_config()
        tracking.load_or_create_anchor_record(
            str(anchor), config.repo_name, config.machine, config.platform, cfg.tracking_dir()
        )
        tracking.register_session(
            tracking.ANCHOR_ID,
            session_id,
            pid=pid,
            pane_id=pane_id,
            started_at=event_at,
            source=source,
        )
    except Exception as e:
        output.err(f"Could not record main-checkout session: {e}")
        return False
    try:
        from . import handoff_diagnostics

        handoff_diagnostics.stamp_session_state_worktree_binding(
            session_id,
            tracking.ANCHOR_ID,
            worktree_dir=str(anchor),
            machine=config.machine,
        )
    except Exception:
        pass
    activity.log_event(
        "session_started",
        worktree_id=tracking.ANCHOR_ID,
        session_id=session_id,
        launch_id=launch_id,
    )
    return True
