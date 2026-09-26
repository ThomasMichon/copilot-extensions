"""``status_disposition_write`` verb -- Phase 3's first migrated call site.

``agent-worktrees-authoritative-daemon`` effort, Phase 3. Wraps
``__main__._cmd_status_write``'s whole guarded transaction (load ->
terminal/effort-bound guards -> conditional reactivation -> ``set_disposition``
-> ``save_record`` -> once-per-session ``status_reported`` activity event, all
under one ``tracking._RecordLock``) as a single :mod:`tracking_write` verb,
per that module's own "verb maps to a whole transaction, not a bare setter"
granularity note.

Picked as the effort's first real call site because it is the "narrower,
lower-traffic disposition-assertion path" the effort README explicitly names
as a safer first pick than ``register_session`` (sessionStart-hook-critical)
or ``mark_resumed`` (embedded in a bigger resume flow) -- an operator-invoked
``status`` write, not a hot facility path.

**Env vars are read by the caller, never by this verb.** The call site reads
``COPILOT_AGENT_SESSION_ID`` from its own process environment and passes it
in as ``session_id`` -- reading it here would read the *daemon's* environment
when this verb runs via the resident daemon, not the CLI invocation's, which
would silently break the once-per-session ``status_reported`` bookkeeping.

**The disposition-history sidecar is explicitly scoped, never ambient.**
``tracking.set_disposition`` is passed ``tracking_path=yaml_path.parent``
(this verb's own resolved record directory) rather than relying on its
default ``cfg.tracking_dir()`` fallback -- the daemon process's own ambient
active project need not match the project the dispatching CLI call actually
targets, and an ambient-scoped write would silently corrupt a *different*
project's disposition-history sidecar (2026-09-26 PR review finding).
"""

from __future__ import annotations

from pathlib import Path

from . import activity, tracking, tracking_write


def apply_status_disposition(args: dict) -> dict:
    """Registered as the ``status_disposition_write`` verb (see module
    docstring). ``args`` carries everything the transaction needs, computed
    by the call site (worktree id + record path already resolved, session id
    already read from the caller's own environment). Returns a JSON-safe
    result the call site uses to render its own message and error paths --
    never raises for an expected guard rejection (terminal/effort-bound),
    only for a genuinely unexpected failure.
    """
    worktree_id = args["worktree_id"]
    # `args` crosses the wire as JSON when a daemon serves the request, so a
    # `Path` sent by the call site arrives here as a plain str either way
    # (in-process fallback or daemon round trip) -- always coerce back to a
    # `Path` before handing it to `tracking._RecordLock`/`load_record`, which
    # both call `Path`-only methods (`with_suffix` etc.).
    yaml_path = Path(args["yaml_path"])
    summary = args.get("summary")
    title = args.get("title")
    follow_up = args.get("follow_up")
    session_id = args.get("session_id")

    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        if record.kind in tracking.MANAGED_KINDS and record.status in {
            "complete", "completed", "finalized",
        }:
            return {"error": "terminal_managed", "worktree_id": worktree_id}
        if follow_up is False and record.active_effort is not None:
            return {"error": "effort_bound"}
        if follow_up is True and record.status == "finalized":
            tracking.update_status(record, "active", save=False)
        tracking.set_disposition(
            record,
            summary=summary,
            title=title,
            follow_up=follow_up,
            session_id=session_id,
            save=False,
            tracking_path=yaml_path.parent,
        )
        tracking.save_record(record)
        # Stage 5 (status_reported): once per session_id, held under the
        # same RecordLock as the write above so two concurrent writers can't
        # both observe "no prior event" and double-emit -- see
        # ``_cmd_status_write``'s own comment (unchanged migration target).
        if session_id and not any(
            e.get("session_id") == session_id
            for e in activity.read_events(worktree_id=worktree_id, event="status_reported")
        ):
            activity.log_event("status_reported", worktree_id=worktree_id, session_id=session_id)

    return {
        "ok": True,
        "follow_up": record.follow_up,
        "title": record.title,
        "summary": record.summary,
    }


tracking_write.register_verb("status_disposition_write", apply_status_disposition)
