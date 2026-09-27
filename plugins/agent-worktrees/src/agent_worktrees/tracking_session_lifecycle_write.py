"""``session_conclude``/``session_link_succession`` verbs.

``agent-worktrees-authoritative-daemon`` effort, Phase 3 -- the fourth
migrated call-site cluster: the two dedicated ground-layer session-
lifecycle write commands (``conclude-session``/``link-succession``,
previously inline in ``session_tracking_cli.py``). Both are clean,
single-worktree, single-lock transactions with no ``activity.log_event``
and no ambient-context reads -- the simplest cluster yet, deliberately
picked over the same module's (``tracking_lifecycle.py``) riskier call
sites still embedded in bigger orchestrated flows:

- ``handoff_cutover.py``'s confirmed-retire repairs
  (``_conclude_retired_predecessor``/``_settle_predecessor_session_claim``)
  -- best-effort, silently-swallowed-exception call sites reachable from
  the live handoff-cutover choreography, deferred pending more care.
- ``terminal_conclusion.py``'s ``_save_session_conclusion`` -- one step
  inside the disposable-worktree conclusion cascade's own single lock, not
  a standalone transaction of its own.
- ``register_session``'s own internal ``link_handoff`` call
  (``tracking_session_registry.py``) -- embedded in the sessionStart-hook-
  critical path, the same risk class the effort's original guidance told
  this migration to avoid for an early verb.

See the effort README's own Journal for the full survey.

Both commands are "project-agnostic" (``_find_tracking_file`` searches
every project, since a higher-layer caller's CWD is unrelated to the
target worktree) -- the resolved ``yaml_path`` is already correct
regardless of ambient project, so unlike ``status_disposition_write``
there is no cross-project scoping concern to thread through here either.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

from . import tracking, tracking_write


def _conclude_session_result(record, raw_worktree_id: str, session_id: str) -> dict:
    entry = record.session_entry(session_id)
    return {
        "worktree_id": record.worktree_id or raw_worktree_id,
        "session": session_id,
        "state": entry.state if entry is not None else None,
        "head_session": record.resolved_head_session,
        "head_revision": record.head_revision,
        "pending_handoffs": [dataclasses.asdict(h) for h in record.pending_handoffs],
    }


def apply_session_conclude(args: dict) -> dict:
    """Registered as the ``session_conclude`` verb. Mirrors the former
    ``session_tracking_cli.cmd_conclude_session`` transaction exactly
    (including its post-save reload, kept for behavior parity). Returns
    ``{"error": "lifecycle", "message": ...}`` for a
    ``tracking.SessionLifecycleError`` rejection (an unknown session or
    invalid state), never raising."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    session_id = args["session_id"]
    state = args.get("state", "handed-off")
    handoff_token = args.get("handoff_token")

    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        try:
            tracking.conclude_session(
                record, session_id, state=state, handoff_token=handoff_token, save=False,
            )
        except tracking.SessionLifecycleError as exc:
            return {"error": "lifecycle", "message": str(exc)}
        tracking.save_record(record, yaml_path)

    record = tracking.load_record(yaml_path)
    return {"ok": True, **_conclude_session_result(record, worktree_id, session_id)}


def apply_session_link_succession(args: dict) -> dict:
    """Registered as the ``session_link_succession`` verb. Mirrors the
    former ``session_tracking_cli.cmd_link_succession`` transaction
    exactly (including its post-save reload, kept for behavior parity)."""
    worktree_id = args["worktree_id"]
    yaml_path = Path(args["yaml_path"])
    predecessor_id = args["predecessor"]
    successor_id = args["successor"]
    predecessor_state = args.get("predecessor_state", "handed-off")
    handoff_token = args.get("handoff_token")

    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        try:
            tracking.link_succession(
                record,
                predecessor_id,
                successor_id,
                predecessor_state=predecessor_state,
                handoff_token=handoff_token,
                save=False,
            )
        except tracking.SessionLifecycleError as exc:
            return {"error": "lifecycle", "message": str(exc)}
        tracking.save_record(record, yaml_path)

    record = tracking.load_record(yaml_path)
    pred = record.session_entry(predecessor_id)
    return {
        "ok": True,
        "worktree_id": record.worktree_id or worktree_id,
        "predecessor": predecessor_id,
        "successor": successor_id,
        "predecessor_state": pred.state if pred is not None else None,
        "head_session": record.resolved_head_session,
        "head_revision": record.head_revision,
    }


tracking_write.register_verb("session_conclude", apply_session_conclude)
tracking_write.register_verb("session_link_succession", apply_session_link_succession)
