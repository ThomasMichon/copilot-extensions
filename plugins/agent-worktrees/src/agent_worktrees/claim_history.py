"""Append-only, timestamped ownership-history ledger for a claimed resource.

Current-owner claim state (``ResourceClaim.state``) only ever answers "who
holds this NOW" -- a single PR can be picked up by multiple
worktrees/sessions over its life (errors, redrives, rebinds), and nothing
preserves *who else ever touched it, in what order, when*. This module is
that durable, append-only record, scoped today to ``pr``-kind claims and
fed from every known persisted mutation path for a ``pr``-kind claim:
``tracking_claim_write.py``'s three single-worktree claim verbs
(add/release/settle), ``pr_ops.py``'s own direct create/merge-driven
claim/release calls, ``finalize.py``'s bulk release-on-finalize, and
``claims_cli.py``'s ``sweep --apply``/``reconcile-at-rest --apply`` batch
release paths. Every call site records history only AFTER its own save is
durably confirmed -- never before, or a save failure could leave a false
event for a mutation that never actually persisted.

``worktree run``'s own PR-claim persistence path (``worktree_ops_cli.py``)
is also wired (worktree-claims-transitive-finalization Phase 3b,
2026-10-03): tracing its save semantics found **two** distinct append+save
sites for a produced ``pr``-kind claim -- ``cmd_run``'s own
``_finish_pending`` closure (the actual live site: it replaces the
pending-``workdir`` placeholder with the real claim once the inner command
returns) and ``_journal_run_claim`` (a separately-tested pure helper not
currently called from ``cmd_run``'s own live flow, but sharing the exact
same append+save shape, so left wired for parity rather than silently
missing the ledger if it's ever reconnected or reused). Both record an
``event="claimed"`` entry only after their own save is durably confirmed.

**Explicitly NOT yet covered by this slice** (tracked as a remaining
follow-up, not silently dropped):

- Claim-handoff bundle transitions (``claim_handoffs.py``'s offer/accept/
  decline/cancel) -- an explicit, deliberate hand-off between worktrees is
  real reassignment history this ledger should eventually include, but
  wiring it in means touching that module's own intricate locked state
  machine, deferred to keep this slice's blast radius to the already-
  identified direct mutation call sites above.
- **Implicit** reassignment: an agent-dispatch task redrive/reassignment
  after an error, or an agent-bridge session rebind to a worktree, both
  change "who is actually working this PR right now" without ever calling
  any of this plugin's own claim verbs. The agent-bridge session-rebind
  half is wired (worktree-claims-transitive-finalization Phase 3b,
  2026-10-02): every context-handoff cutover
  (``tracking_lifecycle.link_handoff()``) now feeds an
  ``event="reassigned"`` entry for every still-ACTIVE ``pr``-kind claim the
  worktree holds, right alongside that function's own existing
  predecessor/successor audit trail (``record.handoffs``) -- see
  ``tracking_lifecycle.record_pr_claims_reassigned()``. The agent-dispatch
  task-redrive half was investigated and found to have no live code path
  to hook today (``dispatch_attempt`` is write-once at worktree creation;
  the one related check, ``worktree_attribution.foreign_task_id``, is a
  defensive reject of a stale carried worktree id, never an active
  reassignment) -- still unstarted, and tracked only in the consuming
  deployment's own private effort tracker, not here.
- Remote-mirroring for converged repos (today this is a single machine-
  local file, same posture ``claim_handoffs.py`` itself started from).

**Concurrent writers.** Multiple independent processes can append a claim
event at once (the CLI, a resident daemon dispatch, ``pr_ops``'s own
reconcile path). A plain ``O_APPEND`` write is not a documented
cross-platform atomicity guarantee, so every append here takes the same
real cross-process advisory lock ``handoff_trace.py`` already established
for this exact problem (``fcntl``/``msvcrt``), rather than relying on
filesystem append semantics alone.

**Ordering is best-effort, not a total order.** Every write-path caller
appends immediately after confirming ITS OWN record save succeeded (never
batched/deferred across multiple records), and the append lock itself
guarantees no two appends interleave mid-line. What is NOT guaranteed:
two genuinely concurrent transitions on the SAME ref, saved under two
different record locks milliseconds apart, could still append in an order
that does not exactly match which save committed to disk first -- there is
no single lock spanning "save a record" and "append its history entry"
across every caller. Treat this ledger as "every real transition gets
recorded, each one truthfully after its own save succeeded" rather than
"a strictly serialized timeline safe to diff for exact interleavings."
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg
from . import handoff_trace

log = logging.getLogger("agent-worktrees")

#: Count of record_event() calls that failed to write, for observability
#: without breaking the never-raise contract -- mirrors
#: ``activity.log_event_failure_count()``.
_write_failures = 0


def write_failure_count() -> int:
    """Return how many :func:`record_event` calls have failed to write in
    this process -- a failed append is never raised, but must still be
    detectable rather than silently and permanently lost."""
    return _write_failures


#: Claim kinds this ledger records. Any other kind is a silent no-op in
#: :func:`record_event`, so callers never need to pre-filter kind
#: themselves before calling it unconditionally from a claim write path.
SUPPORTED_KINDS = frozenset({"pr"})


def history_path() -> Path:
    """Return the machine-local, append-only claim-history ledger path.

    Deliberately separate from ``activity.jsonl`` -- that log is a rolling,
    size-pruned diagnostic trace (``activity.RETENTION_DAYS``), not a
    durable ownership record; this ledger is never pruned.
    """
    return cfg.install_dir() / "logs" / "claim-history.jsonl"


def record_event(
    *,
    kind: str,
    ref: str,
    worktree_id: str,
    machine: str,
    event: str,
    session_id: str | None = None,
    note: str = "",
) -> None:
    """Append one ownership-history entry for a claimed resource.

    ``session_id`` defaults to :func:`current_session_id` when omitted, so
    most call sites never pass it explicitly.

    Best-effort and silently a no-op for any ``kind`` outside
    :data:`SUPPORTED_KINDS` or on any write failure (disk full,
    permissions, ...) -- a diagnostic/history record must never perturb
    the claim-lifecycle operation it observes, mirroring
    ``activity.log_event``'s own never-raises contract. A write failure is
    never silently *invisible* though -- it bumps :func:`write_failure_count`
    and logs a debug line, exactly like ``activity.log_event_failure_count``.
    """
    if kind not in SUPPORTED_KINDS:
        return
    if session_id is None:
        session_id = current_session_id()
    try:
        path = history_path()
        lock_path = path.with_suffix(path.suffix + ".lock")
        entry: dict[str, object] = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "kind": kind,
            "ref": ref,
            "worktree_id": worktree_id,
            "machine": machine,
            "session_id": session_id,
            "event": event,
        }
        if note:
            entry["note"] = note
        line = json.dumps(entry, ensure_ascii=True)
        with handoff_trace._append_lock(lock_path):
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except Exception as exc:
        global _write_failures
        _write_failures += 1
        log.debug("claim_history.record_event(%r, ref=%r) failed to write: %s",
                  event, ref, exc)


def record_claim_released(claim, *, worktree_id: str, machine: str, note: str = "") -> None:
    """Convenience: record_event("released") for a duck-typed claim object
    (``.kind``/``.ref``) -- shrinks a batch-release call site (e.g.
    ``finalize.py``'s release-all-resources loop) to one line."""
    record_event(
        kind=claim.kind, ref=claim.ref, worktree_id=worktree_id, machine=machine,
        event="released", note=note,
    )


def record_pr_event(ref: str, *, worktree_id: str, machine: str, event: str, note: str = "") -> None:
    """Convenience: record_event(kind="pr", ...) for the common ``pr_ops.py``
    call-site shape, shrinking three call sites to one line each."""
    record_event(
        kind="pr", ref=ref, worktree_id=worktree_id, machine=machine,
        event=event, note=note,
    )


def current_session_id() -> str | None:
    """Best-effort invoking-session id, the same env var ``finalize.py``'s
    own session-claim settlement reads. When a write is actually dispatched
    through the coalescing daemon rather than run in-process, this reads
    the DAEMON's environment, not the originating CLI call's -- the same
    known limitation ``finalize._settle_current_session_claim`` documents
    for its own env read; a wrong/missing session_id here degrades to a
    thinner history entry, never a failure.
    """
    return os.environ.get("COPILOT_AGENT_SESSION_ID") or None


def history_for_ref(ref: str) -> list[dict]:
    """Return every recorded event for ``ref``, oldest first.

    Never raises -- a missing file, an unreadable file, or an unparseable
    line is simply skipped/absent rather than surfaced as an error.
    """
    path = history_path()
    out: list[dict] = []
    if not path.exists():
        return out
    try:
        # errors="replace" (matching handoff_trace.read_trace) so one
        # damaged/invalid-UTF-8 byte downgrades to an unparseable (and thus
        # skipped) line instead of raising UnicodeDecodeError and aborting
        # the whole read -- later valid lines remain readable.
        with open(path, encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                if entry.get("ref") == ref:
                    out.append(entry)
    except OSError:
        return out
    return out
