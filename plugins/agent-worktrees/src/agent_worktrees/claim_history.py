"""Append-only, timestamped ownership-history ledger for a claimed resource.

Plan Phase 3b of ``efforts/active/worktree-claims-transitive-finalization``
(aperture-labs) -- **partial slice**, matching the effort's own precedent
for splitting an immediately-actionable narrow piece off a larger item (see
that effort's split-out ``dampener-review-session-audit-trail`` sibling).

Current-owner claim state (``ResourceClaim.state``) only ever answers "who
holds this NOW" -- a single PR can be picked up by multiple
worktrees/sessions over its life (errors, redrives, rebinds), and nothing
preserves *who else ever touched it, in what order, when*. This module is
that durable, append-only record, scoped today to ``pr``-kind claims (the
Plan's own "starting with pr-kind" instruction) and fed only from the three
centralized single-worktree claim-lifecycle write verbs in
``tracking_claim_write.py`` (add/release/settle).

**Explicitly NOT yet covered by this slice** (tracked as the Plan's
remaining Phase 3b work, not silently dropped):

- Claim-handoff bundle transitions (``claim_handoffs.py``'s offer/accept/
  decline/cancel) -- an explicit, deliberate hand-off between worktrees is
  real reassignment history this ledger should eventually include, but
  wiring it in means touching that module's own intricate locked state
  machine, deferred to keep this slice's blast radius to the three simple,
  already-centralized write verbs above.
- **Implicit** reassignment: an agent-dispatch task redrive/reassignment
  after an error, or an agent-bridge session rebind to a worktree, both
  change "who is actually working this PR right now" without ever calling
  any of this plugin's own claim verbs -- feeding those in requires
  coordinating with those two plugins' own event/audit trails rather than
  duplicating them blind, and is unstarted.
- Remote-mirroring for converged repos (today this is a single machine-
  local file, same posture ``claim_handoffs.py`` itself started from).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from . import config as cfg

#: Claim kinds this ledger records. Scoped to the Plan's own "starting with
#: pr-kind" instruction -- any other kind is a silent no-op in
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

    Best-effort and silently a no-op for any ``kind`` outside
    :data:`SUPPORTED_KINDS` or on any write failure (disk full,
    permissions, ...) -- a diagnostic/history record must never perturb
    the claim-lifecycle operation it observes, mirroring
    ``activity.log_event``'s own never-raises contract.
    """
    if kind not in SUPPORTED_KINDS:
        return
    try:
        path = history_path()
        path.parent.mkdir(parents=True, exist_ok=True)
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
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=True) + "\n")
    except Exception:
        pass


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
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    entry = json.loads(raw)
                except Exception:
                    continue
                if entry.get("ref") == ref:
                    out.append(entry)
    except OSError:
        return out
    return out
