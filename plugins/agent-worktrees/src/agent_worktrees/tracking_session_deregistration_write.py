"""``session_deregister`` verb.

``agent-worktrees-authoritative-daemon`` effort, Phase 3 -- the eighth
migrated call-site cluster, and the second taken from a hot hook path
(``register_session``, PR #4159, was the first): ``deregister_session``
itself (previously inline) -- the symmetric sessionEnd counterpart to
``register_session``'s own sessionStart transaction. Much simpler than its
sibling: a single find-the-matching-entry loop, no handoff-token/candidate-
token branching, no return value beyond acknowledgement.

Latency and rolling-upgrade safety are the same checklist here as for
``register_session`` (see that migration's own PR #4159 review rounds) --
not something to rediscover per cluster:

- **Latency**: ``deregister_session``'s own dispatch call passes
  ``boot_wait_s=0`` (never spin-wait for a cold daemon boot -- a
  sessionEnd hook blocks the interactive session's own teardown exactly
  like a sessionStart hook blocks its launch) and a short request
  deadline bounding even a reachable-but-stalled daemon.
- **Rolling-upgrade safety**: already covered automatically by
  ``tracking_write.py``'s own capability-aware endpoint selection
  (``rendezvous_fields``/``endpoint_from_rendezvous``'s ``verb=`` check,
  landed in PR #4159) -- no call-site-specific work needed for this half.

``stop_fsmonitor_daemon`` (a ``git fsmonitor--daemon stop`` subprocess,
best-effort with its own 5s timeout) runs INSIDE this same
``_RecordLock``, exactly matching the pre-migration transaction -- a
first attempt at this migration moved it outside the lock as a claimed
scope-discipline improvement (``_RecordLock``'s own docstring discourages
holding it across git I/O), but PR #4238 review correctly found that the
in-lock ordering is load-bearing, not incidental: holding the SAME lock
``register_session`` needs is what prevents a concurrent session start
from adding a new open session in the window between this transaction's
"no open sessions" check and the actual stop -- without it, a session
that starts moments after this one ends could have its own fsmonitor
killed out from under it. Kept in-lock, matching old behavior exactly.
"""

from __future__ import annotations

from pathlib import Path

from . import tracking, tracking_write
from .tracking_session_registry import (
    _end_session_activation,
    _record_has_open_session,
    stop_fsmonitor_daemon,
)


def apply_session_deregister(args: dict) -> dict:
    """Registered as the ``session_deregister`` verb. Mirrors the former
    ``tracking_session_registry.deregister_session`` transaction exactly
    -- same lock, same mutation order, same no-op conditions, including
    ``stop_fsmonitor_daemon`` running inside the lock (see this module's
    own docstring for why that's load-bearing, not incidental)."""
    yaml_path = Path(args["yaml_path"])
    session_id = args["session_id"]
    ended_at = args.get("ended_at")
    source = args.get("source", "hook")
    recorded_at = args.get("recorded_at")

    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        if record.sessions is None:
            return {"ok": True}
        for entry in record.sessions:
            if entry.session_id != session_id:
                continue
            changed = _end_session_activation(
                entry,
                event_at=ended_at or tracking._now_iso(),
                recorded_at=recorded_at or tracking._now_iso(),
                source=source,
            )
            if changed:
                tracking._next_lifecycle_revision(record, session_id)
                self_session_ref = tracking.format_claim_ref(
                    record.machine, record.repo, record.worktree_id,
                    session=session_id,
                )
                tracking.release_resource_claim(record, self_session_ref, save=False)
                tracking.save_record(record, yaml_path)
                if not _record_has_open_session(record):
                    stop_fsmonitor_daemon(record.worktree_path)
            break
    return {"ok": True}


tracking_write.register_verb("session_deregister", apply_session_deregister)
