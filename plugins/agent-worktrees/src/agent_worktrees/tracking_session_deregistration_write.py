"""``session_deregister`` verb.

Version 3 matches qualified End observations to their original activation.
Historical facts never retire a replacement's claim or fsmonitor. Weaker or
ambiguous input cannot close a source-bound interval; legacy records retain
their original no-source semantics.

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

import logging
from pathlib import Path

from . import tracking, tracking_write
from .execution_spaces import ExecutionSpaceError, require_project_record_mutation
from .tracking_session_activations import activation_token, source_end_authoritative
from .tracking_session_registry import (
    _end_session_activation,
    _record_has_open_session,
    stop_fsmonitor_daemon,
)


def _unconfirmed(session_id: str, reason: str) -> dict:
    logging.getLogger(__name__).warning(
        "Session end source is unconfirmed for %s: %s", session_id, reason,
    )
    return {"ok": True, "lifecycle": "unconfirmed", "reason": reason}


def apply_session_deregister(args: dict) -> dict:
    """Apply a reasoned End under the registration lock, preserving replacements.

    ``stop_fsmonitor_daemon`` remains in-lock for current-interval closure;
    historical and unconfirmed observations never reach that side effect.
    """
    yaml_path = Path(args["yaml_path"])
    session_id = args["session_id"]
    ended_at = args.get("ended_at")
    source = args.get("source", "hook")
    recorded_at = args.get("recorded_at")
    try:
        source_identity = activation_token(
            args.get("source_identity"), field="source_identity",
        )
        end_reason = activation_token(
            args.get("end_reason"), field="end_reason", limit=64,
        )
    except ValueError as exc:
        logging.getLogger(__name__).warning("Invalid session end evidence: %s", exc)
        return {"error": "lifecycle", "message": str(exc)}

    try:
        with tracking._RecordLock(yaml_path, require_sidecar=True):
            record = tracking.load_record(yaml_path)
            try:
                require_project_record_mutation(record)
            except ExecutionSpaceError as exc:
                logging.getLogger(__name__).warning(
                    "Rejecting session deregistration for %s: %s", record.worktree_id, exc,
                )
                return {"error": "execution_space", "message": str(exc)}
            for entry in record.sessions or []:
                if entry.session_id != session_id:
                    continue
                matches = [
                    item for item in entry.activations
                    if item.source_identity == source_identity
                ] if source_identity is not None else []
                if (
                    source_identity is not None and (
                        len(matches) != 1 or matches[0].source_ambiguous
                        or not source_end_authoritative(entry, source_identity)
                    )
                    or source_identity is None and (
                        entry.pruned_sources or entry.source_history_saturated
                        or any(item.source_identity is not None for item in entry.activations)
                    )
                ):
                    return _unconfirmed(session_id, "source-ambiguous")
                latest = max(entry.activations, key=lambda item: item.ordinal, default=None)
                retires_current = source_identity is None or matches[0] is latest
                changed = _end_session_activation(
                    entry,
                    event_at=ended_at or tracking._now_iso(),
                    recorded_at=recorded_at or tracking._now_iso(),
                    source=source,
                    source_identity=source_identity,
                    end_reason=end_reason,
                )
                if changed:
                    tracking._next_lifecycle_revision(record, session_id)
                    if retires_current:
                        self_session_ref = tracking.format_claim_ref(
                            record.execution_machine, record.repo, record.worktree_id,
                            session=session_id,
                        )
                        tracking.release_resource_claim(record, self_session_ref, save=False)
                    tracking.save_record(record, yaml_path)
                    if retires_current and not _record_has_open_session(record):
                        stop_fsmonitor_daemon(record.worktree_path)
                if source_identity is not None:
                    return {
                        "ok": True,
                        "lifecycle": (
                            "duplicate" if not changed
                            else "ended" if retires_current else "historical"
                        ),
                        "activation": {
                            "ordinal": matches[0].ordinal,
                            "source_identity": source_identity,
                        },
                    }
                break
    except TimeoutError as exc:
        logging.getLogger(__name__).warning(
            "Deregistration contended for %s: %s", yaml_path, exc,
        )
        return {"error": "execution_space", "message": f"contended by another process: {exc}"}
    return (
        _unconfirmed(session_id, "session-untracked")
        if source_identity is not None else {"ok": True}
    )


tracking_write.register_verb("session_deregister", apply_session_deregister, version=3)
