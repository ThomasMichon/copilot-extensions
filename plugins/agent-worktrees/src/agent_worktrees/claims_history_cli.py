"""``agent-worktrees claims history`` -- Plan Phase 3b (partial) of the
``worktree-claims-transitive-finalization`` tracking effort: render the
durable, ordered ownership history :mod:`claim_history` records for one
claimed resource (today: ``pr``-kind only).

See :mod:`claim_history`'s own module docstring for the full scope note
(what this slice covers and what remains open).
"""

from __future__ import annotations

import argparse

from . import claim_history, output


def _event_identity(e: dict) -> tuple:
    return (e.get("ledger_id"), e.get("seq"))


def _merge_events(local: list[dict], remote: list[dict]) -> list[dict]:
    """Merge local + mirrored events for one ref into a single ordered
    list, deduplicated ONLY by each event's own durable ``(ledger_id,
    seq)`` identity -- never by matching display fields (``ts``,
    ``event``, ``worktree_id``, ...). Two genuinely distinct events (a
    claim released and re-claimed within the same second, or one event
    from a different ledger incarnation entirely) can share identical
    display fields; treating that as a match would silently drop a real
    event. ``local`` is expected pre-stamped with its own identity (see
    :func:`claim_history_mirror.local_identities_for_ref`) -- a local
    event with no ``ledger_id`` (this machine has never mirrored anything)
    can never be confirmed as any remote event's own mirror, so every
    remote event is kept rather than guessed away. Preserves ``remote``'s
    own original relative order among any events it keeps (a stable sort
    by ``ts`` only re-orders across the two sources, by genuine time, not
    within either one).
    """
    local_ids = {_event_identity(e) for e in local if e.get("ledger_id") is not None}
    merged = list(local)
    for e in remote:
        if _event_identity(e) in local_ids:
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

    if getattr(args, "remote", False):
        from . import claim_history_mirror
        events = claim_history_mirror.local_identities_for_ref("pr", ref)
        remote_events = claim_history_mirror.fetch_remote_history(ref)
        events = _merge_events(events, remote_events)
    else:
        events = claim_history.history_for_ref(ref)

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
