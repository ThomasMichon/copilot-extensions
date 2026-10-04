"""``agent-worktrees claims history`` -- Plan Phase 3b (partial) of the
``worktree-claims-transitive-finalization`` tracking effort: render the
durable, ordered ownership history :mod:`claim_history` records for one
claimed resource (today: ``pr``-kind only).

See :mod:`claim_history`'s own module docstring for the full scope note
(what this slice covers and what remains open).
"""

from __future__ import annotations

import argparse
from collections import Counter

from . import claim_history, output


def _event_key(e: dict) -> tuple:
    return (
        e.get("ts"), e.get("event"), e.get("worktree_id"),
        e.get("machine"), e.get("session_id"), e.get("note"),
    )


def _merge_events(local: list[dict], remote: list[dict]) -> list[dict]:
    """Merge local + mirrored events for one ref into a single ordered list,
    counting (not set-deduplicating) by the full (ts, event, worktree_id,
    machine, session_id, note) tuple. ``record_event`` timestamps only to
    the second, so two genuinely distinct transitions by the same
    worktree/session within one second can share an identical key -- a
    plain set-based dedup across the combined list would silently drop one
    of those REAL local events whenever its mirrored copy also happened to
    be present, not just the redundant mirrored copy of an event local
    already has. Instead: keep every local event as-is (never deduplicated
    against itself), and walk ``remote`` in ITS OWN original order,
    consuming one "already accounted for by local" credit per matching key
    before appending a genuinely extra remote-only copy -- grouping
    same-key remote events together (as an earlier revision did) would
    silently reorder a real same-second remote sequence (e.g. a
    ``claimed, released, claimed`` trio becoming ``claimed, claimed,
    released``) even with no local history to merge against at all. The
    final sort by ``ts`` is stable, so ties preserve local-before-extras
    and each extra's own relative order among themselves.
    """
    local_counts = Counter(_event_key(e) for e in local)
    merged = list(local)
    remaining = Counter(local_counts)
    for e in remote:
        key = _event_key(e)
        if remaining[key] > 0:
            remaining[key] -= 1
            continue
        merged.append(e)
    merged.sort(key=lambda e: str(e.get("ts", "")))
    return merged


def cmd_claims_history(
    args: argparse.Namespace,
    ref: str | None,
    *,
    json_error,
    json_output,
) -> int:
    """``claims history <ref>``: the ordered, timestamped list of every
    recorded claim/release/settle event for ``ref``. Pure read-only
    rendering over :func:`claim_history.history_for_ref`, optionally merged
    with ``--remote``'s mirrored events (worktree-claims-transitive-finalization
    Phase 3b's remote-mirroring item) -- see :mod:`claim_history_mirror`.
    """
    if not ref:
        msg = "claims history: missing <ref>. Usage: claims history <ref>"
        if args.json:
            return json_error(msg, 2)
        output.err(msg)
        return 2

    events = claim_history.history_for_ref(ref)
    remote_events: list[dict] = []
    if getattr(args, "remote", False):
        from . import claim_history_mirror
        remote_events = claim_history_mirror.fetch_remote_history(ref)
        events = _merge_events(events, remote_events)

    if args.json:
        json_output({"ref": ref, "events": events})
        return 0

    print(f"Ownership history for {ref}:")
    if not events:
        print(
            "  (no covered transition recorded -- this does NOT prove "
            "nothing happened: this ref's kind may not be tracked yet "
            "(see claim_history.SUPPORTED_KINDS), or the actual event may "
            "have gone through a path this ledger doesn't cover yet -- see "
            "claim_history.py's own module docstring for the current scope)"
        )
        return 0
    for e in events:
        session = f" session={e['session_id']}" if e.get("session_id") else ""
        note = f"  -- {e['note']}" if e.get("note") else ""
        print(
            f"  - {e.get('ts', '?')}  {e.get('event', '?')}  "
            f"worktree={e.get('worktree_id', '?')} machine={e.get('machine', '?')}"
            f"{session}{note}"
        )
    return 0
